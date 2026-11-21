"""Phím tắt toàn cục bằng RegisterHotKey của Windows (không cần quyền admin).

Phím được ghi dạng chuỗi, ví dụ "f8", "ctrl+shift+t". Có thể đăng ký lại
khi app đang chạy (khi người dùng đổi phím trong Bảng điều khiển).
"""
import ctypes
import threading
from ctypes import wintypes
from queue import Empty, SimpleQueue

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT,
]
user32.PeekMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

WM_HOTKEY = 0x0312
WM_APPLY = 0x8001  # WM_APP + 1: yêu cầu luồng phím tắt đăng ký lại
MOD = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
MOD_ALIASES = {"control": "ctrl"}
MOD_NOREPEAT = 0x4000
VK_F1, VK_F24 = 0x70, 0x87
BLOCKED = {"alt+f4"}  # phím hệ thống, chiếm sẽ làm hỏng thao tác quen thuộc


def _split(spec: str):
    mods, key = [], None
    for part in spec.lower().replace(" ", "").split("+"):
        if not part:
            continue
        part = MOD_ALIASES.get(part, part)
        if part in MOD:
            if part not in mods:
                mods.append(part)
        elif key is None:
            key = part
        else:
            raise ValueError(f"Chỉ được một phím chính: {spec}")
    if key is None:
        raise ValueError(f"Thiếu phím chính: {spec}")
    return [m for m in MOD if m in mods], key


def normalize(spec: str) -> str:
    """Chuỗi chuẩn, ví dụ "Shift + Ctrl + T" -> "ctrl+shift+t". Chuỗi rỗng: không dùng phím."""
    if not spec or not spec.strip():
        return ""
    mods, key = _split(spec)
    parse("+".join(mods + [key]))  # kiểm tra hợp lệ
    return "+".join(mods + [key])


def parse(spec: str):
    """Trả về (modifiers, virtual key) cho RegisterHotKey."""
    mods, key = _split(spec)
    if key.startswith("f") and key[1:].isdigit():
        number = int(key[1:])
        if not 1 <= number <= 24:
            raise ValueError(f"Không có phím {key.upper()}")
        vk = VK_F1 + number - 1
    elif len(key) == 1 and key.isascii() and key.isalnum():
        if not {"ctrl", "alt", "win"} & set(mods):
            raise ValueError("Chữ cái và chữ số cần kèm Ctrl, Alt hoặc Win")
        vk = ord(key.upper())
    else:
        raise ValueError(f"Phím không hỗ trợ: {key}")
    if "+".join(mods + [key]) in BLOCKED:
        raise ValueError("Không dùng được Alt+F4 (phím đóng cửa sổ)")
    flags = MOD_NOREPEAT
    for m in mods:
        flags |= MOD[m]
    return flags, vk


def spec_from_vk(vk: int, *, ctrl=False, alt=False, shift=False, win=False) -> str:
    """Đổi phím người dùng vừa nhấn (virtual key + phím bổ trợ) thành chuỗi phím."""
    if VK_F1 <= vk <= VK_F24:
        key = f"f{vk - VK_F1 + 1}"
    elif 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        key = chr(vk).lower()
    else:
        raise ValueError("Chỉ hỗ trợ F1–F24, chữ cái hoặc chữ số (kèm Ctrl/Alt/Win)")
    mods = [name for name, on in (("ctrl", ctrl), ("alt", alt), ("shift", shift), ("win", win)) if on]
    return normalize("+".join(mods + [key]))


def display(spec: str) -> str:
    """Chuỗi hiển thị, ví dụ "ctrl+shift+t" -> "Ctrl+Shift+T"."""
    if not spec:
        return "Không dùng"
    return "+".join(p.upper() if len(p) == 1 or p[0] == "f" and p[1:].isdigit() else p.capitalize()
                    for p in spec.split("+"))


class HotkeyManager:
    """Một luồng riêng giữ các phím tắt; apply() đăng ký lại toàn bộ."""

    def __init__(self):
        self._thread_id = None
        self._callbacks = {}
        self._requests = SimpleQueue()
        ready = threading.Event()
        threading.Thread(target=self._loop, args=(ready,), daemon=True).start()
        ready.wait(5)

    def _loop(self, ready):
        msg = wintypes.MSG()
        # Tạo hàng đợi message cho luồng trước khi nhận PostThreadMessage.
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._thread_id = kernel32.GetCurrentThreadId()
        ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                callback = self._callbacks.get(msg.wParam)
                if callback:
                    threading.Thread(target=callback, daemon=True).start()
            elif msg.message == WM_APPLY:
                while True:
                    try:
                        bindings, done = self._requests.get_nowait()
                    except Empty:
                        break
                    done(self._register(bindings))

    def _register(self, bindings):
        for hotkey_id in list(self._callbacks):
            user32.UnregisterHotKey(None, hotkey_id)
        self._callbacks = {}
        errors = {}
        for hotkey_id, (name, (spec, callback)) in enumerate(bindings.items(), 1):
            if not spec:
                continue
            try:
                flags, vk = parse(spec)
            except ValueError as e:
                errors[name] = str(e)
                continue
            if user32.RegisterHotKey(None, hotkey_id, flags, vk):
                self._callbacks[hotkey_id] = callback
            else:
                errors[name] = "đang bị phần mềm khác chiếm"
        return errors

    def apply(self, bindings, timeout=3):
        """bindings: {tên: (chuỗi phím, callback)}. Trả về {tên: lý do lỗi}."""
        result = {}
        finished = threading.Event()

        def done(errors):
            result.update(errors)
            finished.set()

        self._requests.put((bindings, done))
        if not self._thread_id or not user32.PostThreadMessageW(self._thread_id, WM_APPLY, 0, 0):
            return {name: "không khởi tạo được phím tắt" for name, (spec, _) in bindings.items() if spec}
        if not finished.wait(timeout):
            return {name: "hết thời gian đăng ký phím" for name, (spec, _) in bindings.items() if spec}
        return result
