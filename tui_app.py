#!/usr/bin/env python3
"""
HỆ THỐNG QUẢN LÝ LỊCH IMS - TERMINAL USER INTERFACE (TUI)
Sử dụng thư viện Textual để hiển thị và quản lý lịch dạy trực quan qua terminal / SSH.
Thiết kế tối ưu không gian: Header ngang chia 2 phần (Khung tìm kiếm 100 cột + Điều hướng/Badge tuần),
bảng chiếm trọn vẹn 100% chiều cao màn hình còn lại.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import functools
import json
import os
from pathlib import Path
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
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.timer import Timer
from textual.widgets import Button, DataTable, Input, Label, Static
from textual.worker import get_current_worker

# Đường dẫn tới các file dữ liệu và script
BASE_DIR = Path(__file__).resolve().parent
WEEKLY_FILE = BASE_DIR / "weekly.json"
UPDATE_SCRIPT = BASE_DIR / "update_schedule.sh"

# Bảng dịch thứ trong tuần sang tiếng Việt
WEEKDAY_MAP = {
    0: "Thứ 2",
    1: "Thứ 3",
    2: "Thứ 4",
    3: "Thứ 5",
    4: "Thứ 6",
    5: "Thứ 7",
    6: "Chủ Nhật",
}

# Style hiển thị trạng thái lớp học
STATUS_CONFIG = {
    "completed": ("[green]● Hoàn thành[/green]", "Hoàn thành"),
    "scheduled": ("[cyan]● Sắp diễn ra[/cyan]", "Sắp diễn ra"),
    "covered": ("[yellow]● Dạy thay[/yellow]", "Dạy thay"),
}

# Tập tên giáo viên nước ngoài đã biết (kiểm tra không phân biệt hoa/thường)
FOREIGN_TEACHER_NAMES: set[str] = {
    "alex", "timothy", "leon", "anna", "james", "michael",
    "david", "sarah", "emma", "jessica", "william", "george",
    "henry", "edward", "richard",
}

EXCLUDED_NAMES: set[str] = {
    "academic faculty",
    "giang long huy",
}

VIETNAMESE_COMMON_SURNAMES: set[str] = {
    "nguyen", "tran", "le", "pham", "hoang", "huynh", "phan", 
    "vu", "vo", "dang", "bui", "do", "ho", "ngo", "duong", "ly", "giang"
}

@functools.lru_cache(maxsize=128)
def format_date_vn(iso_date: str | None) -> str:
    """Định dạng ngày YYYY-MM-DD sang tiếng Việt: Thứ X (DD/MM/YYYY) có memoization cache."""
    if not iso_date:
        return "N/A"
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        weekday_vn = WEEKDAY_MAP.get(dt.weekday(), "")
        return f"{weekday_vn} ({dt.strftime('%d/%m/%Y')})"
    except ValueError:
        return str(iso_date)


def normalize_text(text: str) -> str:
    """Loại bỏ dấu tiếng Việt và chuyển sang chữ thường (lowercase) để so khớp tức thì."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.replace("Đ", "d").replace("đ", "d").lower().strip()


def is_foreign_teacher(session: dict[str, Any]) -> bool:
    """Trả về True nếu giáo viên (hoặc giáo viên dạy thay) là người nước ngoài.
    Tiêu chí:
    1. Tên có trong FOREIGN_TEACHER_NAMES.
    2. Tên không chứa ký tự tiếng Việt có dấu (dấu thanh điệu Unicode).
    3. Không thuộc danh sách EXCLUDED_NAMES.
    """
    def _has_vietnamese_tone(name: str) -> bool:
        """True nếu tên có ký tự thuộc khối combining diacritics (dấu tiếng Việt)."""
        nfd = unicodedata.normalize("NFD", name)
        return any(unicodedata.combining(ch) for ch in nfd)

    def _check_name(name: str | None) -> bool:
        if not name:
            return False
        low = name.strip().lower()

        # 1. Chặn ngay nếu nằm trong danh sách loại trừ
        if any(ex in low for ex in EXCLUDED_NAMES):
            return False

        # 2. Khớp với tên GVNN đã biết
        if any(known in low for known in FOREIGN_TEACHER_NAMES):
            return True

        # 3. Nếu tên không dấu hoàn toàn và không phải ký tự đặc biệt tiếng Việt
        if not _has_vietnamese_tone(name):
            # Phải có ít nhất 1 từ ASCII (không phải họ tên Việt)
            ascii_ratio = sum(1 for c in name if ord(c) < 128) / max(len(name), 1)
            return ascii_ratio >= 0.85

        return False

    # Giữ nguyên cấu trúc bóc tách dữ liệu gốc của bạn
    teacher_obj = session.get("teacher")
    teacher_name = (
        teacher_obj.get("name") if isinstance(teacher_obj, dict) else str(teacher_obj or "")
    )
    cover_obj = session.get("coverTeacher")
    cover_name = (
        cover_obj.get("name") if isinstance(cover_obj, dict) else str(cover_obj or "")
    )

    # Dùng giáo viên dạy thay nếu có, ngược lại dùng giáo viên chính
    active_name = cover_name if cover_name else teacher_name
    return _check_name(active_name)


def is_pending_session(session: dict[str, Any]) -> bool:
    """Trả về True nếu ca học chưa hoàn tất xác nhận (không phải CONFIRMED)."""
    dual_tick = get_dual_tick_status(session)
    if not dual_tick:
        return True
    st = str(dual_tick).strip().upper()
    return st not in ("CONFIRMED", "TRUE")


def get_dual_tick_status(item: dict[str, Any]) -> str | None:
    """
    Trích xuất trạng thái chấm công (Dual Tick) từ bản ghi buổi học,
    hỗ trợ nhiều trường dữ liệu và fallback (dual_tick_status, confirmStatus, attendanceStatus, isConfirmed,...).
    """
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


def format_dual_tick_status(status: Any) -> str:
    """
    Định dạng màu sắc trạng thái Dual Tick (chấm công) cho Rich/Textual:
    - "CONFIRMED" / True: [bold green]● Confirmed[/bold green]
    - "PENDING_TEACHER" (hoặc chứa 'Pending'): [bold #87CEFA]○ Pending Teacher[/bold #87CEFA]
    - Rỗng hoặc không có: [dim]-[/dim]
    """
    if not status:
        return "[dim]-[/dim]"
    return _format_dual_tick_cached(str(status).strip())


@functools.lru_cache(maxsize=32)
def _format_dual_tick_cached(st: str) -> str:
    if st.upper() in ("CONFIRMED", "TRUE"):
        return "[bold green]● Confirmed[/bold green]"
    elif "PENDING" in st.upper():
        return "[bold #87CEFA]○ Pending Teacher[/bold #87CEFA]"
    return f"[dim]{st}[/dim]"


class SessionDetailModal(ModalScreen[None]):
    """Cửa sổ Modal hiển thị thông tin chi tiết một buổi học khi bấm Enter."""

    DEFAULT_CSS = """
    SessionDetailModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #modal_dialog {
        width: 75;
        height: auto;
        max-height: 85%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
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
        Binding("escape", "dismiss", "Đóng"),
        Binding("enter", "dismiss", "Đóng"),
        Binding("q", "dismiss", "Đóng"),
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

        detail_lines = [
            f"[bold cyan]Mã lớp:[/bold cyan] {class_ref.get('code', 'N/A')}",
            f"[bold cyan]Tên lớp:[/bold cyan] {class_ref.get('name') or '(Không có tên riêng)'}",
            f"[bold cyan]Ngày học:[/bold cyan] {s.get('date_formatted', s.get('sessionDate', 'N/A'))}",
            f"[bold cyan]Thời gian:[/bold cyan] {s.get('startTime', '??:??')} - {s.get('endTime', '??:??')}",
            f"[bold cyan]Phòng học:[/bold cyan] {s.get('room', 'N/A')}",
            f"[bold cyan]Giáo viên chính:[/bold cyan] {teacher_name} {f'<{teacher_email}>' if teacher_email else ''}",
            f"[bold cyan]Giáo viên dạy thay:[/bold cyan] {cover_name or 'Không có'}",
            f"[bold cyan]Kỹ năng / Môn học:[/bold cyan] {skill_info.get('name', 'N/A') if isinstance(skill_info, dict) else 'N/A'}",
            f"[bold cyan]Trạng thái:[/bold cyan] {s.get('status', 'N/A')}",
            f"[bold cyan]Trạng thái chấm công:[/bold cyan] {format_dual_tick_status(get_dual_tick_status(s))}",
            f"[bold cyan]Google Meet Link:[/bold cyan] {s.get('googleMeetLink') or 'Không có'}",
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


class SearchInput(Input):
    """
    Ô nhập văn bản tìm kiếm nâng cấp:
    - Khi active: Nền sáng nổi bật (Amber), chữ đen đậm.
    - Hiển thị con trỏ khối đặc '█' chờ lệnh ở cuối/vị trí con trỏ khi có focus.
    - Tự động kích hoạt Spotlight/Dimming khi nhận focus và hoàn nguyên khi mất focus.
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



class HelpModal(ModalScreen[None]):
    """Popup hiển thị toàn bộ phím tắt của ứng dụng."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #help_dialog {
        width: 66;
        height: auto;
        max-height: 90%;
        border: double #5e81ac;
        background: #1e222b;
        padding: 1 2;
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

    # Nội dung phím tắt theo nhóm: (key_label, description)
    _SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
        (
            "TÌM KIẾM & DUYỆT",
            [
                ("/",            "Bật thanh tìm kiếm Spotlight"),
                ("Enter",        "Xác nhận tìm kiếm → focus xuống bảng"),
                ("Esc",          "Thoát tìm kiếm / Xóa bộ lọc"),
                ("↑ / ↓",        "Di chuyển lên / xuống từng dòng"),
                ("Enter (bảng)", "Xem chi tiết buổi học đang chọn"),
            ],
        ),
        (
            "BỘ LỌC DỮ LIỆU",
            [
                ("1 / 2 / 3",    "Xem Tuần 1 / Tuần 2 / Cả 2 tuần"),
                ("f",            "Bật / tắt lọc GV nước ngoài"),
                ("p",            "Bật / tắt lọc ca Chờ duyệt (Pending)"),
            ],
        ),
        (
            "HỆ THỐNG",
            [
                ("u",  "Đồng bộ lịch mới nhất từ API IMS"),
                ("r",  "Nạp lại dữ liệu từ file đĩa"),
                ("?",  "Bật / tắt bảng trợ giúp này"),
                ("q",  "Thoát ứng dụng"),
            ],
        ),
    ]

    def compose(self) -> ComposeResult:
        lines: list[str] = []
        for section_title, keys in self._SECTIONS:
            # Tiêu đề nhóm
            lines.append(f"[bold #81a1c1]▸ {section_title}[/bold #81a1c1]")
            for key_label, desc in keys:
                key_part = f"[bold #ebcb8b]{key_label:<14}[/bold #ebcb8b]"
                desc_part = f"[#d8dee9]{desc}[/#d8dee9]"
                lines.append(f"  {key_part}  {desc_part}")
            lines.append("")  # blank line giữa các nhóm

        content_text = "\n".join(lines).rstrip()

        with Vertical(id="help_dialog"):
            yield Static("⌨  PHÍM TẮT  ⌨", id="help_title")
            yield Static(content_text, id="help_content")
            yield Static(
                "[dim]Nhấn  Esc / Enter / ?  để đóng[/dim]",
                id="help_footer",
            )

    def on_key(self, event: object) -> None:
        """Bắt phím trực tiếp ở màn hình modal — đáng tin cậy bất kể focus."""
        from textual.events import Key
        if isinstance(event, Key):
            if event.key in ("escape", "enter", "question_mark", "q"):
                event.stop()
                self.dismiss()


class IMSApp(App[None]):
    """Ứng dụng TUI Quản lý Lịch dạy IMS."""

    TITLE = "HỆ THỐNG QUẢN LÝ LỊCH IMS"

    CSS = """
    Screen {
        background: $background;
        layout: vertical;
    }

    /* ── Thanh Header 1 dòng gọn nhẹ ── */
    #header_bar {
        dock: top;
        height: 1;
        padding: 0 1;
        background: #181b22;
        layout: horizontal;
        align-vertical: middle;
    }

    /* Badge chế độ: [ BẢNG ] hoặc [ TÌM KIẾM ] – không border, chỉ màu nền */
    #mode_badge {
        width: 12;
        height: 1;
        content-align: center middle;
        background: #2b303c;
        color: #798299;
        text-style: bold;
        margin-right: 1;
    }

    /* Khi search-active: đổi badge sang đỏ */
    #header_bar.search-active #mode_badge {
        background: #d32f2f;
        color: #ffffff;
        text-style: bold;
    }

    /* Ô tìm kiếm phẳng – không border, chiều cao 1 dòng */
    #search_input {
        width: 85;
        height: 1;
        background: #1e2230;
        color: #c8cdd9;
        border: none;
        padding: 0 1;
    }

    /* Active: nền Amber nổi bật, chữ đen */
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

    /* Cụm badge tuần + phím tắt bên phải */
    #header_right {
        width: 1fr;
        height: 1;
        layout: horizontal;
        align-vertical: middle;
        padding-left: 2;
    }

    #week_badge {
        width: auto;
        height: 1;
        content-align: center middle;
        margin-right: 2;
    }

    #hotkeys_bar {
        width: auto;
        height: 1;
        content-align: center middle;
        color: #6c7086;
    }

    /* Bảng dữ liệu chiếm 100% chiều cao còn lại */
    #table_container {
        height: 1fr;
        padding: 0 1;
    }

    DataTable {
        height: 100%;
        width: 100%;
        border: solid $secondary;
        scrollbar-gutter: stable;
        scrollbar-size-vertical: 1;
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

        # Tối ưu hóa hiệu năng & bộ đệm
        self._search_timer: Timer | None = None
        self._last_filter_state: tuple[int, bool, bool, str] | None = None
        self._last_rendered_ids: list[Any] = []
        self._loaded_row_count: int = 0
        self._generation_id: int = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="header_bar"):
            yield Static("[ BẢNG ]", id="mode_badge")
            yield SearchInput(
                placeholder="Nhập tên GV, mã lớp, phòng, ngày... (Bấm Esc để về bảng)",
                id="search_input",
            )
            with Horizontal(id="header_right"):
                yield Static(id="week_badge")
                yield Static(id="hotkeys_bar")
        with Vertical(id="table_container"):
            yield DataTable(id="schedule_table")

    def on_mount(self) -> None:
        """Khởi tạo bảng DataTable và nạp dữ liệu ban đầu."""
        table = self.query_one("#schedule_table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        # Ưu tiên màu chữ gốc của từng cột (renderable), chỉ thay đổi màu nền (css) của hàng chọn
        table.cursor_foreground_priority = "renderable"
        table.cursor_background_priority = "css"

        # Thêm các cột cho DataTable
        table.add_columns(
            "STT",
            "Ngày học",
            "Giờ học",
            "Mã lớp",
            "Phòng học",
            "Giáo viên",
            "Trạng thái",
            "Xác nhận",
        )

        # Nạp dữ liệu từ weekly.json
        self.load_schedule_data()

        # Tập trung con trỏ vào bảng
        table.focus()

    def format_date_vn(self, iso_date: str | None) -> str:
        """Định dạng ngày YYYY-MM-DD sang tiếng Việt: Thứ X (DD/MM/YYYY) có cache."""
        return format_date_vn(iso_date)

    def load_schedule_data(self) -> None:
        """
        Đọc file weekly.json, tạo sẵn Inverted Search Index và cache các ô DataTable
        để loại bỏ hoàn toàn độ trễ khi tìm kiếm, chuyển filter và cuộn bảng.
        """
        table = self.query_one("#schedule_table", DataTable)
        hotkeys_bar = self.query_one("#hotkeys_bar", Static)

        # Kiểm tra file tồn tại
        if not WEEKLY_FILE.exists():
            self.notify(
                "Chưa có dữ liệu: Không tìm thấy file weekly.json. Hãy nhấn 'U' để cập nhật.",
                title="Chưa có dữ liệu",
                severity="warning",
                timeout=6,
            )
            table.clear(columns=False)
            hotkeys_bar.update(
                Text.from_markup(
                    r"[bold yellow]⚠️ Không tìm thấy weekly.json - Bấm [bold cyan]\[U][/bold cyan] để tải lịch[/bold yellow]"
                )
            )
            self.all_sessions = []
            self.filtered_sessions = []
            return

        # Đọc dữ liệu từ file
        try:
            with open(WEEKLY_FILE, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            if not isinstance(raw_data, list):
                raw_data = []
        except Exception as exc:
            self.notify(
                f"Lỗi khi đọc file weekly.json: {exc}",
                title="Lỗi dữ liệu",
                severity="error",
                timeout=6,
            )
            return

        # Sắp xếp buổi học theo Ngày -> Giờ bắt đầu -> Phòng
        def sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
            return (
                item.get("sessionDate") or "",
                item.get("startTime") or "",
                item.get("room") or "",
            )

        raw_data.sort(key=sort_key)

        # Tiền xử lý dữ liệu: Tạo Inverted Search Index + Pre-rendered cells cho từng ca
        self.all_sessions = []
        for item in raw_data:
            session = dict(item)
            iso_date = session.get("sessionDate")
            date_formatted = format_date_vn(iso_date)
            session["date_formatted"] = date_formatted
            session["_session_date"] = iso_date or ""

            # Phân loại sẵn để filter O(1)
            session["_is_foreign"] = is_foreign_teacher(session)
            session["_is_pending"] = is_pending_session(session)

            class_ref = session.get("classRef") or {}
            class_code = class_ref.get("code") or "N/A"
            class_name = class_ref.get("name") or ""
            room = session.get("room") or "N/A"

            teacher_obj = session.get("teacher")
            teacher_name = (
                teacher_obj.get("name")
                if isinstance(teacher_obj, dict)
                else str(teacher_obj or "")
            )
            teacher_email = (
                teacher_obj.get("email")
                if isinstance(teacher_obj, dict)
                else ""
            )
            cover_obj = session.get("coverTeacher")
            cover_name = (
                cover_obj.get("name")
                if isinstance(cover_obj, dict)
                else str(cover_obj or "")
            )

            if cover_name:
                teacher_display = f"{cover_name} (Dạy thay)"
            elif teacher_name:
                teacher_display = teacher_name
            else:
                teacher_display = "Chưa phân công"

            raw_status = session.get("status") or "scheduled"
            status_markup, status_label = STATUS_CONFIG.get(
                raw_status, (f"[dim]{raw_status}[/dim]", raw_status)
            )

            dual_tick_status = get_dual_tick_status(session)
            dual_tick_markup = format_dual_tick_status(dual_tick_status)

            time_display = f"{session.get('startTime', '??:??')} - {session.get('endTime', '??:??')}"

            # Pre-cache các ô Text của hàng (cột 1..7) để đưa vào DataTable không cần khởi tạo lại
            session["_cached_cells"] = (
                Text(date_formatted, style="cyan"),
                Text(time_display, style="#e5c07b"),
                Text(class_code, style="bold white"),
                Text(room, style="#c678dd"),
                Text(teacher_display, style="#abb2bf"),
                Text.from_markup(status_markup),
                Text.from_markup(dual_tick_markup),
            )

            # Inverted Search Index: Gộp chuỗi không dấu viết thường
            skill_info = session.get("skill") or {}
            skill_name = skill_info.get("name") if isinstance(skill_info, dict) else ""
            skill_code = skill_info.get("code") if isinstance(skill_info, dict) else ""

            search_parts = [
                teacher_name,
                teacher_email,
                cover_name,
                class_code,
                class_name,
                room,
                date_formatted,
                iso_date or "",
                session.get("startTime") or "",
                session.get("endTime") or "",
                raw_status,
                status_label,
                str(dual_tick_status or ""),
                skill_name,
                skill_code,
            ]
            session["_search_index"] = normalize_text(" ".join(filter(None, search_parts)))

            self.all_sessions.append(session)

        # Xác định khoảng ngày của Tuần 1 và Tuần 2
        dates = sorted(set(s.get("_session_date") for s in self.all_sessions if s.get("_session_date")))
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

        # Áp dụng bộ lọc hiện tại (bắt buộc nạp mới sau khi đọc file)
        self.apply_filter(force=True)

    @work(exclusive=True, thread=True)
    def apply_filter(self, force: bool = False) -> None:
        """
        Lọc danh sách buổi học kết hợp (AND logic) trong worker thread ngầm:
        1. Bộ lọc tuần  (0: Cả 2 tuần, 1: Tuần 1, 2: Tuần 2)
        2. Bộ lọc GV nước ngoài (filter_foreign)
        3. Bộ lọc ca chờ duyệt  (filter_pending)
        4. Inverted Search Index: Kiểm tra chuỗi con cực nhanh trên RAM.
        """
        worker = get_current_worker()
        if worker.is_cancelled:
            return

        try:
            search_input = self.query_one("#search_input", SearchInput)
            query = search_input.value.strip()
        except Exception:
            query = ""

        current_state = (self.current_week_filter, self.filter_foreign, self.filter_pending, query)
        if not force and current_state == self._last_filter_state:
            return

        # ── 1. Lọc theo tuần ──
        if self.current_week_filter == 1 and self.week1_range:
            sessions = [
                s for s in self.all_sessions
                if self.week1_range[0] <= s.get("_session_date", "") <= self.week1_range[1]
            ]
            week_badge_markup = r"[bold #00e5ff]\[Tuần: Tuần 1][/bold #00e5ff]"
        elif self.current_week_filter == 2 and self.week2_range:
            sessions = [
                s for s in self.all_sessions
                if self.week2_range[0] <= s.get("_session_date", "") <= self.week2_range[1]
            ]
            week_badge_markup = r"[bold #e040fb]\[Tuần: Tuần 2][/bold #e040fb]"
        else:
            sessions = list(self.all_sessions)
            week_badge_markup = r"[bold #00e676]\[Tuần: Cả 2 tuần][/bold #00e676]"

        # ── 2. Lọc GV nước ngoài ──
        if self.filter_foreign:
            sessions = [s for s in sessions if s.get("_is_foreign", False)]
            week_badge_markup += r"  [bold #ff9800]\[GV: Nước ngoài][/bold #ff9800]"

        # ── 3. Lọc ca chờ duyệt ──
        if self.filter_pending:
            sessions = [s for s in sessions if s.get("_is_pending", False)]
            week_badge_markup += r"  [bold #f44336]\[Lọc: Chờ duyệt][/bold #f44336]"

        if worker.is_cancelled:
            return

        # ── 4. Inverted Search Index ──
        if query:
            q_norm = normalize_text(query)
            q_words = q_norm.split()
            if len(q_words) == 1:
                kw = q_words[0]
                sessions = [s for s in sessions if kw in s.get("_search_index", "")]
            else:
                sessions = [
                    s for s in sessions
                    if (q_norm in s.get("_search_index", "") or all(w in s.get("_search_index", "") for w in q_words))
                ]

        if worker.is_cancelled:
            return

        # ── Thống kê số lượng ──
        total = len(self.all_sessions)
        shown = len(sessions)
        if shown < total:
            count_info = f"[bold #ffb300]({shown}/{total} ca)[/bold #ffb300]"
        else:
            count_info = f"[dim]({shown} ca)[/dim]"

        hotkeys_markup = (
            count_info
            + r"   [dim cyan]\[?][/dim cyan][dim] Phím tắt[/dim]"
            + r"   [dim cyan]\[q][/dim cyan][dim] Thoát[/dim]"
        )

        new_ids = [s.get("id") for s in sessions]
        data_changed = force or (new_ids != self._last_rendered_ids)

        if worker.is_cancelled:
            return

        # Cập nhật UI an toàn trên Main thread
        self.call_from_thread(
            self._apply_filter_results,
            sessions,
            current_state,
            new_ids,
            data_changed,
            week_badge_markup,
            hotkeys_markup,
        )

    def _apply_filter_results(
        self,
        sessions: list[dict[str, Any]],
        filter_state: tuple[int, bool, bool, str],
        new_ids: list[Any],
        data_changed: bool,
        week_badge_markup: str,
        hotkeys_markup: str,
    ) -> None:
        """Cập nhật giao diện trên UI thread với kết quả lọc từ worker thread."""
        self._last_filter_state = filter_state
        self.filtered_sessions = sessions

        week_badge = self.query_one("#week_badge", Static)
        week_badge.update(Text.from_markup(week_badge_markup))

        hotkeys_bar = self.query_one("#hotkeys_bar", Static)
        hotkeys_bar.update(Text.from_markup(hotkeys_markup))

        # Chỉ xóa và nạp lại bảng nếu danh sách kết quả thực sự thay đổi
        if data_changed:
            self._last_rendered_ids = new_ids
            self.populate_table(sessions)

    def populate_table(self, sessions: list[dict[str, Any]]) -> None:
        """
        Đổ danh sách buổi học vào DataTable:
        - Gom nhóm thao tác vẽ bằng batch_update() để loại bỏ độ trễ và giảm tải SSH.
        - Hiển thị tức thì 150 dòng đầu tiên (< 4ms).
        - Kích hoạt worker ngầm stream các dòng còn lại qua generator/chunking.
        """
        self._generation_id += 1
        gen_id = self._generation_id

        table = self.query_one("#schedule_table", DataTable)

        with self.batch_update():
            table.clear(columns=False)
            initial_count = min(len(sessions), 150)
            for idx in range(initial_count):
                item = sessions[idx]
                row_key = str(item.get("id", idx + 1))
                table.add_row(
                    Text(str(idx + 1), style="dim"),
                    *item["_cached_cells"],
                    key=row_key,
                )
            self._loaded_row_count = initial_count

        # Nếu còn dữ liệu chưa vẽ, stream nền bằng async worker
        if self._loaded_row_count < len(sessions):
            self._stream_remaining_rows(gen_id)

    @work(exclusive=True, name="stream_remaining_rows")
    async def _stream_remaining_rows(self, gen_id: int) -> None:
        """Worker nạp nền phần còn lại của danh sách theo từng khối để giữ 60 FPS."""
        chunk_size = 200
        while self._loaded_row_count < len(self.filtered_sessions):
            await asyncio.sleep(0.015)
            if gen_id != self._generation_id:
                return
            self._load_more_rows(chunk_size)

    def _load_more_rows(self, chunk_size: int = 200) -> None:
        """Nạp thêm một khối dòng vào bảng (dùng chung cho streamer và scroll on-demand)."""
        if self._loaded_row_count >= len(self.filtered_sessions):
            return
        table = self.query_one("#schedule_table", DataTable)
        start = self._loaded_row_count
        end = min(start + chunk_size, len(self.filtered_sessions))
        with self.batch_update():
            for idx in range(start, end):
                item = self.filtered_sessions[idx]
                row_key = str(item.get("id", idx + 1))
                table.add_row(
                    Text(str(idx + 1), style="dim"),
                    *item["_cached_cells"],
                    key=row_key,
                )
        self._loaded_row_count = end

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Khi con trỏ bảng di chuyển tới gần cuối số dòng đã nạp, lập tức nạp thêm on-demand."""
        if (
            event.cursor_row is not None
            and event.cursor_row >= self._loaded_row_count - 30
            and self._loaded_row_count < len(self.filtered_sessions)
        ):
            self._load_more_rows(chunk_size=150)

    def _cancel_search_timer(self) -> None:
        """Hủy bộ đếm debounce tìm kiếm nếu đang chờ."""
        if self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer = None

    def on_input_changed(self, event: Input.Changed) -> None:
        """Lọc danh sách khi người dùng gõ từ khóa tìm kiếm (Debounce 120ms)."""
        if event.input.id == "search_input":
            if self._search_timer is not None:
                self._search_timer.stop()
            self._search_timer = self.set_timer(0.12, self._debounced_search)

    def _debounced_search(self) -> None:
        """Thực thi tìm kiếm sau khoảng hoãn debounce 120ms."""
        self._search_timer = None
        self.apply_filter()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter khi đang gõ tìm kiếm: xác nhận kết quả, thoát Spotlight, chuyển focus xuống bảng."""
        if event.input.id != "search_input":
            return
        if self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer = None
            self.apply_filter()
        # Giữ nguyên từ khóa và kết quả lọc, chỉ thoát chế độ tìm kiếm
        self.exit_search_mode()
        # Di chuyển con trỏ xuống dòng đầu tiên của kết quả
        table = self.query_one("#schedule_table", DataTable)
        if self.filtered_sessions:
            table.move_cursor(row=0)

    def enter_search_mode(self) -> None:
        """Kích hoạt chế độ tìm kiếm: Spotlight ô nhập, đổi Badge [ TÌM KIẾM ], làm mờ bảng."""
        if self.is_search_mode:
            return
        self.is_search_mode = True
        header_bar = self.query_one("#header_bar")
        header_bar.add_class("search-active")

        mode_badge = self.query_one("#mode_badge", Static)
        mode_badge.update("[ TÌM KIẾM ]")

        table = self.query_one("#schedule_table", DataTable)
        table.add_class("dimmed")

        search_input = self.query_one("#search_input", SearchInput)
        if not search_input.has_focus:
            search_input.focus()

    def exit_search_mode(self) -> None:
        """Thoát chế độ tìm kiếm: Hoàn nguyên Badge [ BẢNG ], bảng sáng rõ, trả focus về bảng."""
        if not self.is_search_mode:
            return
        self.is_search_mode = False
        header_bar = self.query_one("#header_bar")
        header_bar.remove_class("search-active")

        mode_badge = self.query_one("#mode_badge", Static)
        mode_badge.update("[ BẢNG ]")

        table = self.query_one("#schedule_table", DataTable)
        table.remove_class("dimmed")
        if not table.has_focus:
            table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Xử lý khi người dùng chọn/nhấn Enter vào một hàng trong DataTable."""
        self.show_selected_row_detail(event.cursor_row)

    def show_selected_row_detail(self, row_index: int) -> None:
        """Hiển thị Modal chi tiết cho buổi học đang được chọn."""
        if 0 <= row_index < len(self.filtered_sessions):
            session_item = self.filtered_sessions[row_index]
            self.push_screen(SessionDetailModal(session_item))

    # -------------------------------------------------------------------------
    # Các Action xử lý phím tắt
    # -------------------------------------------------------------------------

    def action_focus_search(self) -> None:
        """Phím /: Kích hoạt chế độ tìm kiếm hoặc chèn '/' nếu đang gõ chữ."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            search_input.insert_text_at_cursor("/")
            return
        self.enter_search_mode()

    def action_clear_search(self) -> None:
        """Phím Escape: Thoát chế độ tìm kiếm hoặc xóa từ khóa tìm kiếm."""
        search_input = self.query_one("#search_input", SearchInput)
        if self.is_search_mode or search_input.has_focus:
            self.exit_search_mode()
        else:
            # Nếu đang ở bảng dữ liệu mà có từ khóa tìm kiếm, bấm Esc để xóa từ khóa
            if search_input.value:
                self._cancel_search_timer()
                search_input.value = ""

    def action_select_week_1(self) -> None:
        """Phím 1: Lọc chỉ xem lịch Tuần 1."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        if self.current_week_filter == 1:
            return
        self.current_week_filter = 1
        self._cancel_search_timer()
        self.apply_filter()
        self.notify("Đang hiển thị lịch: Tuần 1", title="Chọn tuần", timeout=2)

    def action_select_week_2(self) -> None:
        """Phím 2: Lọc chỉ xem lịch Tuần 2."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        if self.current_week_filter == 2:
            return
        self.current_week_filter = 2
        self._cancel_search_timer()
        self.apply_filter()
        self.notify("Đang hiển thị lịch: Tuần 2", title="Chọn tuần", timeout=2)

    def action_select_week_all(self) -> None:
        """Phím 3 hoặc A: Xem toàn bộ cả 2 tuần."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        if self.current_week_filter == 0:
            return
        self.current_week_filter = 0
        self._cancel_search_timer()
        self.apply_filter()
        self.notify("Đang hiển thị: Toàn bộ cả 2 tuần", title="Chọn tuần", timeout=2)

    def action_toggle_foreign(self) -> None:
        """Phím F: Bật/tắt lọc chỉ xem lớp học của GV nước ngoài."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        self.filter_foreign = not self.filter_foreign
        self._cancel_search_timer()
        self.apply_filter()
        if self.filter_foreign:
            self.notify(
                "Chỉ hiển thị lớp của GV nước ngoài. Bấm [F] để bỏ lọc.",
                title="[GV: Nước ngoài] BẬT",
                severity="information",
                timeout=3,
            )
        else:
            self.notify(
                "Hiển thị toàn bộ giáo viên.",
                title="[GV: Nước ngoài] TẮT",
                severity="information",
                timeout=2,
            )

    def action_toggle_pending(self) -> None:
        """Phím P: Bật/tắt lọc chỉ xem ca học chưa xác nhận (Pending)."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        self.filter_pending = not self.filter_pending
        self._cancel_search_timer()
        self.apply_filter()
        if self.filter_pending:
            self.notify(
                "Chỉ hiển thị ca học chưa được xác nhận. Bấm [P] để bỏ lọc.",
                title="[Lọc: Chờ duyệt] BẬT",
                severity="warning",
                timeout=3,
            )
        else:
            self.notify(
                "Hiển thị tất cả ca học.",
                title="[Lọc: Chờ duyệt] TẮT",
                severity="information",
                timeout=2,
            )

    def action_show_help(self) -> None:
        """Phím ?: Hiển thị Modal danh sách phím tắt."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            # Khi đang gõ, phím ? được chèn như ký tự bình thường
            search_input.insert_text_at_cursor("?")
            return
        self.push_screen(HelpModal())

    def action_cursor_down(self) -> None:
        """Phím j (vim): Di chuyển con trỏ bảng xuống 1 dòng."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        table = self.query_one("#schedule_table", DataTable)
        if (
            table.cursor_row is not None
            and table.cursor_row >= self._loaded_row_count - 5
            and self._loaded_row_count < len(self.filtered_sessions)
        ):
            self._load_more_rows(chunk_size=150)
        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        """Phím k (vim): Di chuyển con trỏ bảng lên 1 dòng."""
        search_input = self.query_one("#search_input", SearchInput)
        if search_input.has_focus:
            return
        table = self.query_one("#schedule_table", DataTable)
        table.action_cursor_up()

    def action_view_details(self) -> None:
        """Phím Enter: Xem chi tiết buổi học tại vị trí con trỏ hiện tại."""
        table = self.query_one("#schedule_table", DataTable)
        if table.cursor_row is not None:
            self.show_selected_row_detail(table.cursor_row)

    def action_refresh_table(self) -> None:
        """Phím R: Đọc lại file weekly.json và làm mới bảng dữ liệu."""
        self._cancel_search_timer()
        self.load_schedule_data()
        self.notify(
            "Đã nạp lại dữ liệu từ file weekly.json.",
            title="Tải lại",
            severity="information",
            timeout=3,
        )

    @work(exclusive=True, thread=False)
    async def action_update_schedule(self) -> None:
        """
        Phím U: Chạy ngầm ./update_schedule.sh bằng cơ chế Async Worker của Textual.
        Không làm đóng băng (freeze) giao diện, tự động làm mới bảng khi hoàn tất.
        """
        if self.is_updating:
            self.notify(
                "Quá trình cập nhật đang chạy, vui lòng đợi...",
                title="Đang cập nhật",
                severity="warning",
                timeout=4,
            )
            return

        if not UPDATE_SCRIPT.exists():
            self.notify(
                f"Không tìm thấy file script: {UPDATE_SCRIPT.name}",
                title="Lỗi cập nhật",
                severity="error",
                timeout=6,
            )
            return

        self.is_updating = True
        table = self.query_one("#schedule_table", DataTable)
        hotkeys_bar = self.query_one("#hotkeys_bar", Static)

        # Bật trạng thái loading của DataTable và cập nhật thanh trạng thái
        table.loading = True
        hotkeys_bar.update(
            Text.from_markup(
                "[bold yellow]⏳ ĐANG CẬP NHẬT DỮ LIỆU TỪ HỆ THỐNG IMS... (Vui lòng đợi)[/bold yellow]"
            )
        )

        # Hiển thị Toast thông báo bắt đầu cập nhật
        self.notify(
            "Đang thực thi ./update_schedule.sh --weeks 2 để đồng bộ lịch mới nhất...",
            title="Bắt đầu cập nhật",
            severity="information",
            timeout=5,
        )

        try:
            # Chạy file bash không đồng bộ (non-blocking)
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
                # Thông báo thành công qua Toast
                self.notify(
                    "Cập nhật lịch dạy thành công từ hệ thống IMS (2 tuần)!",
                    title="Thành công",
                    severity="information",
                    timeout=5,
                )
                # Tự động nạp lại file và làm mới bảng dữ liệu
                self.load_schedule_data()
            else:
                err_content = (
                    stderr_bytes.decode(errors="replace").strip()
                    or stdout_bytes.decode(errors="replace").strip()
                )
                last_line = (
                    err_content.splitlines()[-1]
                    if err_content
                    else f"Mã thoát {proc.returncode}"
                )
                self.notify(
                    f"Cập nhật thất bại: {last_line}",
                    title="Lỗi cập nhật",
                    severity="error",
                    timeout=7,
                )
                hotkeys_bar.update(
                    Text.from_markup(
                        f"[bold red]❌ Cập nhật thất bại (Mã lỗi: {proc.returncode}) - Nhấn [u] để thử lại[/bold red]"
                    )
                )
        except Exception as exc:
            self.notify(
                f"Lỗi khi thực thi script: {exc}",
                title="Lỗi ngoại lệ",
                severity="error",
                timeout=7,
            )
            hotkeys_bar.update(
                Text.from_markup(f"[bold red]❌ Lỗi ngoại lệ: {exc}[/bold red]")
            )
        finally:
            table.loading = False
            self.is_updating = False

    def action_quit_app(self) -> None:
        """Phím Q: Thoát ứng dụng an toàn."""
        self.exit()


if __name__ == "__main__":
    app = IMSApp()
    app.run()
