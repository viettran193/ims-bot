#!/usr/bin/env python3
"""
Script thu thập dữ liệu thời khóa biểu nhiều tuần từ IMS:
1. Nhận ngày bắt đầu của tuần 1 (Mặc định: Thứ Hai tuần hiện tại, hoặc truyền YYYY-MM-DD).
2. Tự động cộng 7 ngày (timedelta(days=7)) để tính ra week_start của tuần tiếp theo.
3. Gửi request lấy dữ liệu của cả 2 tuần bằng Bearer Token hiện có (hoặc tự động đăng nhập).
4. Gộp (concatenate) 2 danh sách kết quả lại và lưu đè vào file weekly.json.
5. In thông báo số lượng ca dạy thu thập được của từng tuần và tổng số sau khi gộp.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Đảm bảo import được module token_auto trong cùng thư mục
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from token_auto import update_schedule_data


def main():
    parser = argparse.ArgumentParser(
        description="Thu thập và gộp dữ liệu thời khóa biểu nhiều tuần từ hệ thống IMS (Mặc định: 2 tuần)."
    )
    parser.add_argument(
        "start_date",
        nargs="?",
        default=None,
        help="Ngày bắt đầu của tuần 1 (định dạng YYYY-MM-DD). Mặc định là Thứ Hai tuần hiện tại.",
    )
    parser.add_argument(
        "--weeks",
        "-w",
        type=int,
        default=2,
        help="Số lượng tuần cần thu thập dữ liệu (mặc định: 2).",
    )
    parser.add_argument(
        "--token",
        "-t",
        default=None,
        help="Bearer Token IMS có sẵn (nếu không cung cấp, script sẽ tự động đăng nhập để lấy token).",
    )

    args = parser.parse_args()

    # Kiểm tra tính hợp lệ của ngày nếu được truyền vào
    if args.start_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.start_date):
        print(f"[LỖI] Định dạng ngày '{args.start_date}' không hợp lệ. Vui lòng sử dụng định dạng YYYY-MM-DD.", file=sys.stderr)
        sys.exit(1)

    bearer_token = args.token or os.environ.get("IMS_TOKEN")

    print("==========================================================")
    print("      IMS MULTI-WEEK TIMETABLE COLLECTOR (2 WEEKS)")
    print("==========================================================")
    print(f"Số tuần cần lấy: {args.weeks}")
    if args.start_date:
        print(f"Ngày bắt đầu tuần 1 chỉ định: {args.start_date}")
    else:
        print("Ngày bắt đầu tuần 1: Tự động xác định Thứ Hai tuần hiện tại")
    print("----------------------------------------------------------")

    sessions = update_schedule_data(
        token=bearer_token,
        start_date=args.start_date,
        num_weeks=args.weeks,
    )

    if sessions is None:
        print("[LỖI] Thu thập dữ liệu thất bại!", file=sys.stderr)
        sys.exit(1)

    print("[HOÀN THÀNH] Đã thu thập và lưu đè dữ liệu vào weekly.json thành công!")
    sys.exit(0)


if __name__ == "__main__":
    main()
