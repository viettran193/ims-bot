#!/usr/bin/env bash
set -eo pipefail

# Chuyển vào thư mục chứa script để đảm bảo đường dẫn tương đối luôn chính xác
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

WEEKLY_FILE="${SCRIPT_DIR}/weekly.json"

echo "=========================================================="
echo "    IMS SCHEDULE AUTO-UPDATER & PARSER"
echo "=========================================================="

# -----------------------------------------------------------------------------
# Bước 1: Kích hoạt môi trường ảo .venv (nếu có)
# -----------------------------------------------------------------------------
echo ""
echo "[Bước 1/3] Kích hoạt môi trường ảo (.venv)..."

if [ -d "${SCRIPT_DIR}/.venv" ] && [ -f "${SCRIPT_DIR}/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "${SCRIPT_DIR}/.venv/bin/activate"
    echo "[OK] Đã kích hoạt môi trường ảo tại: .venv"
elif [ -d "${SCRIPT_DIR}/venv" ] && [ -f "${SCRIPT_DIR}/venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "${SCRIPT_DIR}/venv/bin/activate"
    echo "[OK] Đã kích hoạt môi trường ảo tại: venv"
else
    echo "[INFO] Không tìm thấy thư mục .venv, tiếp tục với Python hệ thống."
fi

# Kiểm tra lệnh python sẵn sàng
if command -v python &>/dev/null; then
    PY_CMD="python"
elif command -v python3 &>/dev/null; then
    PY_CMD="python3"
else
    echo "[LỖI] Không tìm thấy lệnh 'python' hoặc 'python3' trên hệ thống!" >&2
    exit 1
fi

PYTHON_VERSION=$("$PY_CMD" --version 2>&1)
echo "      Python đang sử dụng: ${PYTHON_VERSION} ($(command -v "$PY_CMD"))"

# -----------------------------------------------------------------------------
# Bước 2: Chạy lệnh python token_auto.py để tải file weekly.json mới nhất
# -----------------------------------------------------------------------------
echo ""
echo "[Bước 2/3] Chạy python token_auto.py để tự động đăng nhập và tải dữ liệu..."

if ! "$PY_CMD" token_auto.py "$@"; then
    echo ""
    echo "[LỖI] Quá trình chạy token_auto.py thất bại!" >&2
    echo "      Vui lòng kiểm tra lại kết nối mạng hoặc thông tin đăng nhập trong token_auto.py." >&2
    exit 1
fi
echo "[OK] token_auto.py đã thực thi thành công."

# -----------------------------------------------------------------------------
# Bước 3: Kiểm tra nếu file weekly.json đã tải thành công thì chạy parse_schedule.py
# -----------------------------------------------------------------------------
echo ""
echo "[Bước 3/3] Kiểm tra file weekly.json và phân tích dữ liệu lịch..."

# 3.1 Kiểm tra file tồn tại
if [ ! -f "${WEEKLY_FILE}" ]; then
    echo "[LỖI] File weekly.json không tồn tại sau khi chạy token_auto.py!" >&2
    exit 1
fi

# 3.2 Kiểm tra file không rỗng
if [ ! -s "${WEEKLY_FILE}" ]; then
    echo "[LỖI] File weekly.json bị rỗng (0 bytes)!" >&2
    exit 1
fi

# 3.3 Kiểm tra cấu trúc JSON hợp lệ
if ! "$PY_CMD" -c "import json; data = json.load(open('${WEEKLY_FILE}')); assert isinstance(data, list), 'Data is not a list'; print(f'      Đã lưu và xác thực thành công {len(data)} ca học/dạy.')" 2>/dev/null; then
    echo "[LỖI] File weekly.json không đúng cấu trúc danh sách ca học JSON hợp lệ!" >&2
    exit 1
fi

echo "[OK] File weekly.json đã tải và xác thực tính hợp lệ thành công!"

# Kiểm tra nếu script được gọi gián tiếp từ parse_schedule.py (--update)
if [ "${IMS_INVOKED_FROM_PARSE:-0}" = "1" ]; then
    echo "      [INFO] Script được gọi từ parse_schedule.py --update. Đã hoàn thành cập nhật dữ liệu."
else
    # Lọc bỏ cờ --update và các cờ --weeks khỏi tham số truyền vào parse_schedule.py
    PARSE_ARGS=()
    skip_next=0
    for arg in "$@"; do
        if [ "$skip_next" -eq 1 ]; then
            skip_next=0
            continue
        fi
        if [ "$arg" = "--update" ]; then
            continue
        fi
        if [ "$arg" = "--weeks" ] || [ "$arg" = "-w" ]; then
            skip_next=1
            continue
        fi
        if [[ "$arg" =~ ^--weeks= ]] || [[ "$arg" =~ ^-w= ]]; then
            continue
        fi
        PARSE_ARGS+=("$arg")
    done

    echo ""
    echo "----------------------------------------------------------"
    echo "Bắt đầu xử lý dữ liệu với parse_schedule.py:"
    echo "----------------------------------------------------------"
    "$PY_CMD" parse_schedule.py "${PARSE_ARGS[@]}"
fi

echo ""
echo "----------------------------------------------------------"
echo "[HOÀN THÀNH] Toàn bộ quy trình cập nhật và xử lý lịch đã kết thúc thành công!"
