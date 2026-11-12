#!/usr/bin/env python3
"""
HỆ THỐNG QUẢN LÝ LỊCH IMS - PHIÊN BẢN MOBILE TERMINAL (TUI)
Tối ưu chuyên biệt cho màn hình dọc điện thoại (độ rộng 50-60 cột ký tự).
- Header 1 dòng siêu gọn: [Badge tuần] + Ô tìm kiếm co giãn 1fr + [Đếm ca].
- Bảng DataTable 5 cột thiết yếu, vừa khít chiều ngang màn hình dọc:
  [Ngày] [Giờ] [Giáo viên] [Phòng] [XN]
- Loại bỏ STT, Mã lớp dài, Trạng thái dài để chống cuộn ngang.
- Tự động rút gọn tên phòng (P1-80, B1.1, ONL) & tên giáo viên thông minh.
- Giữ nguyên toàn bộ logic lọc (1/2/3 tuần, f GVNN, p Chờ duyệt, u Đồng bộ API, live search tiếng Việt).
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
import unicodedata

# Tự động kích hoạt / chuyển sang python trong .venv nếu chạy bằng python hệ thống chưa cài textual
BASE_DIR = Path(__file__).resolve().parent
_VENV_PYTHON = BASE_DIR / ".venv" / "bin" / "python"
if _VENV_PYTHON.exists() and sys.executable != str(_VENV_PYTHON):
    try:
        import textual  # noqa: F401
    except ImportError:
        os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.widgets import Button, DataTable, Input, Label, Static

# Đường dẫn tới các file dữ liệu và script
WEEKLY_FILE = BASE_DIR / "weekly.json"
UPDATE_SCRIPT = BASE_DIR / "update_schedule.sh"

# Bảng dịch thứ viết tắt cho Mobile
WEEKDAY_SHORT = {
    0: "T2",
    1: "T3",
    2: "T4",
    3: "T5",
    4: "T6",
    5: "T7",
    6: "CN",
}

WEEKDAY_FULL = {
    0: "Thứ 2",
    1: "Thứ 3",
    2: "Thứ 4",
    3: "Thứ 5",
    4: "Thứ 6",
    5: "Thứ 7",
    6: "Chủ Nhật",
}

# Tập tên giáo viên nước ngoài đã biết (kiểm tra không phân biệt hoa/thường)
FOREIGN_TEACHER_NAMES: set[str] = {
    "alex", "timothy", "leon", "anna", "academic faculty",
    "james", "michael", "david", "sarah", "emma", "jessica",
    "william", "george", "henry", "edward", "richard", "roel", "nick", "paul", "lesfountain",
}


def normalize_text(text: str | None) -> str:
    """Chuẩn hóa văn bản tiếng Việt sang không dấu, chữ thường để phục vụ tìm kiếm."""
    if not text:
        return ""
    text = str(text)
    text = unicodedata.normalize("NFD", text)
    text = "".join([c for c in text if not unicodedata.combining(c)])
    return text.replace("Đ", "D").replace("đ", "d").lower().strip()


def is_foreign_teacher(session: dict[str, Any]) -> bool:
    """Trả về True nếu giáo viên (hoặc giáo viên dạy thay) là người nước ngoài."""
    def _has_vietnamese_tone(name: str) -> bool:
        nfd = unicodedata.normalize("NFD", name)
        return any(unicodedata.combining(ch) for ch in nfd)

    def _check_name(name: str | None) -> bool:
        if not name:
            return False
        low = name.strip().lower()
        if any(known in low for known in FOREIGN_TEACHER_NAMES):
            return True
        if not _has_vietnamese_tone(name):
            ascii_ratio = sum(1 for c in name if ord(c) < 128) / max(len(name), 1)
            return ascii_ratio >= 0.85
        return False

    teacher_obj = session.get("teacher")
    teacher_name = (
        teacher_obj.get("name") if isinstance(teacher_obj, dict) else str(teacher_obj or "")
    )
    cover_obj = session.get("coverTeacher")
    cover_name = (
        cover_obj.get("name") if isinstance(cover_obj, dict) else str(cover_obj or "")
    )
    active_name = cover_name if cover_name else teacher_name
    return _check_name(active_name)


def get_dual_tick_status(item: dict[str, Any]) -> str | None:
    """Trích xuất trạng thái chấm công (Dual Tick) từ ca học."""
    status = (
        item.get("dual_tick_status")
        or item.get("confirmStatus")
        or item.get("attendanceStatus")
        or item.get("dualTickStatus")
    )
    if not status:
        if item.get("isConfirmed") is True or item.get("teacherConfirmed") is True:
            return "CONFIRMED"
    return status


def is_pending_session(session: dict[str, Any]) -> bool:
    """Trả về True nếu ca học chưa hoàn tất xác nhận (không phải CONFIRMED)."""
    dual_tick = get_dual_tick_status(session)
    if not dual_tick:
        return True
    st = str(dual_tick).strip().upper()
    return st not in ("CONFIRMED", "TRUE")


def format_dual_tick_status(status: Any) -> str:
    """Định dạng màu sắc trạng thái Dual Tick cho Rich/Textual."""
    if not status:
        return "[dim]- Chưa xác nhận[/dim]"
    st = str(status).strip()
    if st.upper() in ("CONFIRMED", "TRUE"):
        return "[bold green]● Confirmed (Đã duyệt)[/bold green]"
    elif "PENDING" in st.upper():
        return "[bold #87CEFA]○ Pending Teacher (Chờ GV)[/bold #87CEFA]"
    return f"[dim]{st}[/dim]"


def format_date_mobile(iso_date: str | None) -> str:
    """Định dạng ngày ngắn gọn trên mobile: T2 28/09 thay vì cả năm."""
    if not iso_date:
        return "N/A"
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        weekday = WEEKDAY_SHORT.get(dt.weekday(), "")
        return f"{weekday} {dt.strftime('%d/%m')}"
    except ValueError:
        return iso_date


def format_date_full(iso_date: str | None) -> str:
    """Định dạng ngày đầy đủ cho cửa sổ chi tiết: Thứ 2 (28/09/2026)."""
    if not iso_date:
        return "N/A"
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        weekday = WEEKDAY_FULL.get(dt.weekday(), "")
        return f"{weekday} ({dt.strftime('%d/%m/%Y')})"
    except ValueError:
        return iso_date


def shorten_room(r: str | None) -> str:
    """
    Rút gọn tên phòng học tối ưu không gian hiển thị trên mobile.
    Ví dụ:
    - ONLINE-VUNGTAU -> ONL
    - P1-80 BĐ       -> P1-80
    - P1.1 - 80BĐ    -> P1.1-80
    - B1.1-2G3       -> B1.1
    - D-2.1_2K3NTH   -> D2.1
    - A1.1-27G1      -> A1.1
    """
    if not r:
        return "-"
    s = r.strip()
    if "ONLINE" in s.upper():
        return "ONL"

    # Sửa lỗi chính tả phổ biến trong dữ liệu (ví dụ P21.-84 -> P2.1-84)
    s = re.sub(r"^P21\.", "P2.1", s)

    # Khớp định dạng phòng P kèm số cơ sở: P1-80, P1.1-80, P1-76...
    m_p = re.match(r"^(P\d+(?:\.\d+)?)\s*[-_ ]\s*(\d{2})", s, re.IGNORECASE)
    if m_p:
        return f"{m_p.group(1).upper()}-{m_p.group(2)}"

    # Khớp định dạng mã phòng có dấu chấm: B-1.1_..., D2.1-3K3, A1.1-27G1
    m_dot = re.search(r"([A-Za-z])-?(\d+\.\d+)", s)
    if m_dot:
        return f"{m_dot.group(1).upper()}{m_dot.group(2)}"

    # Khớp tiền tố chữ cái + số cơ sở: A-27G1 -> A-27
    m_dash = re.match(r"^([A-Za-z])[-_](\d{2})", s)
    if m_dash:
        return f"{m_dash.group(1).upper()}-{m_dash.group(2)}"

    # Khớp các phòng khác: PD, A, B...
    m_prefix = re.match(r"^(PD|[A-Za-z]\d?)\b", s, re.IGNORECASE)
    if m_prefix:
        return m_prefix.group(1).upper()

    return s[:6]


def format_teacher_mobile(name: str | None, max_len: int = 18) -> str:
    """
    Định dạng tên giáo viên gọn gàng, giữ nguyên họ & tên gọi (given name),
    chỉ viết tắt tên đệm nếu quá dài để không chiếm ngang màn hình.
    Ví dụ:
    - Trần Xuân Hữu           -> Trần Xuân Hữu (13 ký tự)
    - Nguyễn Thị Thanh Trang   -> Nguyễn T. T. Trang (18 ký tự)
    - Hoàng Phạm Ái Khuyên     -> Hoàng P. Á. Khuyên (18 ký tự)
    """
    if not name:
        return "Chưa rõ"
    name = name.strip()
    if len(name) <= max_len:
        return name

    parts = name.split()
    if len(parts) >= 3:
        # Viết tắt các tên đệm ở giữa: Họ + Ký tự đầu tên đệm + Tên
        middle_abbr = " ".join(f"{p[0]}." for p in parts[1:-1])
        abbr = f"{parts[0]} {middle_abbr} {parts[-1]}"
        if len(abbr) <= max_len:
            return abbr
        # Nếu vẫn dài, viết tắt thêm họ: H. P. Á. Khuyên
        abbr_family = f"{parts[0][0]}. {middle_abbr} {parts[-1]}"
        if len(abbr_family) <= max_len:
            return abbr_family

    return name[: max_len - 1] + "…"


class SessionDetailModal(ModalScreen[None]):
    """Cửa sổ Modal hiển thị đầy đủ thông tin chi tiết một buổi học trên mobile."""

    DEFAULT_CSS = """
    SessionDetailModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #modal_dialog {
        width: 96%;
        max-width: 56;
        height: auto;
        max-height: 90%;
        border: solid $primary;
        background: $surface;
        padding: 1 1;
    }

    #modal_title {
        text-align: center;
        text-style: bold;
        color: $accent;
        padding-bottom: 1;
        border-bottom: solid $primary;
        margin-bottom: 1;
    }

    #modal_content {
        margin-bottom: 1;
        overflow-y: auto;
    }

    #modal_actions {
        align: center middle;
        height: 3;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Đóng", priority=True),
        Binding("enter", "dismiss", "Đóng", priority=True),
        Binding("q", "dismiss", "Đóng", priority=True),
    ]

    def __init__(self, session_data: dict[str, Any]) -> None:
        super().__init__()
        self.session_data = session_data

    def compose(self) -> ComposeResult:
        s = self.session_data
        class_ref = s.get("classRef") or {}
        teacher_info = s.get("teacher") or {}
        cover_info = s.get("coverTeacher") or {}
        skill_info = s.get("skill") or {}

        teacher_name = (
            teacher_info.get("name")
            if isinstance(teacher_info, dict)
            else str(teacher_info or "Chưa rõ")
        )
        teacher_email = (
            teacher_info.get("email", "") if isinstance(teacher_info, dict) else ""
        )
        cover_name = (
            cover_info.get("name")
            if isinstance(cover_info, dict)
            else str(cover_info or "")
        )
        cover_email = (
            cover_info.get("email", "") if isinstance(cover_info, dict) else ""
        )

        detail_lines = [
            f"[bold cyan]Mã lớp:[/bold cyan] {class_ref.get('code', 'N/A')}",
            f"[bold cyan]Tên lớp:[/bold cyan] {class_ref.get('name') or '(Không có)'}",
            f"[bold cyan]Ngày học:[/bold cyan] {format_date_full(s.get('sessionDate'))}",
            f"[bold cyan]Thời gian:[/bold cyan] {s.get('startTime', '??:??')} - {s.get('endTime', '??:??')}",
            f"[bold cyan]Phòng học:[/bold cyan] {s.get('room', 'N/A')} [dim]({shorten_room(s.get('room'))})[/dim]",
            f"[bold cyan]GV chính:[/bold cyan] {teacher_name}" + (f" <{teacher_email}>" if teacher_email else ""),
            f"[bold cyan]Dạy thay:[/bold cyan] {cover_name + (f' <{cover_email}>' if cover_email else '') if cover_name else 'Không có'}",
            f"[bold cyan]Kỹ năng/Môn:[/bold cyan] {skill_info.get('name', 'N/A') if isinstance(skill_info, dict) else 'N/A'}",
            f"[bold cyan]Trạng thái:[/bold cyan] {s.get('status', 'N/A')}",
            f"[bold cyan]Chấm công:[/bold cyan] {format_dual_tick_status(get_dual_tick_status(s))}",
            f"[bold cyan]Google Meet:[/bold cyan] {s.get('googleMeetLink') or 'Không có'}",
            f"[bold cyan]Ghi chú:[/bold cyan] {s.get('notes') or 'Không có'}",
        ]

        with Vertical(id="modal_dialog"):
            yield Label("CHI TIẾT BUỔI HỌC", id="modal_title")
            yield Static("\n".join(detail_lines), id="modal_content")
            with Horizontal(id="modal_actions"):
                yield Button("Đóng [Esc/Enter]", variant="primary", id="close_btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close_btn":
            self.dismiss()

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "enter", "q"):
            event.stop()
            self.dismiss()


class HelpModal(ModalScreen[None]):
    """Cửa sổ hiển thị phím tắt tối ưu kích thước cho mobile."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #help_dialog {
        width: 96%;
        max-width: 52;
        height: auto;
        max-height: 90%;
        border: solid #5e81ac;
        background: #1e222b;
        padding: 1 1;
    }

    #help_title {
        text-align: center;
        text-style: bold;
        color: #88c0d0;
        padding-bottom: 1;
        border-bottom: solid #3b4252;
        margin-bottom: 1;
    }

    #help_content {
        overflow-y: auto;
    }

    #help_footer {
        text-align: center;
        color: #4c566a;
        margin-top: 1;
        border-top: solid #3b4252;
        padding-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Đóng", priority=True),
        Binding("enter", "dismiss", "Đóng", priority=True),
        Binding("question_mark", "dismiss", "Đóng", priority=True),
        Binding("q", "dismiss", "Đóng", priority=True),
    ]

    _SECTIONS = [
        (
            "TÌM KIẾM & DUYỆT",
            [
                ("/",     "Bật ô tìm kiếm nhanh"),
                ("Enter", "Xem chi tiết / Chọn dòng"),
                ("Esc",   "Về bảng / Xóa tìm kiếm"),
                ("↑ / ↓", "Di chuyển từng dòng"),
            ],
        ),
        (
            "BỘ LỌC DỮ LIỆU",
            [
                ("1 / 2", "Xem Tuần 1 / Tuần 2"),
                ("3 / a", "Xem Cả 2 tuần"),
                ("f",     "Bật / tắt lọc GV nước ngoài"),
                ("p",     "Bật / tắt lọc ca Chờ duyệt"),
            ],
        ),
        (
            "HỆ THỐNG",
            [
                ("u",     "Đồng bộ lịch mới từ API"),
                ("r",     "Tải lại file weekly.json"),
                ("?",     "Xem bảng trợ giúp này"),
                ("q",     "Thoát ứng dụng"),
            ],
        ),
    ]

    def compose(self) -> ComposeResult:
        lines: list[str] = []
        for section_title, keys in self._SECTIONS:
            lines.append(f"[bold #81a1c1]▸ {section_title}[/bold #81a1c1]")
            for key_label, desc in keys:
                lines.append(f"  [bold #ebcb8b]{key_label:<9}[/bold #ebcb8b] [#d8dee9]{desc}[/#d8dee9]")
            lines.append("")

        with Vertical(id="help_dialog"):
            yield Static("⌨  PHÍM TẮT MOBILE  ⌨", id="help_title")
            yield Static("\n".join(lines).rstrip(), id="help_content")
            yield Static("[dim]Nhấn Esc / Enter / ? để đóng[/dim]", id="help_footer")

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "enter", "question_mark", "q"):
            event.stop()
            self.dismiss()


class SearchInput(Input):
    """
    Ô nhập tìm kiếm mobile:
    - Khi active: Nền sáng nổi bật (Amber), chữ đen đậm.
    - Hiển thị con trỏ khối đặc '█' khi focus.
    - Tự động kích hoạt làm mờ bảng khi nhận focus.
    """

    def on_focus(self) -> None:
        app = self.app
        if hasattr(app, "enter_search_mode"):
            app.enter_search_mode()

    def on_blur(self) -> None:
        app = self.app
        if hasattr(app, "exit_search_mode") and getattr(app, "is_search_mode", False):
            app.exit_search_mode()

    def render_line(self, y: int) -> Strip:
        if y != 0:
            return Strip.blank(self.size.width, self.rich_style)

        console = self.app.console
        console_options = self.app.console_options
        max_content_width = self.scrollable_content_region.width

        if not self.value:
            if self.has_focus:
                cursor_char = "█" if self._cursor_visible else " "
                placeholder = Text(cursor_char, style="bold black", end="")
                if self.placeholder:
                    placeholder += Text(f" {self.placeholder}", style="dim #3e2723 italic", end="")
            else:
                placeholder = Text(self.placeholder, justify="left", end="")
                placeholder.stylize(self.get_component_rich_style("input--placeholder"))

            strip = Strip(
                console.render(
                    placeholder, console_options.update_width(max_content_width + 1)
                )
            )
        else:
            result = self._value.copy()
            value = self.value
            value_length = len(value)
            suggestion = self._suggestion
            show_suggestion = len(suggestion) > value_length and self.has_focus
            if show_suggestion:
                result += Text(
                    suggestion[value_length:],
                    self.get_component_rich_style("input--suggestion"),
                    end="",
                )

            if self.has_focus:
                if not self.selection.is_empty:
                    start, end = self.selection
                    start, end = sorted((start, end))
                    selection_style = self.get_component_rich_style("input--selection")
                    result.stylize_before(selection_style, start, end)

                if self._cursor_visible:
                    cursor = self.cursor_position
                    if not show_suggestion and self.cursor_at_end:
                        result.append("█", style="bold black")
                    else:
                        cursor_style = self.get_component_rich_style("input--cursor")
                        result.stylize(cursor_style, cursor, cursor + 1)

            segments = list(
                console.render(result, console_options.update_width(self.content_width))
            )

            strip = Strip(segments)
            scroll_x, _ = self.scroll_offset
            strip = strip.crop(scroll_x, scroll_x + max_content_width + 1)
            strip = strip.extend_cell_length(max_content_width + 1)

        return strip.apply_style(self.rich_style)


class IMSMobileApp(App[None]):
    """Ứng dụng Quản lý Lịch dạy IMS Tối ưu Màn hình Dọc Điện thoại."""

    TITLE = "IMS LỊCH MOBILE"

    CSS = """
    Screen {
        background: $background;
        layout: vertical;
        padding: 0;
        margin: 0;
    }

    /* ── Header 1 dòng siêu gọn trên Mobile ── */
    #header_bar {
        dock: top;
        height: 1;
        padding: 0;
        margin: 0;
        background: #181b22;
        layout: horizontal;
        align-vertical: middle;
    }

    /* Badge tuần / chế độ bên trái: [T1], [T2], [ALL] */
    #mode_badge {
        width: auto;
        min-width: 5;
        height: 1;
        content-align: center middle;
        background: #2b303c;
        color: #798299;
        text-style: bold;
        padding: 0 1;
    }

    /* Khi đang ở chế độ tìm kiếm: badge chuyển đỏ nổi bật */
    #header_bar.search-active #mode_badge {
        background: #d32f2f;
        color: #ffffff;
        text-style: bold;
    }

    /* Ô tìm kiếm co giãn chiếm trọn phần giữa */
    #search_input {
        width: 1fr;
        height: 1;
        background: #1e2230;
        color: #c8cdd9;
        border: none;
        padding: 0 1;
    }

    /* Khi tìm kiếm: nền Amber rực rỡ, chữ đen đậm */
    #header_bar.search-active #search_input,
    #search_input:focus {
        background: #ffb300;
        color: #1a0800;
        text-style: bold;
    }

    #header_bar.search-active #search_input > .input--placeholder,
    #search_input:focus > .input--placeholder {
        color: #5d3f00;
        text-style: italic;
    }

    /* Nhãn đếm số ca bên phải */
    #count_badge {
        width: auto;
        height: 1;
        content-align: right middle;
        padding: 0 1;
        color: #6c7086;
        text-style: bold;
    }

    /* Bảng dữ liệu chiếm trọn không gian còn lại */
    #table_container {
        height: 1fr;
        padding: 0;
        margin: 0;
    }

    DataTable {
        height: 100%;
        width: 100%;
        border: none;
        scrollbar-gutter: stable;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        scrollbar-color: #3b4252 #161922;
        scrollbar-color-hover: #5e81ac #161922;
        scrollbar-color-active: #88c0d0 #161922;
    }

    DataTable.dimmed {
        opacity: 35%;
        tint: #0b0e14 55%;
    }

    DataTable > .datatable--header {
        text-style: bold;
        background: $primary;
        color: $text;
    }

    DataTable > .datatable--cursor {
        background: #2a3447;
    }

    /* ── Footer 1 dòng hỗ trợ gợi ý phím tắt trên mobile ── */
    #footer_bar {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #14171f;
        layout: horizontal;
        align-vertical: middle;
    }

    #footer_text {
        width: 100%;
        height: 1;
        content-align: left middle;
        color: #6c7086;
    }
    """

    BINDINGS = [
        Binding("u", "update_schedule", "Cập nhật (API)", priority=True),
        Binding("r", "refresh_table", "Tải lại", priority=True),
        Binding("slash", "focus_search", "Tìm kiếm", priority=True),
        Binding("question_mark", "show_help", "Phím tắt", priority=True),
        Binding("f", "toggle_foreign", "GV Nước ngoài", priority=True),
        Binding("p", "toggle_pending", "Chờ duyệt", priority=True),
        Binding("1", "select_week_1", "Tuần 1", priority=True),
        Binding("2", "select_week_2", "Tuần 2", priority=True),
        Binding("3", "select_week_all", "Cả 2 tuần", priority=True),
        Binding("a", "select_week_all", "Cả 2 tuần", priority=False),
        Binding("j", "cursor_down", show=False, priority=False),
        Binding("k", "cursor_up", show=False, priority=False),
        Binding("escape", "clear_search", "Bảng", priority=True),
        Binding("enter", "view_details", "Xem chi tiết", priority=False),
        Binding("q", "quit_app", "Thoát", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.is_updating: bool = False
        self.is_search_mode: bool = False
        self.current_week_filter: int = 0  # 0: Cả 2 tuần, 1: Tuần 1, 2: Tuần 2
        self.filter_foreign: bool = False   # [f] Lọc GV nước ngoài
        self.filter_pending: bool = False   # [p] Lọc ca chờ duyệt
        self.week1_range: tuple[str, str] | None = None
        self.week2_range: tuple[str, str] | None = None
        self.all_sessions: list[dict[str, Any]] = []
        self.filtered_sessions: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="header_bar"):
            yield Static("[ALL]", id="mode_badge")
            yield SearchInput(
                placeholder="🔍 Tìm kiếm... [/]",
                id="search_input",
            )
            yield Static("", id="count_badge")

        with Vertical(id="table_container"):
            yield DataTable(id="schedule_table")

        with Horizontal(id="footer_bar"):
            yield Static(
                "[cyan]123[/cyan]:Tuần  [cyan]f[/cyan]:GVNN  [cyan]p[/cyan]:Chờ  [cyan]u[/cyan]:Sync  [cyan]?[/cyan]:Giúp  [cyan]q[/cyan]:Thoát",
                id="footer_text",
            )

    def on_mount(self) -> None:
        """Khởi tạo cấu trúc bảng 5 cột tối ưu cho Mobile."""
        table = self.query_one("#schedule_table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.cursor_foreground_priority = "renderable"
        table.cursor_background_priority = "css"

        # 5 cột thiết yếu nhất, vừa khít chiều ngang màn hình dọc
        table.add_columns(
            "Ngày",
            "Giờ",
            "Giáo viên",
            "Phòng",
            "XN",
        )

        # Nạp dữ liệu
        self.load_schedule_data()

        # Focus vào bảng ban đầu
        table.focus()

    def on_resize(self, event: events.Resize) -> None:
        """Cập nhật lại độ dài tên giáo viên khi xoay ngang/dọc điện thoại."""
        if self.filtered_sessions:
            self.populate_table(self.filtered_sessions)

    def get_teacher_max_len(self) -> int:
        """Tính toán độ dài tối đa tên giáo viên phù hợp với chiều rộng màn hình hiện tại."""
        width = self.size.width or 55
        # Độ rộng các cột còn lại: Ngày(8) + Giờ(11) + Phòng(6) + XN(2) + padding/scrollbar ≈ 34-36
        return max(15, min(25, width - 36))

    def load_schedule_data(self) -> None:
        """Đọc và xử lý dữ liệu từ weekly.json."""
        table = self.query_one("#schedule_table", DataTable)
        footer_text = self.query_one("#footer_text", Static)

        if not WEEKLY_FILE.exists():
            self.notify(
                "Chưa có dữ liệu: Không tìm thấy weekly.json. Nhấn 'U' để đồng bộ.",
                title="Chưa có dữ liệu",
                severity="warning",
                timeout=6,
            )
            table.clear(columns=False)
            footer_text.update(
                Text.from_markup("[bold yellow]⚠️ Không thấy weekly.json - Bấm [u] để tải lịch[/bold yellow]")
            )
            self.all_sessions = []
            self.filtered_sessions = []
            return

        try:
            with open(WEEKLY_FILE, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            if not isinstance(raw_data, list):
                raw_data = []
        except Exception as exc:
            self.notify(
                f"Lỗi đọc file weekly.json: {exc}",
                title="Lỗi dữ liệu",
                severity="error",
                timeout=6,
            )
            return

        # Sắp xếp buổi học theo Ngày -> Giờ -> Phòng
        def sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
            return (
                item.get("sessionDate") or "",
                item.get("startTime") or "",
                item.get("room") or "",
            )

        raw_data.sort(key=sort_key)

        # Lưu cache dữ liệu với định dạng rút gọn sẵn
        self.all_sessions = []
        for item in raw_data:
            s_copy = dict(item)
            s_copy["date_mobile"] = format_date_mobile(item.get("sessionDate"))
            s_copy["date_formatted"] = format_date_full(item.get("sessionDate"))
            s_copy["room_short"] = shorten_room(item.get("room"))
            self.all_sessions.append(s_copy)

        # Xác định khoảng ngày Tuần 1 và Tuần 2
        dates = sorted(set(s.get("sessionDate") for s in self.all_sessions if s.get("sessionDate")))
        if dates:
            first_d = datetime.strptime(dates[0], "%Y-%m-%d").date()
            w1_start = first_d - timedelta(days=first_d.weekday())
            w1_end = w1_start + timedelta(days=6)
            w2_start = w1_start + timedelta(days=7)
            w2_end = w2_start + timedelta(days=6)
            self.week1_range = (w1_start.isoformat(), w1_end.isoformat())
            self.week2_range = (w2_start.isoformat(), w2_end.isoformat())
        else:
            today = date.today()
            w1_start = today - timedelta(days=today.weekday())
            w1_end = w1_start + timedelta(days=6)
            w2_start = w1_start + timedelta(days=7)
            w2_end = w2_start + timedelta(days=6)
            self.week1_range = (w1_start.isoformat(), w1_end.isoformat())
            self.week2_range = (w2_start.isoformat(), w2_end.isoformat())

        self.apply_filter()

    def update_header_badge(self) -> None:
        """Cập nhật badge chế độ / tuần ở góc trái Header."""
        mode_badge = self.query_one("#mode_badge", Static)
        if self.is_search_mode:
            mode_badge.update("[TÌM]")
            return

        # Xác định nhãn tuần
        if self.current_week_filter == 1:
            badge_text = "T1"
        elif self.current_week_filter == 2:
            badge_text = "T2"
        else:
            badge_text = "ALL"

        # Bổ sung chỉ báo lọc
        flags = []
        if self.filter_foreign:
            flags.append("GV")
        if self.filter_pending:
            flags.append("P")

        if flags:
            badge_str = f"[{badge_text}•{'+'.join(flags)}]"
        else:
            badge_str = f"[{badge_text}]"

        mode_badge.update(badge_str)

    def apply_filter(self) -> None:
        """Lọc danh sách ca học kết hợp tuần, GVNN, chờ duyệt và từ khóa tìm kiếm."""
        # 1. Lọc theo tuần
        if self.current_week_filter == 1 and self.week1_range:
            sessions = [
                s for s in self.all_sessions
                if self.week1_range[0] <= (s.get("sessionDate") or "") <= self.week1_range[1]
            ]
        elif self.current_week_filter == 2 and self.week2_range:
            sessions = [
                s for s in self.all_sessions
                if self.week2_range[0] <= (s.get("sessionDate") or "") <= self.week2_range[1]
            ]
        else:
            sessions = list(self.all_sessions)

        # 2. Lọc GV nước ngoài
        if self.filter_foreign:
            sessions = [s for s in sessions if is_foreign_teacher(s)]

        # 3. Lọc ca chờ duyệt
        if self.filter_pending:
            sessions = [s for s in sessions if is_pending_session(s)]

        # 4. Tìm kiếm live search (không dấu tiếng Việt)
        search_input = self.query_one("#search_input", SearchInput)
        query = search_input.value.strip()

        if query:
            q_norm = normalize_text(query)

            def match(item: dict[str, Any]) -> bool:
                class_ref = item.get("classRef") or {}
                teacher_obj = item.get("teacher") or {}
                cover_obj = item.get("coverTeacher") or {}
                skill_obj = item.get("skill") or {}
                search_targets = [
                    item.get("sessionDate") or "",
                    item.get("date_mobile") or "",
                    item.get("date_formatted") or "",
                    item.get("startTime") or "",
                    item.get("endTime") or "",
                    class_ref.get("code") or "",
                    class_ref.get("name") or "",
                    item.get("room") or "",
                    item.get("room_short") or "",
                    teacher_obj.get("name", "") if isinstance(teacher_obj, dict) else str(teacher_obj),
                    cover_obj.get("name", "") if isinstance(cover_obj, dict) else str(cover_obj),
                    skill_obj.get("name", "") if isinstance(skill_obj, dict) else str(skill_obj),
                    item.get("status") or "",
                    get_dual_tick_status(item) or "",
                ]
                return any(q_norm in normalize_text(t) for t in search_targets)

            sessions = [s for s in sessions if match(s)]

        self.filtered_sessions = sessions
        self.populate_table(self.filtered_sessions)
        self.update_header_badge()

        # Cập nhật số lượng ca học
        count_badge = self.query_one("#count_badge", Static)
        total = len(self.all_sessions)
        shown = len(sessions)
        if shown < total:
            count_badge.update(Text(f"{shown}/{total}", style="bold #ffb300"))
        else:
            count_badge.update(Text(f"{shown}", style="dim"))

    def populate_table(self, sessions: list[dict[str, Any]]) -> None:
        """Đổ dữ liệu vào bảng DataTable 5 cột thiết yếu cho Mobile."""
        table = self.query_one("#schedule_table", DataTable)
        table.clear(columns=False)

        max_t_len = self.get_teacher_max_len()

        for idx, item in enumerate(sessions):
            # Cột 1: Ngày (T2 28/09)
            date_display = item.get("date_mobile") or format_date_mobile(item.get("sessionDate"))

            # Cột 2: Giờ (08:00-09:00 nén gọn)
            start_t = item.get("startTime", "??:??")
            end_t = item.get("endTime", "??:??")
            time_display = f"{start_t}-{end_t}"

            # Cột 3: Giáo viên (Ưu tiên hiển thị rõ, viết tắt tên đệm nếu dài)
            teacher_obj = item.get("teacher")
            teacher_name = (
                teacher_obj.get("name")
                if isinstance(teacher_obj, dict)
                else str(teacher_obj or "")
            )
            cover_obj = item.get("coverTeacher")
            cover_name = (
                cover_obj.get("name")
                if isinstance(cover_obj, dict)
                else str(cover_obj or "")
            )

            if cover_name:
                teacher_text = f"{format_teacher_mobile(cover_name, max_t_len)}*"
                teacher_style = "#e5c07b"  # Vàng: báo hiệu dạy thay
            elif teacher_name:
                teacher_text = format_teacher_mobile(teacher_name, max_t_len)
                teacher_style = "#abb2bf"
            else:
                teacher_text = "Chưa rõ"
                teacher_style = "dim"

            # Cột 4: Phòng học rút gọn (P1-80, B1.1, ONL)
            room_display = item.get("room_short") or shorten_room(item.get("room"))

            # Cột 5: XN (Chỉ 1 chấm màu: Xanh cho Confirmed, Đỏ cho Pending)
            is_pending = is_pending_session(item)
            if not is_pending:
                xn_text = Text("●", style="bold green")
            else:
                xn_text = Text("●", style="bold red")

            row_key = str(item.get("id", idx))

            table.add_row(
                Text(date_display, style="cyan"),
                Text(time_display, style="#e5c07b"),
                Text(teacher_text, style=teacher_style),
                Text(room_display, style="#c678dd"),
                xn_text,
                key=row_key,
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        """Lọc dữ liệu tức thì khi gõ từ khóa tìm kiếm."""
        if event.input.id == "search_input":
            self.apply_filter()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter khi đang tìm kiếm: thoát Spotlight, chuyển con trỏ xuống bảng để duyệt kết quả."""
        if event.input.id != "search_input":
            return
        self.exit_search_mode()
        table = self.query_one("#schedule_table", DataTable)
        if self.filtered_sessions:
            table.move_cursor(row=0)

    def enter_search_mode(self) -> None:
        """Kích hoạt chế độ tìm kiếm Spotlight."""
        if self.is_search_mode:
            return
        self.is_search_mode = True
        header_bar = self.query_one("#header_bar")
        header_bar.add_class("search-active")

        self.update_header_badge()

        table = self.query_one("#schedule_table", DataTable)
        table.add_class("dimmed")

        search_input = self.query_one("#search_input", SearchInput)
        if not search_input.has_focus:
            search_input.focus()

    def exit_search_mode(self) -> None:
        """Thoát chế độ tìm kiếm, trả tiêu điểm về bảng dữ liệu."""
        if not self.is_search_mode:
            return
        self.is_search_mode = False
        header_bar = self.query_one("#header_bar")
        header_bar.remove_class("search-active")

        self.update_header_badge()

        table = self.query_one("#schedule_table", DataTable)
        table.remove_class("dimmed")
        if not table.has_focus:
            table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Chạm hoặc nhấn Enter trên dòng để xem chi tiết buổi học."""
        self.show_selected_row_detail(event.cursor_row)

    def show_selected_row_detail(self, row_index: int) -> None:
        """Mở Modal chi tiết buổi học."""
        if 0 <= row_index < len(self.filtered_sessions):
            session_item = self.filtered_sessions[row_index]
            self.push_screen(SessionDetailModal(session_item))

    # -------------------------------------------------------------------------
    # Các phím tắt chức năng
    # -------------------------------------------------------------------------

    def action_focus_search(self) -> None:
        """Phím /: Kích hoạt tìm kiếm nhanh."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            search_input.insert_text_at_cursor("/")
            return
        self.enter_search_mode()

    def action_clear_search(self) -> None:
        """Phím Escape: Thoát modal, thoát tìm kiếm hoặc xóa từ khóa."""
        if len(self.screen_stack) > 1:
            self.pop_screen()
            return
        search_input = self.query_one("#search_input", SearchInput)
        if self.is_search_mode or search_input.has_focus:
            self.exit_search_mode()
        else:
            if search_input.value:
                search_input.value = ""

    def action_select_week_1(self) -> None:
        """Phím 1: Chỉ xem lịch Tuần 1."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        self.current_week_filter = 1
        self.apply_filter()
        self.notify("Đang hiển thị: Tuần 1", title="Chọn tuần", timeout=2)

    def action_select_week_2(self) -> None:
        """Phím 2: Chỉ xem lịch Tuần 2."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        self.current_week_filter = 2
        self.apply_filter()
        self.notify("Đang hiển thị: Tuần 2", title="Chọn tuần", timeout=2)

    def action_select_week_all(self) -> None:
        """Phím 3 hoặc A: Xem cả 2 tuần."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        self.current_week_filter = 0
        self.apply_filter()
        self.notify("Đang hiển thị: Cả 2 tuần", title="Chọn tuần", timeout=2)

    def action_toggle_foreign(self) -> None:
        """Phím F: Bật/tắt lọc giáo viên nước ngoài."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        self.filter_foreign = not self.filter_foreign
        self.apply_filter()
        status_str = "BẬT" if self.filter_foreign else "TẮT"
        self.notify(f"Lọc GV Nước ngoài: {status_str}", title="[GVNN]", timeout=2)

    def action_toggle_pending(self) -> None:
        """Phím P: Bật/tắt lọc ca học chờ duyệt."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        self.filter_pending = not self.filter_pending
        self.apply_filter()
        status_str = "BẬT" if self.filter_pending else "TẮT"
        self.notify(f"Lọc Chờ duyệt: {status_str}", title="[Chờ duyệt]", timeout=2)

    def action_show_help(self) -> None:
        """Phím ?: Xem danh sách phím tắt."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            search_input.insert_text_at_cursor("?")
            return
        self.push_screen(HelpModal())

    def action_cursor_down(self) -> None:
        """Phím j: Di chuyển xuống 1 dòng."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        self.query_one("#schedule_table", DataTable).action_scroll_down()

    def action_cursor_up(self) -> None:
        """Phím k: Di chuyển lên 1 dòng."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        self.query_one("#schedule_table", DataTable).action_scroll_up()

    def action_view_details(self) -> None:
        """Phím Enter trên bảng: Mở chi tiết buổi học."""
        table = self.query_one("#schedule_table", DataTable)
        if table.cursor_row is not None:
            self.show_selected_row_detail(table.cursor_row)

    def action_refresh_table(self) -> None:
        """Phím R: Nạp lại file weekly.json."""
        self.load_schedule_data()
        self.notify("Đã nạp lại dữ liệu từ đĩa.", title="Tải lại", timeout=2)

    def reset_footer(self) -> None:
        """Khôi phục lại dòng gợi ý phím tắt ở footer."""
        try:
            footer_text = self.query_one("#footer_text", Static)
            footer_text.update(
                "[cyan]123[/cyan]:Tuần  [cyan]f[/cyan]:GVNN  [cyan]p[/cyan]:Chờ  [cyan]u[/cyan]:Sync  [cyan]?[/cyan]:Giúp  [cyan]q[/cyan]:Thoát"
            )
        except Exception:
            pass

    @work(exclusive=True, thread=False)
    async def action_update_schedule(self) -> None:
        """Phím U: Chạy ngầm ./update_schedule.sh đồng bộ từ API."""
        if self.is_updating:
            self.notify("Đang đồng bộ dữ liệu, vui lòng đợi...", title="Đang cập nhật", timeout=3)
            return

        if not UPDATE_SCRIPT.exists():
            self.notify(f"Không tìm thấy script: {UPDATE_SCRIPT.name}", title="Lỗi", severity="error", timeout=5)
            return

        self.is_updating = True
        table = self.query_one("#schedule_table", DataTable)
        footer_text = self.query_one("#footer_text", Static)

        table.loading = True
        footer_text.update(Text.from_markup("[bold yellow]⏳ Đang đồng bộ lịch từ hệ thống IMS...[/bold yellow]"))
        self.notify("Bắt đầu đồng bộ 2 tuần từ API IMS...", title="Cập nhật", timeout=4)

        try:
            proc = await asyncio.create_subprocess_exec(
                "/bin/bash",
                str(UPDATE_SCRIPT),
                "--weeks",
                "2",
                cwd=str(BASE_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()

            if proc.returncode == 0:
                self.notify("Đồng bộ lịch thành công!", title="Thành công", severity="information", timeout=4)
                footer_text.update(Text.from_markup("[bold green]✓ Cập nhật thành công![/bold green]"))
                self.load_schedule_data()
            else:
                err = (
                    stderr_bytes.decode(errors="replace").strip()
                    or stdout_bytes.decode(errors="replace").strip()
                )
                last_err = err.splitlines()[-1] if err else f"Mã lỗi {proc.returncode}"
                self.notify(f"Thất bại: {last_err}", title="Lỗi cập nhật", severity="error", timeout=6)
                footer_text.update(Text.from_markup(f"[bold red]❌ Lỗi ({proc.returncode}) - Nhấn u thử lại[/bold red]"))
        except Exception as exc:
            self.notify(f"Ngoại lệ: {exc}", title="Lỗi ngoại lệ", severity="error", timeout=6)
            footer_text.update(Text.from_markup(f"[bold red]❌ Lỗi ngoại lệ: {exc}[/bold red]"))
        finally:
            table.loading = False
            self.is_updating = False
            self.set_timer(4.0, self.reset_footer)

    def action_quit_app(self) -> None:
        """Phím Q: Thoát ứng dụng an toàn."""
        self.exit()


if __name__ == "__main__":
    app = IMSMobileApp()
    app.run()
