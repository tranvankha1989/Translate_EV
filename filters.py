"""Bỏ qua nội dung không nên gửi đi dịch: mật khẩu, token, URL, đường dẫn, email."""
import ctypes
import os
import re
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardOwner.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

# Trình quản lý mật khẩu (KeePass, Bitwarden...) gắn các định dạng này vào clipboard
# để báo "đừng theo dõi / đừng lưu lịch sử". Windows cũng dùng chúng cho Win+V.
_PRIVATE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")
_HISTORY_FORMAT = "CanIncludeInClipboardHistory"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_URL = re.compile(r"(?:[a-z][a-z0-9+.-]*://|www\.)\S+", re.I)
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_WINDOWS_PATH = re.compile(r'"?(?:[a-z]:[\\/]|\\\\[^\\\s])', re.I)
_UNIX_PATH = re.compile(r"~?/\S+")
_TOKEN = re.compile(r"[A-Za-z0-9_\-+/=.:]{16,}")


def looks_sensitive(text: str) -> bool:
    """Văn bản trông như mật khẩu, mã token, URL, đường dẫn hoặc email (không phải câu chữ)."""
    s = text.strip()
    if not s or "\n" in s:
        return False
    if _WINDOWS_PATH.match(s):
        return True  # đường dẫn Windows có thể chứa dấu cách
    if any(ch.isspace() for ch in s):
        return False  # có dấu cách: là câu chữ bình thường
    if _URL.fullmatch(s) or _EMAIL.fullmatch(s) or _UNIX_PATH.fullmatch(s):
        return True
    if len(s) >= 8:
        kinds = sum((
            any(ch.islower() for ch in s),
            any(ch.isupper() for ch in s),
            any(ch.isdigit() for ch in s),
            any(not ch.isalnum() for ch in s),
        ))
        if kinds >= 3:
            return True  # kiểu mật khẩu: trộn chữ hoa, chữ thường, số, ký tự đặc biệt
    return bool(_TOKEN.fullmatch(s)) and any(ch.isdigit() for ch in s)


def normalize_app_names(names) -> list[str]:
    """"KeePass" / "C:\\...\\KeePass.exe" -> "keepass.exe"; bỏ dòng trống và trùng lặp."""
    result = []
    for name in names:
        name = os.path.basename(str(name).strip().strip('"')).lower()
        if not name:
            continue
        if not name.endswith(".exe"):
            name += ".exe"
        if name not in result:
            result.append(name)
    return result


def _read_dword_format(fmt) -> int | None:
    for _ in range(10):
        if user32.OpenClipboard(None):
            try:
                handle = user32.GetClipboardData(fmt)
                if not handle or kernel32.GlobalSize(handle) < 4:
                    return None
                address = kernel32.GlobalLock(handle)
                if not address:
                    return None
                try:
                    return ctypes.c_uint32.from_address(address).value
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        time.sleep(0.01)
    return None


def clipboard_is_private() -> bool:
    """Clipboard hiện tại được đánh dấu riêng tư (thường do trình quản lý mật khẩu)."""
    for name in _PRIVATE_FORMATS:
        fmt = user32.RegisterClipboardFormatW(name)
        if fmt and user32.IsClipboardFormatAvailable(fmt):
            return True
    fmt = user32.RegisterClipboardFormatW(_HISTORY_FORMAT)
    if fmt and user32.IsClipboardFormatAvailable(fmt):
        return _read_dword_format(fmt) != 1  # 0 = không cho lưu; không đọc được thì coi là riêng tư
    return False


def clipboard_owner_app() -> str | None:
    """Tên file exe của app vừa copy, ví dụ "code.exe"; None nếu không xác định được."""
    hwnd = user32.GetClipboardOwner()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value).lower()
        return None
    finally:
        kernel32.CloseHandle(process)


def skip_reason(text, *, skip_private=True, skip_sensitive=True, excluded_apps=(), manual=False):
    """Lý do không dịch (để hiện trên tooltip), hoặc None nếu được dịch.

    Dịch thủ công bằng phím tắt vẫn tôn trọng cờ riêng tư của trình quản lý mật khẩu,
    nhưng bỏ qua danh sách app loại trừ và bộ lọc "giống mật khẩu".
    """
    if skip_private and clipboard_is_private():
        return "nội dung riêng tư"
    if manual:
        return None
    excluded = set(normalize_app_names(excluded_apps))
    if excluded:
        owner = clipboard_owner_app()
        if owner and owner in excluded:
            return f"copy từ {owner}"
    if skip_sensitive and looks_sensitive(text):
        return "giống mật khẩu, URL hoặc đường dẫn"
    return None
