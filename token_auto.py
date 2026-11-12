import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
import requests

# Thay đổi các thông tin này theo đúng tài khoản và API của bạn
LOGIN_URL = "https://ims.theforumcenter.com/api/auth/login"
TIMETABLE_URL = "https://ims.theforumcenter.com/api/classes/timetable/weekly"
DUAL_TICK_URL = "https://ims.theforumcenter.com/api/attendance/dual-tick/pending"
USERNAME = os.environ.get("IMS_USERNAME", "thevietttt@gmail.com")
PASSWORD = os.environ.get("IMS_PASSWORD", "")


def get_new_token() -> str | None:
    """Hàm tự động đăng nhập và lấy token mới."""
    if not PASSWORD:
        print("Lỗi: chưa đặt biến môi trường IMS_PASSWORD", file=sys.stderr)
        return None
    payload = {
        "email": USERNAME,
        "password": PASSWORD
    }
    
    try:
        response = requests.post(LOGIN_URL, json=payload, timeout=15)
        if response.status_code == 200:
            data = response.json()
            token = data.get("token") 
            print("Đã lấy được token mới!")
            print(f"Token vừa lấy được là: {token}")
            return token
        else:
            print("Đăng nhập thất bại:", response.text)
            return None
    except Exception as e:
        print(f"Lỗi kết nối khi đăng nhập: {e}")
        return None


def fetch_weekly_data(token: str, week_start: str) -> list[dict] | None:
    """
    Gửi request lấy dữ liệu thời khóa biểu cho một tuần cụ thể qua tham số week_start (YYYY-MM-DD).
    Sử dụng Bearer token trong headers và cookie mô phỏng.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    }
    
    params = {
        "week_start": week_start
    }
    
    cookies = {
        "token": token
    }
    
    try:
        response = requests.get(TIMETABLE_URL, headers=headers, params=params, cookies=cookies, timeout=20)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                print(f"  [OK] Tải thành công dữ liệu tuần {week_start} ({len(data)} ca dạy).")
                return data
            elif isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                print(f"  [OK] Tải thành công dữ liệu tuần {week_start} ({len(data['data'])} ca dạy).")
                return data["data"]
            else:
                print(f"  [Cảnh báo] Phản hồi tuần {week_start} không phải danh sách hợp lệ.")
                return []
        elif response.status_code == 401:
            print(f"  [LỖI 401] Token đã hết hạn khi tải tuần {week_start}!")
            return None
        else:
            print(f"  [LỖI {response.status_code}] Không thể tải tuần {week_start}: {response.text}")
            return None
    except Exception as e:
        print(f"  [LỖI KẾT NỐI] Khi tải dữ liệu thời khóa biểu tuần {week_start}: {e}")
        return None


def resolve_start_monday(start_date_input: str | date | None = None) -> date:
    """
    Xác định ngày Thứ Hai đầu tuần.
    Nếu người dùng truyền ngày (chuỗi YYYY-MM-DD hoặc date), tự động căn chỉnh về Thứ Hai của tuần đó.
    Nếu không truyền, lấy Thứ Hai của tuần hiện tại.
    """
    if not start_date_input:
        today = date.today()
        return today - timedelta(days=today.weekday())
    if isinstance(start_date_input, date):
        return start_date_input - timedelta(days=start_date_input.weekday())
    
    parsed_d = datetime.strptime(str(start_date_input).strip(), "%Y-%m-%d").date()
    return parsed_d - timedelta(days=parsed_d.weekday())


def fetch_multi_weeks(token: str, start_monday: date, num_weeks: int = 2) -> tuple[list[dict] | None, list[tuple[str, int]]]:
    """
    Nhận vào ngày bắt đầu của tuần 1, tự động cộng 7 ngày (timedelta(days=7)) cho các tuần tiếp theo.
    Gửi request lấy dữ liệu của tất cả các tuần bằng Bearer Token hiện có.
    Gộp (concatenate) danh sách kết quả lại.
    Trả về (all_sessions, week_counts).
    """
    all_sessions: list[dict] = []
    week_counts: list[tuple[str, int]] = []

    for i in range(num_weeks):
        # Tự động cộng 7 ngày (timedelta(days=7)) để tính ra week_start tuần tiếp theo
        week_start_date = start_monday + timedelta(days=7 * i)
        week_start_str = week_start_date.isoformat()
        
        print(f"Đang gửi request lấy dữ liệu tuần {i + 1}/{num_weeks} (week_start={week_start_str})...")
        week_data = fetch_weekly_data(token, week_start_str)
        if week_data is None:
            print(f"Lỗi: Không thể tải dữ liệu tuần {week_start_str} từ IMS!", file=sys.stderr)
            return None, week_counts
        
        count = len(week_data)
        week_counts.append((week_start_str, count))
        # Gộp (concatenate) danh sách kết quả
        all_sessions.extend(week_data)

    return all_sessions, week_counts


def print_fetch_summary(week_counts: list[tuple[str, int]], total_count: int) -> None:
    """In thông báo số lượng ca dạy thu thập được của từng tuần và tổng số sau khi gộp."""
    print("\n" + "=" * 58)
    print("      THỐNG KÊ THU THẬP DỮ LIỆU THỜI KHÓA BIỂU")
    print("=" * 58)
    for idx, (w_str, count) in enumerate(week_counts, 1):
        print(f"  • Tuần {idx} (week_start = {w_str}): {count} ca dạy")
    print("-" * 58)
    print(f"  => TỔNG SỐ CA DẠY SAU KHI GỘP ({len(week_counts)} tuần): {total_count} ca dạy")
    print("=" * 58 + "\n")


def fetch_dual_tick_data(token: str, from_date: str | None = None, to_date: str | None = None, page_size: int = 50) -> list[dict]:
    """
    Hàm gọi API Dual Tick lấy danh sách trạng thái chấm công bằng vòng lặp phân trang (Pagination Loop).
    - Tham số phân trang: 'limit' (hệ thống IMS backend sử dụng 'limit', tối đa 50 bản ghi/lần).
    - Để tránh hiện tượng trượt bản ghi do backend không sắp xếp ổn định khi truy vấn dải ngày dài cả tuần,
      hàm sẽ tự động phân trang theo từng ngày nếu có khoảng thời gian from_date -> to_date.
    - Kết thúc vòng lặp mỗi ngày khi trang hiện tại rỗng hoặc ít hơn kích thước trang (page_size).
    - Gộp tất cả kết quả từ các trang vào một danh sách duy nhất và loại bỏ trùng lặp nếu có.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    }
    cookies = {
        "token": token
    }

    all_items = []
    total_pages = 0
    seen_ids = set()

    # Xác định danh sách các ngày cần truy vấn
    date_list = []
    if from_date and to_date:
        try:
            d_start = datetime.strptime(from_date, "%Y-%m-%d").date()
            d_end = datetime.strptime(to_date, "%Y-%m-%d").date()
            curr = d_start
            while curr <= d_end:
                date_list.append(curr.isoformat())
                curr += timedelta(days=1)
        except Exception:
            date_list = [None]
    else:
        date_list = [None]

    for d in date_list:
        page = 1
        while True:
            params = {
                "page": page,
                "limit": page_size
            }
            if d:
                params["dateFrom"] = d
                params["fromDate"] = d
                params["dateTo"] = d
                params["toDate"] = d
            elif from_date or to_date:
                if from_date:
                    params["dateFrom"] = from_date
                    params["fromDate"] = from_date
                if to_date:
                    params["dateTo"] = to_date
                    params["toDate"] = to_date

            try:
                response = requests.get(DUAL_TICK_URL, headers=headers, params=params, cookies=cookies, timeout=20)
                if response.status_code == 200:
                    res_data = response.json()
                    if isinstance(res_data, dict):
                        items = res_data.get("data", [])
                    elif isinstance(res_data, list):
                        items = res_data
                    else:
                        items = []

                    # Nếu dữ liệu trang hiện tại rỗng, kết thúc vòng lặp của ngày
                    if not items:
                        break

                    for item in items:
                        item_id = item.get("id") or item.get("class_session_id")
                        if item_id not in seen_ids:
                            seen_ids.add(item_id)
                            all_items.append(item)

                    total_pages += 1

                    # Nếu dữ liệu trang hiện tại ít hơn kích thước trang, kết thúc vòng lặp của ngày
                    if len(items) < page_size:
                        break

                    page += 1
                else:
                    print(f"[Cảnh báo] Lỗi {response.status_code} khi tải dữ liệu Dual Tick ngày {d or 'all'} trang {page}: {response.text}")
                    break
            except Exception as e:
                print(f"[Cảnh báo] Lỗi kết nối khi tải dữ liệu Dual Tick ngày {d or 'all'} trang {page}: {e}")
                break

    print(f"Đã thu thập thành công {len(all_items)} bản ghi Dual Tick từ {total_pages} trang.")
    return all_items


def update_schedule_data(token: str | None = None, start_date: str | date | None = None, num_weeks: int = 1) -> list[dict] | None:
    """
    Quy trình cập nhật thời khóa biểu và trạng thái chấm công:
    1. Lấy Bearer Token mới (hoặc tái sử dụng token được truyền vào).
    2. Xác định ngày bắt đầu tuần 1 và các tuần tiếp theo (cộng timedelta(days=7)).
    3. Gửi request tải dữ liệu các tuần bằng Bearer Token.
    4. Gộp (concatenate) kết quả các tuần lại.
    5. In thống kê số lượng ca dạy từng tuần và tổng số sau khi gộp.
    6. Thu thập Dual Tick cho toàn bộ dải ngày và đối chiếu.
    7. Lưu đè vào file weekly.json và lưu bản sao archive.
    """
    # 1. Xác thực Bearer Token
    current_token = token
    if not current_token:
        current_token = get_new_token()
    else:
        print("Sử dụng Bearer Token hiện có...")

    if not current_token:
        print("Lỗi: Không lấy được token đăng nhập!", file=sys.stderr)
        return None

    # 2. Xác định ngày bắt đầu tuần 1 và ngày kết thúc tuần cuối
    monday = resolve_start_monday(start_date)
    end_sunday = monday + timedelta(days=7 * num_weeks - 1)
    from_date = monday.isoformat()
    to_date = end_sunday.isoformat()

    # 3 & 4. Lấy dữ liệu các tuần và gộp (concatenate) lại
    all_sessions, week_counts = fetch_multi_weeks(current_token, monday, num_weeks=num_weeks)
    if all_sessions is None:
        return None

    # 5. In thống kê số ca dạy từng tuần và tổng số sau khi gộp
    print_fetch_summary(week_counts, len(all_sessions))

    # 6. Tải dữ liệu Dual Tick từ API và gắn vào all_sessions
    print(f"Đang tải dữ liệu Dual Tick từ {from_date} đến {to_date}...")
    dual_tick_items = fetch_dual_tick_data(current_token, from_date, to_date)
    if dual_tick_items:
        dual_tick_map = {}
        for item in dual_tick_items:
            session_obj = item.get("session")
            session_id = None
            if isinstance(session_obj, dict):
                session_id = session_obj.get("id")
            if session_id is None:
                session_id = item.get("class_session_id")
            status = item.get("status")
            if session_id is not None and status:
                dual_tick_map[session_id] = status

        matched_count = 0
        for session in all_sessions:
            sid = session.get("id")
            if sid in dual_tick_map:
                session["dual_tick_status"] = dual_tick_map[sid]
                matched_count += 1
        print(f"Đã đối chiếu và cập nhật trạng thái Dual Tick cho {matched_count}/{len(dual_tick_map)} buổi học!")
    else:
        print("Bỏ qua cập nhật Dual Tick (không có dữ liệu hoặc API không phản hồi thành công).")

    # 7. Lưu đè vào file weekly.json
    script_dir = Path(__file__).resolve().parent
    weekly_file = script_dir / "weekly.json"
    if num_weeks == 1:
        archive_file = script_dir / f"weekly_data_{from_date}.json"
    else:
        archive_file = script_dir / f"weekly_data_{from_date}_to_{to_date}.json"

    with open(weekly_file, "w", encoding="utf-8") as file:
        json.dump(all_sessions, file, ensure_ascii=False, indent=4)
    print(f"Đã lưu đè thành công {len(all_sessions)} ca dạy vào file '{weekly_file.name}'!")

    # Đồng thời lưu một bản lưu trữ
    with open(archive_file, "w", encoding="utf-8") as file:
        json.dump(all_sessions, file, ensure_ascii=False, indent=4)

    return all_sessions


def parse_args():
    """Phân tích các tham số dòng lệnh một cách linh hoạt, tương thích ngược."""
    parser = argparse.ArgumentParser(
        description="Tự động cập nhật thời khóa biểu IMS và đồng bộ chấm công Dual Tick.",
        add_help=False
    )
    parser.add_argument("date_pos", nargs="?", default=None, help="Ngày bắt đầu tuần 1 (YYYY-MM-DD)")
    parser.add_argument("--date", "-d", dest="date_flag", default=None, help="Ngày bắt đầu tuần 1 (YYYY-MM-DD)")
    parser.add_argument("--week-start", dest="week_start_flag", default=None, help="Ngày bắt đầu tuần 1 (YYYY-MM-DD)")
    parser.add_argument("--weeks", "-w", type=int, default=None, help="Số tuần cần thu thập dữ liệu (mặc định: 1, ví dụ: --weeks 2)")
    parser.add_argument("--token", "-t", default=None, help="Bearer token IMS có sẵn (nếu có)")
    parser.add_argument("--help", "-h", action="store_true", help="Hiển thị hướng dẫn sử dụng")

    args, unknown = parser.parse_known_args()
    return args, unknown


# --- LUỒNG CHẠY CHÍNH TỰ ĐỘNG ---
if __name__ == "__main__":
    args, unknown = parse_args()

    if args.help:
        print("Sử dụng: python token_auto.py [YYYY-MM-DD] [--weeks N] [--token TOKEN]")
        print("Các tham số hỗ trợ:")
        print("  [YYYY-MM-DD]          : Ngày bắt đầu tuần 1 (Mặc định: Thứ Hai tuần hiện tại)")
        print("  --weeks, -w <N>       : Số tuần cần tải dữ liệu (VD: --weeks 2). Mặc định: 1")
        print("  --token, -t <TOKEN>   : Sử dụng Bearer Token có sẵn thay vì đăng nhập lại")
        print("  --date, -d <DATE>     : Chỉ định ngày bắt đầu tuần 1")
        print("  --week-start <DATE>   : Chỉ định ngày bắt đầu tuần 1")
        sys.exit(0)

    # 1. Xác định số tuần (weeks)
    num_weeks = args.weeks
    if num_weeks is None:
        env_weeks = os.environ.get("IMS_WEEKS")
        if env_weeks and env_weeks.isdigit():
            num_weeks = int(env_weeks)
        else:
            # Kiểm tra xem trong unknown args có số nguyên dương không (VD: token_auto.py 2026-09-28 2)
            for u in unknown:
                if u.isdigit() and int(u) > 0:
                    num_weeks = int(u)
                    break
    if num_weeks is None or num_weeks < 1:
        num_weeks = 1

    # 2. Xác định ngày bắt đầu tuần 1
    start_date = args.week_start_flag or args.date_flag
    if not start_date and args.date_pos and re.match(r"^\d{4}-\d{2}-\d{2}$", args.date_pos):
        start_date = args.date_pos
    if not start_date:
        for u in unknown:
            if re.match(r"^\d{4}-\d{2}-\d{2}$", u):
                start_date = u
                break

    # 3. Lấy token (nếu có truyền qua tham số hoặc biến môi trường)
    bearer_token = args.token or os.environ.get("IMS_TOKEN")

    sessions = update_schedule_data(token=bearer_token, start_date=start_date, num_weeks=num_weeks)
    if sessions is None:
        sys.exit(1)
    sys.exit(0)