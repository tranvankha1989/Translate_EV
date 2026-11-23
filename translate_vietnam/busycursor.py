"""Con trỏ chuột "đang xử lý" (vòng xoay) trong lúc dịch / sửa chính tả, xong thì trở lại bình thường.

Windows không cho đổi con trỏ bên trong app khác, nên app tạm thay con trỏ hệ thống
(mũi tên và con trỏ chữ I) bằng con trỏ vòng xoay của bộ con trỏ đang dùng, rồi nạp lại
bộ con trỏ của người dùng khi xong. An toàn:
- luôn tự trả lại sau tối đa MAX_SECONDS giây, kể cả khi việc dịch bị treo;
- trả lại khi app khởi động và khi thoát (phòng app bị tắt ngang giữa chừng).
"""
import ctypes
import os
import threading
from contextlib import contextmanager
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                              wintypes.UINT]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.CopyIcon.argtypes = [wintypes.HANDLE]
user32.CopyIcon.restype = wintypes.HANDLE
user32.SetSystemCursor.argtypes = [wintypes.HANDLE, wintypes.DWORD]
user32.SetSystemCursor.restype = wintypes.BOOL
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]

OCR_NORMAL, OCR_IBEAM = 32512, 32513  # mũi tên, con trỏ chữ I (khi chuột nằm trên văn bản)
SPI_SETCURSORS = 0x0057
MOUSEEVENTF_MOVE = 0x0001
IMAGE_CURSOR, LR_LOADFROMFILE, LR_DEFAULTSIZE = 2, 0x10, 0x40
MAX_SECONDS = 60

SPIN, ARROW, OFF = "spin", "arrow", "off"
STYLES = (SPIN, ARROW, OFF)
# kiểu -> (tên trong HKCU\Control Panel\Cursors, file mặc định của Windows, IDC_*)
_SOURCE = {SPIN: ("Wait", "aero_busy.ani", 32514), ARROW: ("AppStarting", "aero_working.ani", 32650)}

_lock = threading.Lock()
_count = 0
_timer = None
# File đánh dấu "con trỏ đang bị thay": app bị tắt ngang thì lần mở sau biết để trả lại.
MARKER = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TranslateVietnam", "cursor_busy")


def _mark(on):
    try:
        if on:
            os.makedirs(os.path.dirname(MARKER), exist_ok=True)
            open(MARKER, "w").close()
        elif os.path.exists(MARKER):
            os.remove(MARKER)
    except OSError:
        pass


def _cursor_setting(name):
    """Giá trị trong HKCU\\Control Panel\\Cursors (bộ con trỏ và cỡ con trỏ của người dùng trên máy đó)."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _scheme_file(name):
    """File con trỏ trong bộ con trỏ người dùng đang dùng (rỗng nếu dùng mặc định của Windows)."""
    value = _cursor_setting(name)
    return os.path.expandvars(value) if isinstance(value, str) else ""


def _cursor_size():
    """Cỡ con trỏ người dùng chọn trong Cài đặt > Trợ năng (0 = cỡ thường của hệ thống)."""
    size = _cursor_setting("CursorBaseSize")
    return size if isinstance(size, int) and 32 < size <= 256 else 0


def _load(style):
    """Con trỏ vòng xoay mới (có chuyển động), đúng bộ và đúng cỡ con trỏ của máy đang chạy.

    Mỗi lần thay cần một bản riêng vì Windows giữ và tự hủy nó. Thiếu file thì dùng
    con trỏ có sẵn của Windows, nên máy nào cũng có vòng xoay.
    """
    name, default_file, idc = _SOURCE[style]
    windows = os.environ.get("SystemRoot") or r"C:\Windows"
    size = _cursor_size()
    flags = LR_LOADFROMFILE | (0 if size else LR_DEFAULTSIZE)
    for path in (_scheme_file(name), os.path.join(windows, "Cursors", default_file)):
        if path and os.path.exists(path):
            handle = user32.LoadImageW(None, path, IMAGE_CURSOR, size, size, flags)
            if handle:
                return handle
    shared = user32.LoadCursorW(None, ctypes.c_void_p(idc))
    return user32.CopyIcon(shared) if shared else None


def _refresh():
    """Chuột đứng yên thì Windows chưa vẽ lại con trỏ: gửi một lần "di chuột" 0 điểm ảnh."""
    user32.mouse_event(MOUSEEVENTF_MOVE, 0, 0, 0, 0)


def _apply(style):
    _mark(True)
    for cursor_id in (OCR_NORMAL, OCR_IBEAM):
        handle = _load(style)
        if handle:
            user32.SetSystemCursor(handle, cursor_id)
    _refresh()


def restore():
    """Nạp lại bộ con trỏ của người dùng (gọi được nhiều lần, không hại gì)."""
    user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
    _refresh()
    _mark(False)


def restore_if_left_over():
    """Mở app: lần chạy trước bị tắt ngang lúc đang đổi con trỏ thì trả lại con trỏ bình thường."""
    if os.path.exists(MARKER):
        restore()


def shutdown():
    """Thoát app: còn việc đang chạy thì trả con trỏ về bình thường ngay."""
    global _count, _timer
    with _lock:
        if _timer:
            _timer.cancel()
            _timer = None
        if _count:
            _count = 0
            restore()


def _expired():
    global _count, _timer
    with _lock:
        _count, _timer = 0, None
        restore()


def begin(style):
    """Bắt đầu một việc đang xử lý. Nhiều việc cùng lúc thì con trỏ chỉ đổi một lần."""
    global _count, _timer
    if style not in _SOURCE:
        return False
    with _lock:
        _count += 1
        if _count == 1:
            try:
                _apply(style)
            except OSError:
                pass
            _timer = threading.Timer(MAX_SECONDS, _expired)
            _timer.daemon = True
            _timer.start()
    return True


def end():
    """Xong một việc; khi không còn việc nào thì trả con trỏ về bình thường."""
    global _count, _timer
    with _lock:
        if _count == 0:
            return
        _count -= 1
        if _count == 0:
            if _timer:
                _timer.cancel()
                _timer = None
            restore()


@contextmanager
def busy(style):
    """with busy(kiểu): con trỏ vòng xoay trong suốt khối lệnh, kể cả khi có lỗi."""
    started = begin(style)
    try:
        yield
    finally:
        if started:
            end()
