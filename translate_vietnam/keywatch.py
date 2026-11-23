"""Nhận biết lúc người dùng nhấn Ctrl+X (cắt) để sửa chính tả đoạn vừa cắt.

Dùng Raw Input của Windows ở chế độ chỉ nghe (RIDEV_INPUTSINK): app nhận một bản sao
của phím vừa nhấn, không chặn và không làm chậm bàn phím, không cần quyền quản trị.
App chỉ ghi lại thời điểm nhấn Ctrl+X và Ctrl+C, không lưu hay gửi phím nào khác,
và chỉ đăng ký nghe khi tính năng sửa chính tả đang bật.
"""
import ctypes
import os
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_INPUT = 0x00FF
RIDEV_REMOVE = 0x00000001
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEKEYBOARD = 1
RI_KEY_BREAK = 0x01
HID_USAGE_PAGE_GENERIC, HID_USAGE_KEYBOARD = 0x01, 0x06
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
VK_C, VK_X, VK_INSERT = 0x43, 0x58, 0x2D
CUT_WINDOW = 2.0  # giây: clipboard đổi trong khoảng này sau Ctrl+X mới tính là cắt


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT), ("Reserved", wintypes.USHORT),
                ("VKey", wintypes.USHORT), ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("keyboard", RAWKEYBOARD)]


user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]
user32.GetRawInputData.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

_lock = threading.Lock()
_last_cut = float("-inf")
_last_copy = float("-inf")
_OWN_PID = os.getpid()


def classify_key(vk, *, ctrl, alt=False, shift=False, win=False):
    """Một lần nhấn phím: "cut" (Ctrl+X), "copy" (Ctrl+C, Ctrl+Insert) hoặc None."""
    if not ctrl or alt or win:
        return None  # Ctrl+Alt là AltGr trên một số bàn phím: không phải lệnh cắt
    if vk == VK_X and not shift:
        return "cut"
    if vk in (VK_C, VK_INSERT):
        return "copy"
    return None


def record(kind, now=None):
    global _last_cut, _last_copy
    now = time.monotonic() if now is None else now
    with _lock:
        if kind == "cut":
            _last_cut = now
        elif kind == "copy":
            _last_copy = now


def cut_pending(now=None) -> bool:
    """Lần nhấn gần nhất là Ctrl+X và mới trong CUT_WINDOW giây."""
    now = time.monotonic() if now is None else now
    with _lock:
        return _last_cut >= _last_copy and 0 <= now - _last_cut <= CUT_WINDOW


def consume_cut():
    """Đã nhận lần cắt này: các lần clipboard đổi sau đó là copy bình thường."""
    global _last_cut
    with _lock:
        _last_cut = float("-inf")


def read_key_event(lparam):
    """Đọc WM_INPUT: (virtual key, đang nhấn xuống) hoặc None nếu không phải phím."""
    header_size = ctypes.sizeof(RAWINPUTHEADER)
    size = wintypes.UINT(0)
    if user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), header_size) != 0 or not size.value:
        return None
    buffer = ctypes.create_string_buffer(max(size.value, ctypes.sizeof(RAWINPUT)))
    if user32.GetRawInputData(lparam, RID_INPUT, buffer, ctypes.byref(size), header_size) in (0, 0xFFFFFFFF):
        return None
    raw = RAWINPUT.from_buffer(buffer)
    if raw.header.dwType != RIM_TYPEKEYBOARD:
        return None
    return raw.keyboard.VKey, not raw.keyboard.Flags & RI_KEY_BREAK


def _down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def _foreground_is_own_app():
    """Cắt chữ trong chính Bảng điều khiển (ví dụ ô API key) thì không sửa."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == _OWN_PID


def handle_raw_input(lparam):
    """Gọi trong window procedure khi nhận WM_INPUT. Không bao giờ ném lỗi."""
    try:
        event = read_key_event(lparam)
        if not event or not event[1]:
            return
        kind = classify_key(event[0], ctrl=_down(VK_CONTROL), alt=_down(VK_MENU),
                            shift=_down(VK_SHIFT), win=_down(VK_LWIN) or _down(VK_RWIN))
        if kind and not _foreground_is_own_app():
            record(kind)
    except Exception:
        pass


def set_listening(hwnd, enabled) -> bool:
    """Bật / tắt nghe bàn phím cho cửa sổ ẩn hwnd. Gọi trên luồng sở hữu cửa sổ đó."""
    device = RAWINPUTDEVICE(HID_USAGE_PAGE_GENERIC, HID_USAGE_KEYBOARD,
                            RIDEV_INPUTSINK if enabled else RIDEV_REMOVE, hwnd if enabled else None)
    ok = bool(user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(RAWINPUTDEVICE)))
    if not enabled:
        consume_cut()
    return ok
