# IMS Schedule Parser & Updater

Tool tra cứu lịch dạy giáo viên từ hệ thống IMS The Forum và tự động cập nhật dữ liệu hàng tuần. Hỗ trợ tìm kiếm thông minh không cần gõ dấu tiếng Việt.

---

## 1. Hướng dẫn sử dụng (`parse_schedule.py`)

### Tra cứu cơ bản (Hỗ trợ tiếng Việt không dấu)
Bạn không cần bật bộ gõ tiếng Việt, có thể gõ trực tiếp tên không dấu:

```bash
# Tra cứu lịch hôm nay của giáo viên (VD: Leon, Vũ Hải, Thanh Trang, Khải)
python3 parse_schedule.py Leon
python3 parse_schedule.py "Vu Hai"
python3 parse_schedule.py "Thanh Trang"
python3 parse_schedule.py Khai

# Hoặc dùng cờ --teacher (-t)
python3 parse_schedule.py -t "Dinh Sy Manh Linh"
```

### Xem toàn bộ lịch trong tuần (`--week`)
```bash
python3 parse_schedule.py "Vu Hai" --week
python3 parse_schedule.py "Thanh Trang" --week
```

### Tra cứu theo ngày cụ thể (`--date YYYY-MM-DD`)
Mặc định tool lấy ngày hôm nay. Bạn có thể chỉ định ngày:
```bash
python3 parse_schedule.py "Leon" --date 2026-09-30
```

### Loại trừ các ca dạy thay (`--no-cover`)
Mặc định tool sẽ tìm cả ca dạy chính và ca dạy thay (cover). Nếu chỉ muốn xem ca dạy chính:
```bash
python3 parse_schedule.py "Vu Hai" --no-cover
```

---

## 2. Cách cập nhật `weekly.json`

Hệ thống hỗ trợ lấy thời khóa biểu 1 tuần hoặc **nhiều tuần liên tiếp** (ví dụ 2 tuần: tuần hiện tại và tuần tiếp theo tự động cộng `timedelta(days=7)`):

### Cách 1: Sử dụng script chuyên dụng `fetch_multi_weeks.py` (Mặc định 2 tuần)
Script chuyên dụng để lấy lịch 2 tuần (hoặc nhiều hơn), tự động cộng 7 ngày cho tuần kế tiếp, gộp danh sách và lưu đè vào `weekly.json`:
```bash
# Tải 2 tuần mặc định (tuần hiện tại + tuần tiếp theo)
python3 fetch_multi_weeks.py

# Chỉ định ngày bắt đầu tuần 1 (VD: 2026-09-28)
python3 fetch_multi_weeks.py 2026-09-28

# Tùy chỉnh số lượng tuần (VD: 3 tuần) hoặc dùng Bearer Token có sẵn
python3 fetch_multi_weeks.py 2026-09-28 --weeks 3 --token "YOUR_BEARER_TOKEN"
```

### Cách 2: Chạy script `update_schedule.sh` (Hỗ trợ cờ `--weeks 2`)
Script tự động kích hoạt môi trường ảo `.venv`, gọi `token_auto.py` để đăng nhập/tải dữ liệu, gộp kết quả và gọi `parse_schedule.py`:
```bash
# Tải 1 tuần mặc định và phân tích lịch
./update_schedule.sh

# Tải 2 tuần liên tiếp bằng tham số --weeks 2
./update_schedule.sh --weeks 2

# Tải 2 tuần chỉ định ngày bắt đầu và tra cứu giáo viên
./update_schedule.sh --weeks 2 2026-09-28 "Vu Hai"
```

### Cách 3: Chạy trực tiếp `token_auto.py`
Nếu bạn chỉ muốn đăng nhập và tải `weekly.json` (hỗ trợ `--weeks` và `--token`):
```bash
# Tải 1 tuần mặc định
python3 token_auto.py

# Tải 2 tuần liên tiếp (tuần 1 và tuần 2)
python3 token_auto.py --weeks 2

# Chỉ định ngày tuần 1 và dùng token có sẵn
python3 token_auto.py 2026-09-28 --weeks 2 --token "YOUR_TOKEN"
```

### Cách 4: Cập nhật qua `parse_schedule.py --update`
```bash
# Cập nhật 2 tuần rồi tra cứu lịch giáo viên
python3 parse_schedule.py --update --weeks 2 "Vu Hai"
```

---

## 3. Quản lý tài khoản đăng nhập (`token_auto.py`)

Hệ thống đã chuyển sang cơ chế tự động đăng nhập bằng email & password để lấy token JWT mới nhất mỗi khi tải dữ liệu, thay thế hoàn toàn việc sao chép token tĩnh thủ công.

Nếu cần đổi thông tin đăng nhập IMS, chỉnh sửa trực tiếp trong file [`token_auto.py`](file:///home/admin1/ims-bot/token_auto.py):
```python
USERNAME = os.environ.get("IMS_USERNAME", "thevietttt@gmail.com")
PASSWORD = os.environ.get("IMS_PASSWORD", "")  # export IMS_PASSWORD=... truoc khi chay
```

---

## 4. Giao diện Terminal TUI (`tui_app.py`)

Khởi động giao diện quản lý lịch dạy trực quan qua terminal:

```bash
python3 tui_app.py
```

### Các tính năng và phím tắt chính:
- **Tối ưu không gian hiển thị**:
  - Loại bỏ widget Footer dưới đáy, giúp bảng `DataTable` chiếm trọn 100% chiều cao màn hình còn lại.
  - Header ngang chia 2 phần: Bên trái là khung tìm kiếm (cố định 100 cột vừa khớp mép bảng), bên phải là Badge tuần và phím tắt điều hướng.
- **Xem dữ liệu 2 tuần liên tiếp**:
  - Tự động nạp toàn bộ danh sách lớp của cả 2 tuần từ `weekly.json`.
  - Badge trạng thái tuần đổi chữ và màu sắc trực tiếp trên Header:
    * Phím `[1]`: Lọc chỉ xem **Tuần 1** (Badge `[Tuần: Tuần 1]` màu Cyan sáng).
    * Phím `[2]`: Lọc chỉ xem **Tuần 2** (Badge `[Tuần: Tuần 2]` màu Tím Magenta).
    * Phím `[3]` hoặc `[A]`: Xem **toàn bộ cả 2 tuần** (Badge `[Tuần: Cả 2 tuần]` màu Xanh lá Green).
- **Chế độ tìm kiếm Spotlight & Dimming (Phím `/`)**:
  - **Khi bấm `/`**: Ô nhập chuyển sang nền Amber sáng nổi bật, hiển thị Badge `[ TÌM KIẾM ]` đỏ rực, toàn bộ bảng dữ liệu tự động làm mờ (dim/muted) và con trỏ khối đặc `█` xuất hiện chờ lệnh.
  - **Khi bấm `Esc`**: Lập tức thoát chế độ tìm kiếm, trả nhãn về `[ BẢNG ]`, bảng sáng rõ trở lại với đầy đủ màu sắc ngữ nghĩa và con trỏ điều hướng mũi tên trở lại bảng. Nhấn `Esc` lần nữa để xóa từ khóa tìm kiếm.
- **Tối ưu Highlight dòng đang chọn**:
  - Chỉ đổi màu nền dòng chọn sang xanh than (`#2a3447`), giữ nguyên 100% màu chữ ngữ nghĩa gốc của từng cột (mã lớp trắng, chấm `● Hoàn thành` và `● Confirmed` giữ màu xanh lá đặc trưng).
- **Xem chi tiết (`Enter`)**: Mở modal xem toàn bộ thông tin chi tiết của buổi học được chọn.
- **Đồng bộ trực tiếp (`U`)**: Chạy ngầm `./update_schedule.sh --weeks 2` để cập nhật đồng thời 2 tuần và trạng thái chấm công Dual Tick.
- **Tải lại bảng (`R`)**: Nạp lại dữ liệu từ file `weekly.json`.
- **Thoát (`Q`)**.
- **Cụm phím tắt điều hướng nhanh trên Header**:
  `[/] Tìm  [1/2/3] Tuần  [u] Sync  [r] Tải lại  [q] Thoát`


