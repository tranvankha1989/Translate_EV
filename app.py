"""Translate Vietnam: dịch clipboard, chạy nền ở khay hệ thống.

Bấm trái vào icon: tắt / bật dịch. Chuột phải: chọn chế độ, mở Bảng điều khiển.
Phím tắt mặc định (đổi được trong Bảng điều khiển):
    F8  -> chuyển sang chế độ Sang tiếng Việt và dịch luôn clipboard
    F9  -> chuyển sang chế độ Sang tiếng Anh và dịch luôn clipboard
    F10 -> tắt dịch / bật lại chế độ trước đó
Chạy app lần nữa khi app đang chạy sẽ mở Bảng điều khiển của bản đang chạy.
"""
import ctypes
import os
import re
import sys
import threading
import time
from ctypes import wintypes
from queue import Empty, SimpleQueue

import pyperclip
import pystray
import requests

import filters
import hotkeys
import icons
import translator
import updater
from config import APP_NAME, Settings, model_label
from translator import TranslationCancelled, is_vietnamese, translate

OFF, AUTO, TO_VI, TO_EN = "off", "auto", "vi", "en"
MODE_LABEL = {OFF: "Tắt", AUTO: "Tự động", TO_VI: "Sang Việt", TO_EN: "Sang Anh"}
HOTKEY_KEYS = ("hotkey_vi", "hotkey_en", "hotkey_toggle")
HOTKEY_NAMES = {"hotkey_vi": "Sang tiếng Việt", "hotkey_en": "Sang tiếng Anh", "hotkey_toggle": "Bật/tắt dịch"}
MAX_WORKERS = 3
ERROR_NOTICE_INTERVAL = 600  # giây tối thiểu giữa hai lần báo "không dịch được"
# Ctrl+C hai lần: hai lần copy cùng nội dung cách nhau trong khoảng này mới dịch.
# Dưới DOUBLE_COPY_MIN là một app tự ghi clipboard hai lần cho một lần copy, không phải người bấm.
DOUBLE_COPY_MIN, DOUBLE_COPY_MAX = 0.06, 0.6
DEBOUNCE_SECONDS = 0.35  # "Mỗi lần copy": copy liên tiếp nhanh thì chỉ dịch lần cuối
WM_CLIPBOARDUPDATE = 0x031D
WM_SHOW_PANEL = 0x8002  # WM_APP + 2: bản chạy sau nhờ bản đang chạy mở Bảng điều khiển
HWND_MESSAGE = ctypes.c_void_p(-3)
LISTENER_CLASS = "TranslateVietnamMessageWindow"
MUTEX_NAME = "Local\\TranslateVietnam.SingleInstance"
ERROR_ALREADY_EXISTS = 183
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "TranslateVietnam"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
clipboard_sequence = user32.GetClipboardSequenceNumber
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = wintypes.ATOM
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
user32.AddClipboardFormatListener.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE

settings = None  # config.Settings, tạo trong main()
mode = AUTO
tray_lock = threading.RLock()
wake = threading.Event()
jobs = SimpleQueue()
ui_queue = SimpleQueue()  # việc cần chạy trên luồng giao diện (tkinter)
seen_sequence = None
pending = None
clipboard_dirty = False
last_internal_text = None
last_copy_text = None  # lần copy trước, để nhận ra Ctrl+C hai lần
last_copy_at = 0.0
active_job = None
busy_workers = 0
retry_at = 0.0
tray_revision = 0
last_error_notice = float("-inf")
icon = None
panel = None
tk_root = None
hotkey_manager = None
_instance_mutex = None


# ---------------------------------------------------------------- nhật ký lỗi

LOG_MAX_BYTES = 5 * 1024 * 1024  # app.log vượt 5 MB thì xóa phần cũ nhất
LOG_KEEP_BYTES = LOG_MAX_BYTES // 2  # phần log mới nhất được giữ lại sau khi dọn
_log_lock = threading.Lock()


def _log_dirs():
    """Thư mục chứa app.log: cạnh exe/app.py, không ghi được thì dùng LOCALAPPDATA."""
    if getattr(sys, "frozen", False):
        # Bản exe onefile: __file__ nằm trong thư mục tạm, bị xóa khi thoát app.
        yield os.path.dirname(sys.executable)
    else:
        yield os.path.dirname(os.path.abspath(__file__))
    yield os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TranslateVietnam")


def log_path():
    """Đường dẫn app.log đang dùng (file đầu tiên đã tồn tại, nếu không thì vị trí ưu tiên)."""
    paths = [os.path.join(folder, "app.log") for folder in _log_dirs()]
    return next((path for path in paths if os.path.exists(path)), paths[0])


def _trim_log(path):
    """Khi file đầy thì bỏ phần cũ, giữ LOG_KEEP_BYTES cuối (cắt đúng đầu dòng)."""
    try:
        if os.path.getsize(path) < LOG_MAX_BYTES:
            return
    except OSError:
        return  # chưa có file
    with open(path, "rb") as f:
        f.seek(-LOG_KEEP_BYTES, os.SEEK_END)
        tail = f.read()
    newline = tail.find(b"\n")
    tail = tail[newline + 1:] if newline >= 0 else b""
    temp = path + ".tmp"
    with open(temp, "wb") as f:
        f.write(tail)
    os.replace(temp, path)


def log(msg):
    """Ghi lỗi ra app.log (pythonw không có cửa sổ để in)."""
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n"
    with _log_lock:
        for folder in _log_dirs():
            path = os.path.join(folder, "app.log")
            try:
                os.makedirs(folder, exist_ok=True)
                _trim_log(path)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
                return
            except OSError:
                continue


# ---------------------------------------------------------------- icon khay

def ui_call(fn):
    """Chạy fn trên luồng giao diện (tkinter không an toàn khi gọi từ luồng khác)."""
    ui_queue.put(fn)


def refresh_icon(target=None, progress=0, status=None, clear_after=None, *, job=None):
    global tray_revision
    with tray_lock:
        current_mode = mode
        if not icon or (target is not None and target != current_mode) or (
            job is not None and job is not active_job
        ):
            return
        tray_revision += 1
        revision = tray_revision
        image = icons.tray_icon(current_mode, progress)
        title = status or f"{APP_NAME}: {MODE_LABEL[current_mode]}"
        if icon.icon is not image:
            icon.icon = image
        if icon.title != title:
            icon.title = title[:127]
        if clear_after is not None:
            def clear():
                with tray_lock:
                    if revision == tray_revision:
                        refresh_icon(target)

            timer = threading.Timer(clear_after, clear)
            timer.daemon = True
            timer.start()


def notify(message):
    try:
        if icon:
            icon.notify(message, APP_NAME)
    except Exception:
        pass


def notify_translation_error():
    """Báo một lần (tối đa mỗi 10 phút) để người dùng biết clipboard chưa được dịch."""
    global last_error_notice
    if not settings["notify_errors"]:
        return
    now = time.monotonic()
    with tray_lock:
        if now - last_error_notice < ERROR_NOTICE_INTERVAL:
            return
        last_error_notice = now
    notify("Không dịch được: mất mạng hoặc dịch vụ dịch đang bận.\nClipboard được giữ nguyên.")


# ---------------------------------------------------------------- dịch clipboard

def replace_clipboard(result, sequence, job):
    global seen_sequence, last_internal_text
    deadline = time.monotonic() + 0.5
    data = result.encode("utf-16-le") + b"\0\0"
    handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    hwnd = None
    try:
        address = kernel32.GlobalLock(handle)
        if not address:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(address, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)
        # EmptyClipboard needs a real owner window, even for immediate text data.
        hwnd = user32.CreateWindowExW(0, "Static", None, 0, 0, 0, 0, 0, None, None, None, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        while not job.is_set() and clipboard_sequence() == sequence:
            if user32.OpenClipboard(hwnd):
                with tray_lock:
                    try:
                        if job is not active_job or clipboard_sequence() != sequence:
                            return False
                        if not user32.EmptyClipboard() or not user32.SetClipboardData(13, handle):  # CF_UNICODETEXT
                            raise ctypes.WinError(ctypes.get_last_error())
                        handle = None  # Windows now owns the allocation.
                        last_internal_text = result
                        seen_sequence = clipboard_sequence()
                        return True
                    finally:
                        user32.CloseClipboard()
            if time.monotonic() >= deadline:
                raise ctypes.WinError(ctypes.get_last_error())
            job.wait(0.01)
        return False
    finally:
        if hwnd:
            user32.DestroyWindow(hwnd)
        if handle is not None:
            kernel32.GlobalFree(handle)


def do_translate(target, sequence, manual, job):
    animation = None
    translated = 0

    def is_cancelled():
        return job.is_set() or clipboard_sequence() != sequence

    def update_progress(fraction):
        nonlocal translated
        translated = min(99, int(fraction * 100))

    def animate():
        started = time.monotonic()
        previous = None
        last_rendered = 0.0
        while not job.is_set():
            # The API provides progress only when a chunk finishes.
            estimated = min(75, 6 + int((time.monotonic() - started) * 30))
            percent = min(95, max(estimated, translated))
            # Frequent Shell_NotifyIcon updates make Windows redraw the tooltip.
            # Render in stable 5% steps and at most five times per second.
            display_percent = min(95, (percent // 5) * 5)
            now = time.monotonic()
            if (
                display_percent != previous
                and (now - last_rendered >= 0.2 or display_percent == 95)
            ):
                label = "Đang tự động dịch…" if target == AUTO else f"Đang dịch {MODE_LABEL[target].lower()}…"
                refresh_icon(target, display_percent, label, job=job)
                previous = display_percent
                last_rendered = now
            job.wait(0.1)

    try:
        if is_cancelled():
            return 0
        try:
            text = pyperclip.paste()
        except pyperclip.PyperclipException:
            return 0.2
        if not text.strip() or is_cancelled() or (not manual and target == TO_VI and is_vietnamese(text)):
            return 0
        if target == AUTO and not any(char.isalpha() for char in text):
            return 0
        reason = filters.skip_reason(
            text,
            skip_private=settings["skip_password_managers"],
            skip_sensitive=settings["skip_sensitive_text"],
            excluded_apps=settings["excluded_apps"],
            manual=manual,
        )
        if reason:
            refresh_icon(target, status=f"Không dịch: {reason}", clear_after=2, job=job)
            return 0

        animation = threading.Thread(target=animate, daemon=True)
        animation.start()
        result = translate(text, target, on_progress=update_progress, is_cancelled=is_cancelled)
        if not result or is_cancelled():
            return 0

        if result != text and not replace_clipboard(result, sequence, job):
            return 0
        job.set()
        animation.join()
        refresh_icon(target, 100, "Đã dịch xong (100%)", clear_after=0.8, job=job)
        return 0
    except TranslationCancelled:
        return 0
    except Exception as e:
        if not is_cancelled():
            job.set()
            if animation:
                animation.join()
            log(f"Lỗi dịch clipboard: {e!r}")
            refresh_icon(target, status="Không thể dịch clipboard", clear_after=3, job=job)
            notify_translation_error()
            # Do not hammer a failed service with the same clipboard indefinitely.
            # A new clipboard event or explicit hotkey starts the next attempt.
        return 0
    finally:
        job.set()
        if animation:
            animation.join()
        save_gemini_usage()
        if translator.last_gemini_error or settings["gemini_api_key"]:
            ui_call(lambda: panel and panel.refresh_engine())


def translation_worker():
    global active_job, busy_workers, pending, retry_at
    while True:
        target, sequence, manual, job = jobs.get()
        delay = do_translate(target, sequence, manual, job)
        with tray_lock:
            busy_workers -= 1
            if active_job is job:
                active_job = None
                if delay and clipboard_sequence() == sequence:
                    pending = manual
                    retry_at = time.monotonic() + delay
        wake.set()


def classify_copy(trigger, text, now, previous_text, previous_at):
    """Một lần copy mới: "debounce" (dịch sau một chút), "translate" (dịch ngay) hoặc "record" (chờ lần 2)."""
    if trigger == "single":
        return "debounce"
    if (text is not None and text == previous_text
            and DOUBLE_COPY_MIN <= now - previous_at <= DOUBLE_COPY_MAX):
        return "translate"
    return "record"


def watcher():
    global seen_sequence, pending, active_job, busy_workers, retry_at, clipboard_dirty, last_internal_text
    global last_copy_text, last_copy_at
    for _ in range(MAX_WORKERS):
        threading.Thread(target=translation_worker, daemon=True).start()
    while True:
        with tray_lock:
            retry_delay = max(0, retry_at - time.monotonic()) if pending is not None else None
        wake.wait(retry_delay)
        wake.clear()
        with tray_lock:
            sequence = clipboard_sequence()
            if mode != OFF and clipboard_dirty:
                clipboard_dirty = False
                if sequence != seen_sequence:
                    try:
                        current_text = pyperclip.paste()
                    except pyperclip.PyperclipException:
                        current_text = None
                    if last_internal_text is not None and current_text == last_internal_text:
                        # EmptyClipboard/SetClipboardData can generate more than one
                        # WM_CLIPBOARDUPDATE for the result we just produced.
                        seen_sequence = sequence
                        last_internal_text = None
                    else:
                        last_internal_text = None
                        seen_sequence = sequence
                        if active_job:
                            active_job.set()
                            active_job = None
                        now = time.monotonic()
                        action = classify_copy(settings["trigger"], current_text, now, last_copy_text, last_copy_at)
                        if action == "debounce":
                            pending, retry_at = False, now + DEBOUNCE_SECONDS
                        elif action == "translate":
                            pending, retry_at = False, 0
                            last_copy_text = None  # lần bấm thứ ba bắt đầu lượt đếm mới
                        else:
                            pending = None  # copy bình thường; chờ xem có Ctrl+C lần hai không
                            last_copy_text, last_copy_at = current_text, now
                        refresh_icon()
            if mode != OFF and pending is not None and busy_workers < MAX_WORKERS and time.monotonic() >= retry_at:
                active_job = threading.Event()
                jobs.put((mode, sequence, pending, active_job))
                busy_workers += 1
                pending = None


def message_listener():
    """Cửa sổ ẩn: nhận báo clipboard thay đổi, và yêu cầu mở bảng từ bản chạy sau."""
    global clipboard_dirty
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    @callback_type
    def window_proc(hwnd, message, wparam, lparam):
        global clipboard_dirty
        if message == WM_CLIPBOARDUPDATE:
            with tray_lock:
                clipboard_dirty = True
            wake.set()
            return 0
        if message == WM_SHOW_PANEL:
            ui_call(show_panel)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    class WindowClass(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", callback_type),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    window_class = WindowClass(
        0, window_proc, 0, 0, kernel32.GetModuleHandleW(None),
        None, None, None, None, LISTENER_CLASS,
    )
    if not user32.RegisterClassW(ctypes.byref(window_class)):
        log(f"Không đăng ký được clipboard listener: {ctypes.WinError(ctypes.get_last_error())!r}")
        return
    hwnd = user32.CreateWindowExW(
        0, LISTENER_CLASS, None, 0, 0, 0, 0, 0, HWND_MESSAGE, None,
        window_class.hInstance, None,
    )
    if not hwnd or not user32.AddClipboardFormatListener(hwnd):
        log(f"Không khởi tạo được clipboard listener: {ctypes.WinError(ctypes.get_last_error())!r}")
        return
    message = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))


# ---------------------------------------------------------------- chế độ và phím tắt

def current_mode():
    with tray_lock:
        return mode


def change_mode(target, *, manual=False):
    global mode, seen_sequence, pending, active_job, retry_at
    with tray_lock:
        mode = target
        seen_sequence = clipboard_sequence()
        if active_job:
            active_job.set()
            active_job = None
        pending, retry_at = (True if manual else None), 0
        refresh_icon()
    changes = {"mode": target}
    if target != OFF:
        changes["last_on_mode"] = target
    if not settings.update(**changes):
        log("Không lưu được cài đặt")
    if icon:
        icon.update_menu()
    ui_call(lambda: panel and panel.refresh_mode())
    wake.set()


def set_mode(m):
    def handler(icon_, item):
        change_mode(m)
    return handler


def toggle_off(*_):
    """Bấm trái / F10: đang bật thì tắt; đang tắt thì bật lại chế độ trước đó."""
    change_mode(settings["last_on_mode"] if current_mode() == OFF else OFF)


def switch_to(m):
    """F8/F9: chuyển chế độ (icon đổi V/E) và dịch luôn clipboard hiện tại."""
    change_mode(m, manual=True)


def is_mode(m):
    return lambda item: current_mode() == m


def hotkey_bindings():
    return {
        "hotkey_vi": (settings["hotkey_vi"], lambda: switch_to(TO_VI)),
        "hotkey_en": (settings["hotkey_en"], lambda: switch_to(TO_EN)),
        "hotkey_toggle": (settings["hotkey_toggle"], toggle_off),
    }


def register_hotkeys():
    errors = hotkey_manager.apply(hotkey_bindings())
    for key, why in errors.items():
        log(f"Phím {settings[key]}: {why}")
    if icon:
        icon.update_menu()
    return errors


# ---------------------------------------------------------------- hệ thống

def acquire_single_instance():
    """Chỉ chạy một bản. Nếu đã có bản đang chạy thì nhờ nó mở Bảng điều khiển."""
    global _instance_mutex
    ctypes.set_last_error(0)
    _instance_mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if _instance_mutex and ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
        return True
    hwnd = user32.FindWindowExW(HWND_MESSAGE, None, LISTENER_CLASS, None)
    if hwnd:
        user32.PostMessageW(hwnd, WM_SHOW_PANEL, 0, 0)
    return False


def startup_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    executable = sys.executable
    if executable.lower().endswith("python.exe"):
        hidden = executable[:-10] + "pythonw.exe"
        if os.path.exists(hidden):
            executable = hidden
    return f'"{executable}" "{os.path.abspath(__file__)}"'


def _command_is_valid(command):
    paths = re.findall(r'"([^"]+)"', command or "")
    return bool(paths) and all(os.path.exists(path) for path in paths)


def apply_autostart(enabled, *, force=False):
    """Bật/tắt chạy cùng Windows (HKCU\\...\\Run, không cần quyền admin).

    Khi app tự khởi động, nó không ghi đè một mục còn hợp lệ của bản khác
    (ví dụ bản chạy thử ở thư mục khác). Bấm ô trong Bảng điều khiển thì ghi đè (force).
    """
    if os.name != "nt":
        return
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            try:
                current = winreg.QueryValueEx(key, RUN_VALUE)[0]
            except FileNotFoundError:
                current = None
            if not enabled:
                if current is not None:
                    winreg.DeleteValue(key, RUN_VALUE)
                return
            wanted = startup_command()
            if current == wanted or (current and not force and _command_is_valid(current)):
                return
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, wanted)
    except OSError as e:
        log(f"Không đặt được khởi động cùng Windows: {e!r}")


def set_dpi_awareness():
    """Cửa sổ sắc nét trên màn hình độ phân giải cao."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def apply_translator_settings():
    translator.configure(
        mymemory_email=settings["mymemory_email"],
        gemini_api_key=settings["gemini_api_key"],
        gemini_backup_keys=settings["gemini_backup_keys"],
        gemini_model=settings["gemini_model"],
        gemini_domain=settings["domain"],
    )


def save_gemini_usage():
    """Lưu số lượt Gemini đã dùng (chỉ con số) để bộ đếm đúng cả khi khởi động lại app."""
    day, count = translator.gemini_usage["day"], translator.gemini_usage["count"]
    if (day, count) != (settings["gemini_usage_day"], settings["gemini_usage_count"]):
        settings.update(gemini_usage_day=day, gemini_usage_count=count)


def engine_summary():
    if not translator.gemini_keys():
        return "Đang dịch bằng: Google Dịch. Nhập API key Gemini (Mở rộng) để dịch theo lĩnh vực."
    reset = translator.quota_reset_local().strftime("%H:%M")
    usage = f"Hôm nay đã dùng {translator.gemini_usage_today()} lượt Gemini (làm mới lúc {reset})."
    if translator.last_gemini_error and not translator.gemini_ready():
        return f"{translator.last_gemini_error}, tạm dùng Google Dịch. {usage}"
    key = translator.active_key_label
    key_note = f", {key}" if key and key != "key chính" else ""
    return (f"Đang dịch bằng: Gemini ({model_label(settings['gemini_model'])}{key_note}), "
            f"lĩnh vực {settings['domain']}. {usage}")


def check_gemini(api_key, model):
    result = translator.check_gemini(api_key, model)
    save_gemini_usage()
    return result


def show_panel():
    if panel:
        panel.show()


def request_quit():
    def stop():
        if tk_root:
            tk_root.destroy()

    ui_call(stop)


class Controller:
    """Cầu nối giữa Bảng điều khiển và phần chạy nền."""

    def __init__(self):
        self.config_path = settings.path
        self.ui_call = ui_call
        self.current_mode = current_mode
        self.engine_summary = engine_summary
        self.log_path = log_path
        self.check_gemini = check_gemini

    def get(self, key):
        return settings[key]

    def set_mode(self, m):
        change_mode(m)

    def set_option(self, key, value):
        settings.update(**{key: value})
        if key == "autostart":
            apply_autostart(value, force=True)
        elif key == "mymemory_email":
            apply_translator_settings()

    def set_gemini(self, **changes):
        settings.update(**changes)
        apply_translator_settings()

    def set_excluded_apps(self, names):
        names = filters.normalize_app_names(names)
        if names != settings["excluded_apps"]:
            settings.update(excluded_apps=names)

    def set_hotkey(self, key, spec):
        for other in HOTKEY_KEYS:
            if other != key and spec and settings[other] == spec:
                return f"Phím {hotkeys.display(spec)} đang dùng cho “{HOTKEY_NAMES[other]}”."
        settings.update(**{key: spec})
        return None

    def suspend_hotkeys(self):
        hotkey_manager.apply({})

    def resume_hotkeys(self):
        return register_hotkeys()

    def reset_defaults(self):
        settings.reset()
        apply_autostart(settings["autostart"], force=True)
        apply_translator_settings()
        change_mode(settings["mode"])
        return register_hotkeys()

    def open_log_folder(self):
        folder = os.path.dirname(log_path())
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)

    def quit(self):
        request_quit()

    # ----- cập nhật qua GitHub -----
    can_self_update = staticmethod(updater.can_self_update)
    releases_page = updater.RELEASES_PAGE

    def check_update(self):
        """Chạy trong luồng phụ. Trả về bản mới hoặc None; lỗi mạng thì ném UpdateError."""
        try:
            release = updater.check()
        except (requests.RequestException, ValueError) as e:
            raise updater.UpdateError(f"Không kết nối được GitHub: {e.__class__.__name__}") from e
        set_available_release(release)
        return release

    def install_update(self, release, on_progress=None):
        """Chạy trong luồng phụ: tải, kiểm tra rồi giao cho bản mới tự cài; app này thoát."""
        exe_path = sys.executable
        target = updater.update_path(exe_path)
        try:
            updater.download(release, target, on_progress)
        except (requests.RequestException, OSError) as e:
            raise updater.UpdateError(f"Không tải được bản cập nhật: {e.__class__.__name__}") from e
        updater.start_install(target, exe_path)
        log(f"Cập nhật: đã tải bản {release.version}, đang cài đặt")
        request_quit()


available_release = None
_notified_version = None


def set_available_release(release):
    """Ghi nhận bản mới: hiện mục menu, dòng báo trong Bảng điều khiển và thông báo một lần."""
    global available_release, _notified_version
    available_release = release
    if icon:
        icon.update_menu()
    ui_call(lambda: panel and panel.show_update_available(release))
    if release and release.version != _notified_version:
        _notified_version = release.version
        notify(f"Có bản cập nhật {APP_NAME} {release.version}.\n"
               "Chuột phải vào icon → Cập nhật, hoặc mở Bảng điều khiển.")


def update_checker():
    """Tự kiểm tra bản mới: 1 phút sau khi mở app, sau đó mỗi ngày một lần."""
    stop = threading.Event()
    delay = 60
    while not stop.wait(delay):
        delay = 24 * 3600
        if not settings["auto_update"]:
            continue
        try:
            release = updater.check()
        except Exception as e:  # mất mạng, GitHub lỗi...: thử lại ngày mai
            log(f"Không kiểm tra được bản cập nhật: {e!r}")
            continue
        if release:
            set_available_release(release)


def handle_update_args(args):
    """Xử lý tham số của quy trình cập nhật. Trả về True nếu tiến trình này chỉ để cài đặt."""
    if "install-update" in args:
        try:
            updater.install_update(args["install-update"], args.get("wait-pid", []))
        except Exception as e:
            log(f"Cài bản cập nhật thất bại: {e!r}")
        return True
    if "cleanup" in args:
        threading.Thread(target=updater.cleanup, args=(args["cleanup"],), daemon=True).start()
    return False


def build_menu():
    def label(text, key):
        return lambda item: f"{text}\t{hotkeys.display(settings[key])}" if settings[key] else text

    return pystray.Menu(
        pystray.MenuItem("Bật/tắt dịch", toggle_off, default=True, visible=False),
        pystray.MenuItem("Tự động hai chiều", set_mode(AUTO), checked=is_mode(AUTO), radio=True),
        pystray.MenuItem(label("Sang tiếng Việt", "hotkey_vi"), set_mode(TO_VI), checked=is_mode(TO_VI), radio=True),
        pystray.MenuItem(label("Sang tiếng Anh", "hotkey_en"), set_mode(TO_EN), checked=is_mode(TO_EN), radio=True),
        pystray.MenuItem(label("Tắt", "hotkey_toggle"), set_mode(OFF), checked=is_mode(OFF), radio=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Bảng điều khiển…", lambda: ui_call(show_panel)),
        pystray.MenuItem("Hướng dẫn", lambda: ui_call(lambda: panel and panel.show_help())),
        pystray.MenuItem(
            lambda item: f"Cập nhật lên bản {available_release.version}…" if available_release else "Cập nhật",
            lambda: ui_call(lambda: panel and available_release and panel.prompt_update(available_release)),
            visible=lambda item: available_release is not None,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Thoát", request_quit),
    )


def on_ready(icon_, hotkey_errors, updated_to=None):
    icon_.visible = True
    threading.Thread(target=message_listener, daemon=True).start()
    threading.Thread(target=watcher, daemon=True).start()
    threading.Thread(target=update_checker, daemon=True).start()
    if updated_to:
        notify(f"Đã cập nhật {APP_NAME} lên phiên bản {updated_to}.")
    if hotkey_errors:
        notify("Phím tắt không dùng được: " + "; ".join(
            f"{hotkeys.display(settings[key])} {why}" for key, why in hotkey_errors.items())
            + "\nĐổi phím trong Bảng điều khiển.")


def pump_ui():
    while True:
        try:
            fn = ui_queue.get_nowait()
        except Empty:
            break
        try:
            fn()
        except Exception as e:
            log(f"Lỗi giao diện: {e!r}")
    if tk_root:
        tk_root.after(100, pump_ui)


def main():
    global settings, mode, seen_sequence, hotkey_manager, icon, panel, tk_root
    args = updater.parse_args(sys.argv[1:])
    if handle_update_args(args):
        return  # tiến trình cài bản cập nhật: chép file xong là thoát
    if not acquire_single_instance():
        return  # đã có bản đang chạy: bản đó sẽ mở Bảng điều khiển
    set_dpi_awareness()
    settings = Settings()
    first_run = settings.first_run
    if first_run and not settings.save():
        log("Không lưu được cài đặt lần đầu")
    mode = settings["mode"]
    translator.load_gemini_usage(settings["gemini_usage_day"], settings["gemini_usage_count"])
    apply_translator_settings()
    apply_autostart(settings["autostart"])
    seen_sequence = clipboard_sequence()
    for target in icons.MODE_COLOR:
        icons.tray_icon(target, 0)
    hotkey_manager = hotkeys.HotkeyManager()
    hotkey_errors = register_hotkeys()

    import tkinter as tk
    from PIL import ImageTk

    from panel import ControlPanel

    tk_root = tk.Tk()
    tk_root.withdraw()
    window_icon = ImageTk.PhotoImage(icons.app_icon(64))
    panel = ControlPanel(tk_root, Controller(), window_icon)
    icon = pystray.Icon("translate-vn", icons.tray_icon(mode, 0),
                        f"{APP_NAME}: {MODE_LABEL[mode]}", build_menu())
    icon.run_detached(setup=lambda icon_: on_ready(icon_, hotkey_errors, args.get("updated")))
    if first_run or settings["show_panel_on_start"]:
        tk_root.after(300, show_panel)
    tk_root.after(100, pump_ui)
    try:
        tk_root.mainloop()
    finally:
        tk_root = None
        icon.stop()


if __name__ == "__main__":
    main()
