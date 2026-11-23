"""Translate Vietnam: dịch clipboard Việt – Anh và sửa chính tả, chạy nền trên Windows.

Các phần chính:
    app          chạy nền: icon khay, theo dõi clipboard, chế độ dịch, cài đặt / cập nhật
    panel        Bảng điều khiển (tkinter), Hướng dẫn, Giới thiệu
    translator   dịch bằng Gemini theo lĩnh vực, dự phòng Google Dịch và MyMemory
    proofreader  sửa chính tả, câu văn bằng Gemini (Ctrl+X rồi Ctrl+V)
    keywatch     nhận biết phím Ctrl+X (Raw Input, chỉ nghe, không chặn bàn phím)
    filters      bỏ qua nội dung riêng tư: mật khẩu, token, app loại trừ
    hotkeys      phím tắt toàn cục (F8 / F9 / F10)
    config       cài đặt người dùng, mã hóa API key bằng Windows DPAPI
    icons        icon khay và icon app
    installer    cài vào Windows, lối tắt, gỡ cài đặt
    updater      tự cập nhật từ GitHub Releases
"""
