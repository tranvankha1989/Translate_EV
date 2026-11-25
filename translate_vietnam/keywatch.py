"""Nhận biết lúc người dùng nhấn Ctrl+X (cắt) để sửa chính tả đoạn vừa cắt, và lúc giữ riêng
một phím (mặc định Shift, người dùng chọn được) để mở khung chọn vùng màn hình cần dịch.

Dùng Raw Input của Windows ở chế độ chỉ nghe (RIDEV_INPUTSINK): app nhận một bản sao
của phím vừa nhấn, không chặn và không làm chậm bàn phím, không cần quyền quản trị.
App chỉ ghi lại thời điểm nhấn Ctrl+X, Ctrl+C và lúc giữ / nhả phím giữ, không lưu hay gửi phím
nào khác, và chỉ đăng ký nghe khi tính năng sửa chính tả hoặc giữ phím để dịch đang bật.
Chuột chỉ được nghe trong lúc đang giữ phím (để Shift+lăn chuột, Shift+bấm chuột không mở khung).
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
RIM_TYPEMOUSE, RIM_TYPEKEYBOARD = 0, 1
RI_KEY_BREAK = 0x01
RI_KEY_E0 = 0x02  # phím mở rộng: Ctrl phải
SC_RSHIFT = 0x36  # mã quét của Shift phải
RI_MOUSE_PRESS = 0x0001 | 0x0004 | 0x0010 | 0x0040 | 0x0100  # nhấn nút trái / phải / giữa / phụ
RI_MOUSE_WHEEL_ANY = 0x0400 | 0x0800  # lăn chuột dọc / ngang
HID_USAGE_PAGE_GENERIC, HID_USAGE_MOUSE, HID_USAGE_KEYBOARD = 0x01, 0x02, 0x06
VK_LBUTTON, VK_RBUTTON, VK_MBUTTON = 0x01, 0x02, 0x04
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL = 0xA0, 0xA1, 0xA2, 0xA3
VK_C, VK_X, VK_INSERT = 0x43, 0x58, 0x2D
CUT_WINDOW = 2.0  # giây: clipboard đổi trong khoảng này sau Ctrl+X mới tính là cắt
HOLD_SECONDS = 0.6  # giữ riêng phím giữ lâu chừng này thì mở khung chọn vùng màn hình
# Phím giữ (tên giống config.HOLD_KEYS): virtual key, chỉ tính phím bên phải.
HOLD_KEY_VK = {"shift": (VK_SHIFT, False), "rshift": (VK_SHIFT, True),
               "ctrl": (VK_CONTROL, False), "rctrl": (VK_CONTROL, True)}
# Lúc mở khung: phím phải đang được nhấn, các phím không được nhấn cùng (ngoài Alt / Win / nút chuột).
_HOLD_STATE = {"shift": (VK_SHIFT, (VK_CONTROL,)), "rshift": (VK_RSHIFT, (VK_LSHIFT, VK_CONTROL)),
               "ctrl": (VK_CONTROL, (VK_SHIFT,)), "rctrl": (VK_RCONTROL, (VK_LCONTROL, VK_SHIFT))}


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT), ("Reserved", wintypes.USHORT),
                ("VKey", wintypes.USHORT), ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG)]


class RAWMOUSE(ctypes.Structure):
    # usFlags rồi tới một union 4 byte: có 2 byte đệm ở giữa.
    _fields_ = [("usFlags", wintypes.USHORT), ("_pad", wintypes.USHORT),
                ("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT),
                ("ulRawButtons", wintypes.ULONG), ("lLastX", wintypes.LONG), ("lLastY", wintypes.LONG),
                ("ulExtraInformation", wintypes.ULONG)]


class _RAWDATA(ctypes.Union):
    _fields_ = [("keyboard", RAWKEYBOARD), ("mouse", RAWMOUSE)]


class RAWINPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("header", RAWINPUTHEADER), ("data", _RAWDATA)]


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


def read_event(lparam):
    """Đọc WM_INPUT: ("key", virtual key, đang nhấn xuống, là phím bên phải), ("mouse", cờ nút chuột)
    hoặc None."""
    header_size = ctypes.sizeof(RAWINPUTHEADER)
    size = wintypes.UINT(0)
    if user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), header_size) != 0 or not size.value:
        return None
    buffer = ctypes.create_string_buffer(max(size.value, ctypes.sizeof(RAWINPUT)))
    if user32.GetRawInputData(lparam, RID_INPUT, buffer, ctypes.byref(size), header_size) in (0, 0xFFFFFFFF):
        return None
    raw = RAWINPUT.from_buffer(buffer)
    if raw.header.dwType == RIM_TYPEKEYBOARD:
        kb = raw.keyboard
        right = ((kb.VKey == VK_SHIFT and kb.MakeCode == SC_RSHIFT)
                 or (kb.VKey == VK_CONTROL and bool(kb.Flags & RI_KEY_E0)))
        return "key", kb.VKey, not kb.Flags & RI_KEY_BREAK, right
    if raw.header.dwType == RIM_TYPEMOUSE:
        return "mouse", raw.mouse.usButtonFlags
    return None


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


def _key_alone(name):
    """Lúc này chỉ có phím giữ name đang được nhấn: không kèm phím bổ trợ khác hay nút chuột."""
    held, excluded = _HOLD_STATE.get(name, _HOLD_STATE["shift"])
    return _down(held) and not any(
        _down(vk) for vk in excluded + (VK_MENU, VK_LWIN, VK_RWIN, VK_LBUTTON, VK_RBUTTON, VK_MBUTTON))


def _start_timer(seconds, fn):
    timer = threading.Timer(seconds, fn)
    timer.daemon = True
    timer.start()


class KeyHold:
    """Nhận ra lúc người dùng giữ riêng phím giữ (key_name, mặc định Shift) đủ lâu: không kèm phím khác,
    nút chuột hay lăn chuột.

    key() / mouse() chạy trên luồng nhận Raw Input; on_hold() chạy trên luồng hẹn giờ.
    Mỗi lần giữ phím chỉ gọi on_hold một lần.
    """

    def __init__(self, *, key_name="shift", seconds=HOLD_SECONDS, start_timer=None, alone=None,
                 clock=time.monotonic):
        self.enabled = False
        self.on_hold = None
        self.watch_mouse = None  # hàm(bool): nghe chuột chỉ trong lúc đang giữ phím
        self.key_name = key_name  # đổi được lúc đang chạy (cài đặt "Phím giữ")
        self.seconds = seconds
        self._start_timer = start_timer or _start_timer
        self._alone = alone or (lambda: _key_alone(self.key_name))
        self._clock = clock
        self._lock = threading.Lock()
        self._token = 0
        self._holding = False
        self._spoiled = False  # lần giữ này đã kèm phím / chuột khác, hoặc đã mở khung
        self._last_press = float("-inf")

    def key(self, vk, down, right=False):
        if not self.enabled:
            return
        watch = token = None
        hold_vk, right_only = HOLD_KEY_VK.get(self.key_name, HOLD_KEY_VK["shift"])
        with self._lock:
            now = self._clock()
            if vk == hold_vk and (right or not right_only):
                repeat = down and self._holding and now - self._last_press < 1.5
                self._last_press = now
                if repeat:
                    return  # Windows tự lặp phím khi đang giữ
                self._holding, self._spoiled, watch = down, False, down
                if down:
                    self._token += 1
                    token = self._token
            elif down and self._holding and not self._spoiled:
                self._spoiled, watch = True, False  # gõ chữ hoa, Shift+mũi tên, Ctrl+C, Ctrl+Shift (bộ gõ)...
        if watch is not None and self.watch_mouse:
            self.watch_mouse(watch)
        if token is not None:
            self._start_timer(self.seconds, lambda: self._fire(token))

    def mouse(self, button_flags):
        if not button_flags & (RI_MOUSE_PRESS | RI_MOUSE_WHEEL_ANY):
            return
        with self._lock:
            if not self._holding or self._spoiled:
                return
            self._spoiled = True  # Shift+lăn chuột (cuộn ngang), Shift+bấm (bôi đen), Ctrl+lăn (phóng to)...
        if self.watch_mouse:
            self.watch_mouse(False)

    def _fire(self, token):
        with self._lock:
            ready = self.enabled and self._holding and not self._spoiled and token == self._token
            if ready:
                self._spoiled = True
        if ready and self._alone() and self.on_hold:
            self.on_hold()


hold = KeyHold()
_listen_hwnd = None


def _watch_mouse(enabled):
    """Nghe nút chuột / lăn chuột trong lúc đang giữ phím. Gọi trên luồng sở hữu cửa sổ nghe."""
    if enabled and not _listen_hwnd:
        return
    device = RAWINPUTDEVICE(HID_USAGE_PAGE_GENERIC, HID_USAGE_MOUSE,
                            RIDEV_INPUTSINK if enabled else RIDEV_REMOVE, _listen_hwnd if enabled else None)
    user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(RAWINPUTDEVICE))


hold.watch_mouse = _watch_mouse


def handle_raw_input(lparam):
    """Gọi trong window procedure khi nhận WM_INPUT. Không bao giờ ném lỗi."""
    try:
        event = read_event(lparam)
        if not event:
            return
        if event[0] == "mouse":
            hold.mouse(event[1])
            return
        _, vk, down, right = event
        hold.key(vk, down, right)
        if not down:
            return
        kind = classify_key(vk, ctrl=_down(VK_CONTROL), alt=_down(VK_MENU),
                            shift=_down(VK_SHIFT), win=_down(VK_LWIN) or _down(VK_RWIN))
        if kind and not _foreground_is_own_app():
            record(kind)
    except Exception:
        pass


def set_listening(hwnd, enabled) -> bool:
    """Bật / tắt nghe bàn phím cho cửa sổ ẩn hwnd. Gọi trên luồng sở hữu cửa sổ đó."""
    global _listen_hwnd
    device = RAWINPUTDEVICE(HID_USAGE_PAGE_GENERIC, HID_USAGE_KEYBOARD,
                            RIDEV_INPUTSINK if enabled else RIDEV_REMOVE, hwnd if enabled else None)
    ok = bool(user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(RAWINPUTDEVICE)))
    _listen_hwnd = hwnd if enabled and ok else None
    if not enabled:
        consume_cut()
        _watch_mouse(False)
    return ok
