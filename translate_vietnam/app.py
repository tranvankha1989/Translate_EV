"""Translate Vietnam: dịch clipboard và sửa chính tả, chạy nền ở khay hệ thống.

Nhấn nhanh Ctrl+C hai lần: dịch. Nhấn Ctrl+X: sửa chính tả, câu văn. Sau đó Ctrl+V để dán.
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

from . import filters
from . import hotkeys
from . import icons
from . import installer
from . import keywatch
from . import proofreader
from . import translator
from . import updater
from .config import APP_NAME, APP_VERSION, Settings, config_dir, model_label
from .translator import TranslationCancelled, is_vietnamese, translate

OFF, AUTO, TO_VI, TO_EN = "off", "auto", "vi", "en"
PROOFREAD = "proofread"  # việc sửa chính tả (Ctrl+X), chạy độc lập với chế độ dịch
FIX_FACE = "fix"  # icon khay lúc đang sửa chính tả
MODE_LABEL = {OFF: "Tắt", AUTO: "Tự động", TO_VI: "Sang Việt", TO_EN: "Sang Anh"}
# Khi chạy từ mã nguồn: thư mục dự án chứa app.py (file khởi chạy) và app.log.
SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(SOURCE_ROOT, "app.py")
HOTKEY_KEYS = ("hotkey_vi", "hotkey_en", "hotkey_toggle")
HOTKEY_NAMES = {"hotkey_vi": "Sang tiếng Việt", "hotkey_en": "Sang tiếng Anh", "hotkey_toggle": "Bật/tắt dịch"}
MAX_WORKERS = 3
ERROR_NOTICE_INTERVAL = 600  # giây tối thiểu giữa hai lần báo "không dịch được"
# Ctrl+C hai lần: hai lần copy cùng nội dung cách nhau trong khoảng này mới dịch.
# Dưới DOUBLE_COPY_MIN là một app tự ghi clipboard hai lần cho một lần copy, không phải người bấm.
DOUBLE_COPY_MIN, DOUBLE_COPY_MAX = 0.06, 0.6
DEBOUNCE_SECONDS = 0.35  # "Mỗi lần copy": copy liên tiếp nhanh thì chỉ dịch lần cuối
CUT_DEBOUNCE = 0.2  # app ghi clipboard hai lần cho một lần cắt: chỉ sửa một lần
# Giữ kết nối tới Gemini: đo 24/11/2026 máy chủ giữ kết nối bỏ không 3–5 phút; 60 giây để dư
# an toàn với router / mạng công ty đóng kết nối sớm hơn. Dùng lại kết nối ~0,06 giây, nối lại ~0,45 giây.
KEEP_WARM_SECONDS = 60
WM_CLIPBOARDUPDATE = 0x031D
WM_SHOW_PANEL = 0x8002  # WM_APP + 2: bản chạy sau nhờ bản đang chạy mở Bảng điều khiển
WM_QUIT_APP = 0x8003  # WM_APP + 3: trình gỡ cài đặt nhờ bản đang chạy thoát
WM_KEYWATCH = 0x8004  # WM_APP + 4: bật (wParam=1) / tắt (0) nghe Ctrl+X
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
pending_task = None  # PROOFREAD: việc đang chờ là sửa chính tả; None: dịch theo chế độ hiện tại
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
listener_hwnd = None  # cửa sổ ẩn nhận báo clipboard và phím Ctrl+X
_gemini_hint_shown = False
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
        yield SOURCE_ROOT
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


def refresh_icon(target=None, progress=0, status=None, clear_after=None, *, job=None, face=None):
    """face: hình icon tạm thời (ví dụ FIX_FACE khi đang sửa chính tả), mặc định theo chế độ dịch."""
    global tray_revision
    with tray_lock:
        current_mode = mode
        if not icon or (target is not None and target != current_mode) or (
            job is not None and job is not active_job
        ):
            return
        tray_revision += 1
        revision = tray_revision
        image = icons.tray_icon(face or current_mode, progress)
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


def notify_error(message):
    """Báo lỗi bằng thông báo Windows, tối đa mỗi 10 phút một lần."""
    global last_error_notice
    if not settings["notify_errors"]:
        return
    now = time.monotonic()
    with tray_lock:
        if now - last_error_notice < ERROR_NOTICE_INTERVAL:
            return
        last_error_notice = now
    notify(message)


def notify_translation_error():
    notify_error("Không dịch được: mất mạng hoặc dịch vụ dịch đang bận.\nClipboard được giữ nguyên.")


def notify_needs_gemini():
    """Sửa chính tả cần API key Gemini: nhắc một lần mỗi lần mở app."""
    global _gemini_hint_shown
    if _gemini_hint_shown:
        return
    _gemini_hint_shown = True
    notify("Sửa chính tả khi nhấn Ctrl+X cần API key Gemini.\n"
           "Mở Bảng điều khiển, bấm Mở rộng, tab AI Gemini để nhập key (miễn phí).")


def log_duration(action, text, started):
    """Ghi thời gian mỗi lần dịch / sửa vào nhật ký (chỉ số ký tự và số giây, không ghi nội dung)."""
    seconds = f"{time.monotonic() - started:.1f}".replace(".", ",")
    log(f"Thời gian: {action} {len(text)} ký tự trong {seconds} giây ({translator.last_engine or 'không rõ'})")


def prepare_gemini():
    """Kiểm tra key, model Gemini và mở sẵn kết nối; cập nhật dòng trạng thái trong Bảng điều khiển."""
    try:
        translator.prepare_gemini()
    except Exception as e:
        log(f"Không kiểm tra được Gemini lúc khởi động: {e!r}")
    ui_call(lambda: panel and panel.refresh_engine())


def gemini_keeper():
    """Mở app: kiểm tra Gemini ngay, sau đó giữ kết nối luôn mở để mỗi lần dịch không phải nối lại."""
    prepare_gemini()
    stop = threading.Event()
    while not stop.wait(KEEP_WARM_SECONDS):
        if translator.gemini_keys():
            translator.keep_warm()


def start_progress(job, label, *, target=None, face=None):
    """Tô tiến độ lên icon khay trong lúc chờ dịch vụ. Trả về (luồng vẽ, hàm báo tiến độ 0–1)."""
    state = {"done": 0}

    def update(fraction):
        state["done"] = min(99, int(fraction * 100))

    def animate():
        started = time.monotonic()
        previous = None
        last_rendered = 0.0
        while not job.is_set():
            # The API provides progress only when a chunk finishes.
            estimated = min(75, 6 + int((time.monotonic() - started) * 30))
            percent = min(95, max(estimated, state["done"]))
            # Frequent Shell_NotifyIcon updates make Windows redraw the tooltip.
            # Render in stable 5% steps and at most five times per second.
            display_percent = min(95, (percent // 5) * 5)
            now = time.monotonic()
            if (
                display_percent != previous
                and (now - last_rendered >= 0.2 or display_percent == 95)
            ):
                refresh_icon(target, display_percent, label, job=job, face=face)
                previous = display_percent
                last_rendered = now
            job.wait(0.1)

    thread = threading.Thread(target=animate, daemon=True)
    thread.start()
    return thread, update


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
    started = time.monotonic()

    def is_cancelled():
        return job.is_set() or clipboard_sequence() != sequence

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

        label = "Đang tự động dịch…" if target == AUTO else f"Đang dịch {MODE_LABEL[target].lower()}…"
        animation, update_progress = start_progress(job, label, target=target)
        result = translate(text, target, on_progress=update_progress, is_cancelled=is_cancelled)
        if not result or is_cancelled():
            return 0

        if result != text and not replace_clipboard(result, sequence, job):
            return 0
        job.set()
        animation.join()
        refresh_icon(target, 100, "Đã dịch xong (100%)", clear_after=0.8, job=job)
        log_duration("dịch", text, started)
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


def do_proofread(sequence, job):
    """Sửa chính tả đoạn vừa cắt (Ctrl+X). Xong thì clipboard là bản đã sửa để Ctrl+V.

    Không sửa được (thiếu key, hết lượt, mất mạng, kết quả bất thường) thì clipboard
    giữ nguyên đúng đoạn đã cắt, nên Ctrl+V vẫn dán lại được, không mất chữ.
    """
    animation = None
    started = time.monotonic()

    def is_cancelled():
        return job.is_set() or clipboard_sequence() != sequence

    def show(text, seconds=3):
        refresh_icon(status=text, clear_after=seconds, job=job)

    try:
        if is_cancelled():
            return 0
        try:
            text = pyperclip.paste()
        except pyperclip.PyperclipException:
            return 0.2
        if is_cancelled():
            return 0
        reason = proofreader.skip_reason(text) or filters.skip_reason(
            text,
            skip_private=settings["skip_password_managers"],
            skip_sensitive=settings["skip_sensitive_text"],
            excluded_apps=settings["excluded_apps"] + settings["proofread_excluded_apps"],
        )
        if reason:
            show(f"Không sửa chính tả: {reason}", 2)
            return 0
        if not translator.gemini_keys():
            show("Sửa chính tả cần API key Gemini (Mở rộng, tab AI Gemini)")
            notify_needs_gemini()
            return 0
        if not translator.gemini_ready():
            why = translator.last_gemini_error or "Gemini đang tạm dừng"
            show(f"Chưa sửa được: {why}. Clipboard giữ nguyên.")
            notify_error(f"Chưa sửa được chính tả: {why}.\nCtrl+V sẽ dán lại đúng đoạn bạn đã cắt.")
            return 0

        animation, update_progress = start_progress(job, "Đang sửa chính tả…", face=FIX_FACE)
        result = proofreader.proofread(text, update_progress, is_cancelled=is_cancelled)
        if not result or is_cancelled():
            return 0
        if result != text and not replace_clipboard(result, sequence, job):
            return 0
        job.set()
        animation.join()
        done = ("Đã sửa xong (100%), nhấn Ctrl+V để dán" if result != text
                else "Đoạn văn đã ổn, không cần sửa")
        refresh_icon(None, 100, done, clear_after=1.2, job=job, face=FIX_FACE)
        log_duration("sửa chính tả", text, started)
        return 0
    except TranslationCancelled:
        return 0
    except Exception as e:
        if not is_cancelled():
            job.set()
            if animation:
                animation.join()
            if isinstance(e, proofreader.ProofreadRejected):
                log(f"Bỏ kết quả sửa chính tả bất thường: {e}")
                show("Kết quả sửa khác bản gốc quá nhiều, giữ nguyên đoạn đã cắt")
            else:
                log(f"Lỗi sửa chính tả: {e!r}")
                show("Không sửa được chính tả, clipboard giữ nguyên")
                notify_error("Không sửa được chính tả: mất mạng hoặc Gemini đang bận.\n"
                             "Ctrl+V sẽ dán lại đúng đoạn bạn đã cắt.")
        return 0
    finally:
        job.set()
        if animation:
            animation.join()
        save_gemini_usage()
        ui_call(lambda: panel and panel.refresh_engine())


def translation_worker():
    global active_job, busy_workers, pending, pending_task, retry_at
    while True:
        task, sequence, manual, job = jobs.get()
        if task == PROOFREAD:
            delay = do_proofread(sequence, job)
        else:
            delay = do_translate(task, sequence, manual, job)
        with tray_lock:
            busy_workers -= 1
            if active_job is job:
                active_job = None
                if delay and clipboard_sequence() == sequence:
                    pending = manual
                    pending_task = PROOFREAD if task == PROOFREAD else None
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


def classify_change(current_mode, trigger, is_cut, text, now, previous_text, previous_at):
    """Clipboard vừa đổi: "proofread" (vừa Ctrl+X), "debounce" / "translate" / "record" (xem
    classify_copy) hoặc "ignore" (đang tắt dịch và không phải cắt)."""
    if is_cut and text and any(char.isalpha() for char in text):
        return "proofread"  # cắt file trong Explorer, cắt ảnh...: không có chữ, không sửa
    if current_mode == OFF:
        return "ignore"
    return classify_copy(trigger, text, now, previous_text, previous_at)


def watcher():
    global seen_sequence, pending, pending_task, active_job, busy_workers, retry_at, clipboard_dirty
    global last_internal_text, last_copy_text, last_copy_at
    for _ in range(MAX_WORKERS):
        threading.Thread(target=translation_worker, daemon=True).start()
    while True:
        with tray_lock:
            retry_delay = max(0, retry_at - time.monotonic()) if pending is not None else None
        wake.wait(retry_delay)
        wake.clear()
        with tray_lock:
            sequence = clipboard_sequence()
            proofread_on = settings["proofread_on_cut"]
            if (mode != OFF or proofread_on) and clipboard_dirty:
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
                        is_cut = proofread_on and keywatch.cut_pending(now)
                        action = classify_change(mode, settings["trigger"], is_cut, current_text, now,
                                                 last_copy_text, last_copy_at)
                        pending_task = None
                        if action == "proofread":
                            pending, pending_task, retry_at = False, PROOFREAD, now + CUT_DEBOUNCE
                            last_copy_text = None
                        elif action == "debounce":
                            pending, retry_at = False, now + DEBOUNCE_SECONDS
                        elif action == "translate":
                            pending, retry_at = False, 0
                            last_copy_text = None  # lần bấm thứ ba bắt đầu lượt đếm mới
                        elif action == "record":
                            pending = None  # copy bình thường; chờ xem có Ctrl+C lần hai không
                            last_copy_text, last_copy_at = current_text, now
                        else:
                            pending = None  # đang tắt dịch: copy bình thường
                        refresh_icon()
            task = pending_task or mode
            if task != OFF and pending is not None and busy_workers < MAX_WORKERS and time.monotonic() >= retry_at:
                if task == PROOFREAD:
                    keywatch.consume_cut()  # các lần clipboard đổi sau đó là copy bình thường
                active_job = threading.Event()
                jobs.put((task, sequence, pending, active_job))
                busy_workers += 1
                pending, pending_task = None, None


def message_listener():
    """Cửa sổ ẩn: nhận báo clipboard thay đổi, phím Ctrl+X, và yêu cầu mở bảng từ bản chạy sau."""
    global clipboard_dirty, listener_hwnd
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    @callback_type
    def window_proc(hwnd, message, wparam, lparam):
        global clipboard_dirty
        if message == keywatch.WM_INPUT:
            keywatch.handle_raw_input(lparam)
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)  # Windows cần để dọn dữ liệu
        if message == WM_CLIPBOARDUPDATE:
            with tray_lock:
                clipboard_dirty = True
            wake.set()
            return 0
        if message == WM_KEYWATCH:
            if not keywatch.set_listening(hwnd, bool(wparam)) and wparam:
                log(f"Không bật được nhận phím Ctrl+X: {ctypes.WinError(ctypes.get_last_error())!r}")
            return 0
        if message == WM_SHOW_PANEL:
            ui_call(show_panel)
            return 0
        if message == WM_QUIT_APP:
            request_quit()
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
    listener_hwnd = hwnd
    apply_keywatch()
    message = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))


# ---------------------------------------------------------------- chế độ và phím tắt

def current_mode():
    with tray_lock:
        return mode


def change_mode(target, *, manual=False):
    global mode, seen_sequence, pending, pending_task, active_job, retry_at
    with tray_lock:
        mode = target
        seen_sequence = clipboard_sequence()
        if active_job:
            active_job.set()
            active_job = None
        pending, pending_task, retry_at = (True if manual else None), None, 0
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


def apply_keywatch():
    """Nghe phím Ctrl+X chỉ khi bật sửa chính tả (việc đăng ký chạy trên luồng cửa sổ ẩn)."""
    if listener_hwnd:
        user32.PostMessageW(listener_hwnd, WM_KEYWATCH, int(bool(settings["proofread_on_cut"])), 0)


def set_proofread(enabled):
    if not settings.update(proofread_on_cut=bool(enabled)):
        log("Không lưu được cài đặt")
    apply_keywatch()
    if icon:
        icon.update_menu()
    ui_call(lambda: panel and panel.refresh_checks())


def toggle_proofread(*_):
    """Mục menu khay: bật / tắt sửa chính tả khi nhấn Ctrl+X."""
    set_proofread(not settings["proofread_on_cut"])


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
    return f'"{executable}" "{LAUNCHER}"'


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
        if key == "proofread_on_cut":
            set_proofread(value)
            return
        settings.update(**{key: value})
        if key == "autostart":
            apply_autostart(value, force=True)
        elif key == "mymemory_email":
            apply_translator_settings()

    def set_gemini(self, **changes):
        settings.update(**changes)
        apply_translator_settings()
        if {"gemini_api_key", "gemini_backup_keys", "gemini_model"} & set(changes):
            threading.Thread(target=prepare_gemini, daemon=True).start()  # kiểm tra key / model mới ngay

    def set_excluded_apps(self, names, key="excluded_apps"):
        """key: "excluded_apps" (không dịch) hoặc "proofread_excluded_apps" (không sửa chính tả)."""
        names = filters.normalize_app_names(names)
        if names != settings[key]:
            settings.update(**{key: names})

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
        apply_keywatch()
        change_mode(settings["mode"])
        return register_hotkeys()

    def open_log_folder(self):
        folder = os.path.dirname(log_path())
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)

    def quit(self):
        request_quit()

    # ----- cài vào Windows -----
    def can_install(self):
        return getattr(sys, "frozen", False) and not installer.is_installed_copy()

    def install_location(self):
        exe = installer.installed_exe()
        return os.path.dirname(exe) if exe and installer.is_installed_copy() else None

    def restart_to_install(self):
        restart_to_install()

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
        target = updater.download_path(exe_path)  # Program Files: tải vào thư mục tạm của người dùng
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
               "Mở Bảng điều khiển và bấm dòng phiên bản trên cùng để cập nhật.")


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
    """Xử lý tham số của quy trình cập nhật. Trả về True nếu tiến trình này chỉ để cài bản cập nhật."""
    if "install-update" in args:
        try:
            if not updater.install_update(args["install-update"], args.get("wait-pid", [])):
                log("Chưa cài được bản cập nhật (có thể chưa cấp quyền quản trị), đã mở lại bản cũ")
        except Exception as e:
            log(f"Cài bản cập nhật thất bại: {e!r}")
        return True
    return False


# ---------------------------------------------------------------- cài vào Windows / gỡ cài đặt

def show_message(text, error=False):
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    (messagebox.showerror if error else messagebox.showinfo)(APP_NAME, text, parent=root)
    root.destroy()


def ask_install():
    from PIL import ImageTk

    from .panel import ask_install_options

    return ask_install_options(
        installer.default_install_dir(), installer.normalize_install_dir, installer.needs_admin,
        window_icon=lambda root: ImageTk.PhotoImage(icons.app_icon(64), master=root))


def own_pids():
    pids = [os.getpid()]
    if getattr(sys, "frozen", False):
        pids.append(os.getppid())  # bản exe 1 file: tiến trình cha cũng giữ file exe
    return ",".join(map(str, pids))


def launch_installed(target, args):
    """Mở bản đã cài (chờ tiến trình này thoát để nhường quyền "chỉ chạy một bản")."""
    extra = ["--installed"]
    if args.get("updated"):
        extra += ["--updated", args["updated"]]
    if args.get("cleanup"):
        extra += ["--cleanup", args["cleanup"]]
    updater.launch([target, *extra, "--wait-pid", own_pids()])


def maybe_install(args):
    """Bản exe chưa cài: cài vào Windows rồi chuyển sang bản đã cài. True = tiến trình này thoát."""
    if not getattr(sys, "frozen", False) or args.get("portable") or installer.is_installed_copy():
        return False
    existing = installer.installed_exe()
    try:
        if existing and os.path.exists(existing):
            # Đã cài rồi: bản này mới hơn thì nâng cấp bản đã cài, sau đó mở bản đã cài.
            if installer.version_tuple(APP_VERSION) > installer.version_tuple(installer.installed_version()):
                target = installer.install_to(os.path.dirname(existing), desktop=False)
            else:
                target = existing
        elif "install-to" in args:  # cài không hỏi, dùng cho dòng lệnh / quản trị viên
            target = installer.install_to(installer.normalize_install_dir(args["install-to"]),
                                          desktop=not args.get("no-desktop"))
        else:
            if settings["run_without_install"]:
                return False
            choice = ask_install()
            if choice is None:
                return True  # Hủy: thoát
            if choice[0] == "portable":
                settings.update(run_without_install=True)
                return False
            _, folder, desktop = choice
            target = installer.install_to(folder, desktop=desktop)
    except installer.InstallCancelled:
        show_message("Chưa cài được vì chưa cấp quyền quản trị.\n"
                     "App sẽ chạy tạm không cài đặt; chạy lại file để cài, hoặc chọn thư mục khác.")
        return False
    except OSError as e:
        log(f"Cài đặt thất bại: {e!r}")
        show_message(f"Cài đặt thất bại: {e}\nApp sẽ chạy tạm không cài đặt.", error=True)
        return False
    launch_installed(target, args)
    return True


def stop_running_instance(timeout=10.0):
    hwnd = user32.FindWindowExW(HWND_MESSAGE, None, LISTENER_CLASS, None)
    if not hwnd:
        return
    user32.PostMessageW(hwnd, WM_QUIT_APP, 0, 0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and user32.FindWindowExW(HWND_MESSAGE, None, LISTENER_CLASS, None):
        time.sleep(0.2)
    time.sleep(1.0)  # chờ tiến trình cha của exe 1 file thoát hẳn


def remove_autostart_for(folder):
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            paths = re.findall(r'"([^"]+)"', winreg.QueryValueEx(key, RUN_VALUE)[0])
            if paths and os.path.normcase(os.path.dirname(os.path.abspath(paths[0]))) == os.path.normcase(
                    os.path.abspath(folder)):
                winreg.DeleteValue(key, RUN_VALUE)
    except OSError:
        pass


def uninstall_app(quiet=False):
    """Gỡ cài đặt (Settings → Apps gọi exe với --uninstall)."""
    import shutil

    exe = installer.installed_exe() or sys.executable
    folder = os.path.dirname(exe)
    root = messagebox = None
    if not quiet:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        if not messagebox.askyesno(f"Gỡ {APP_NAME}", f"Gỡ {APP_NAME} khỏi máy?", parent=root):
            root.destroy()
            return
    stop_running_instance()
    installer.remove_shortcuts(exe)
    installer.unregister()
    remove_autostart_for(folder)
    if root is not None and messagebox.askyesno(
            APP_NAME, "Xóa luôn cài đặt của bạn (chế độ, lĩnh vực, phím tắt, API key…)?\n"
                      "Chọn No để giữ lại, lần sau cài lại sẽ dùng tiếp.", parent=root):
        shutil.rmtree(config_dir(), ignore_errors=True)
        shutil.rmtree(os.path.join(os.environ.get("LOCALAPPDATA") or "", "TranslateVietnam"), ignore_errors=True)
    try:
        scheduled = installer.remove_files_later(folder) is not None
    except OSError:
        scheduled = False
    if root is not None:
        if not scheduled:
            messagebox.showinfo(APP_NAME, f"Đã gỡ {APP_NAME}. Còn file trong thư mục\n{folder}\n"
                                          "chưa xóa được (chưa cấp quyền quản trị), bạn có thể tự xóa.",
                                parent=root)
        root.destroy()


def restart_to_install():
    """Nút "Cài vào máy…": bật lại câu hỏi cài đặt rồi khởi động lại app."""
    settings.update(run_without_install=False)
    updater.launch([sys.executable, "--wait-pid", own_pids()])
    request_quit()


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
        pystray.MenuItem("Sửa chính tả khi nhấn Ctrl+X", toggle_proofread,
                         checked=lambda item: settings["proofread_on_cut"]),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Bảng điều khiển…", lambda: ui_call(show_panel)),
        pystray.MenuItem("Hướng dẫn", lambda: ui_call(lambda: panel and panel.show_help())),
        pystray.MenuItem("Giới thiệu và liên hệ", lambda: ui_call(lambda: panel and panel.show_about())),
        pystray.MenuItem(
            lambda item: f"Cập nhật lên bản {available_release.version}…" if available_release else "Cập nhật",
            lambda: ui_call(lambda: panel and available_release and panel.confirm_update(available_release)),
            visible=lambda item: available_release is not None,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Thoát", request_quit),
    )


def on_ready(icon_, hotkey_errors, updated_to=None, installed=False):
    icon_.visible = True
    threading.Thread(target=message_listener, daemon=True).start()
    threading.Thread(target=watcher, daemon=True).start()
    threading.Thread(target=gemini_keeper, daemon=True).start()
    threading.Thread(target=update_checker, daemon=True).start()
    if updated_to:
        notify(f"Đã cập nhật {APP_NAME} lên phiên bản {updated_to}.")
    elif installed:
        notify(f"Đã cài {APP_NAME} vào máy.\nLần sau mở từ Start Menu (gõ \"Translate\") hoặc lối tắt ngoài Desktop.")
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
    if "copy-to" in args:  # tiến trình phụ có quyền quản trị: chỉ chép file exe rồi thoát
        sys.exit(0 if updater.copy_self_to(args["copy-to"]) else 1)
    if handle_update_args(args):
        return  # tiến trình cài bản cập nhật: chép file xong là thoát
    if args.get("uninstall"):
        set_dpi_awareness()
        uninstall_app(quiet=bool(args.get("quiet")))
        return
    if args.get("wait-pid"):
        updater.wait_for_pids(args["wait-pid"])  # chờ bản trước nhường chỗ (sau khi cài / cập nhật)
    if not acquire_single_instance():
        return  # đã có bản đang chạy: bản đó sẽ mở Bảng điều khiển
    set_dpi_awareness()
    settings = Settings()
    first_run = settings.first_run
    if first_run and not settings.save():
        log("Không lưu được cài đặt lần đầu")
    if maybe_install(args):
        return  # đã chuyển sang bản cài trong Windows (hoặc người dùng bấm Hủy)
    if "cleanup" in args:
        threading.Thread(target=updater.cleanup, args=(args["cleanup"],), daemon=True).start()
    installed_copy = installer.is_installed_copy()
    if installed_copy:
        try:
            installer.repair()
        except OSError as e:
            log(f"Không cập nhật được mục cài đặt: {e!r}")
    mode = settings["mode"]
    translator.load_gemini_usage(settings["gemini_usage_day"], settings["gemini_usage_count"])
    apply_translator_settings()
    apply_autostart(settings["autostart"], force=installed_copy)  # bản đã cài luôn là bản tự khởi động
    seen_sequence = clipboard_sequence()
    for target in icons.MODE_COLOR:
        icons.tray_icon(target, 0)
    hotkey_manager = hotkeys.HotkeyManager()
    hotkey_errors = register_hotkeys()

    import tkinter as tk
    from PIL import ImageTk

    from .panel import ControlPanel

    tk_root = tk.Tk()
    tk_root.withdraw()
    window_icon = ImageTk.PhotoImage(icons.app_icon(64))
    panel = ControlPanel(tk_root, Controller(), window_icon)
    icon = pystray.Icon("translate-vn", icons.tray_icon(mode, 0),
                        f"{APP_NAME}: {MODE_LABEL[mode]}", build_menu())
    icon.run_detached(setup=lambda icon_: on_ready(icon_, hotkey_errors, args.get("updated"),
                                                   bool(args.get("installed"))))
    if first_run or settings["show_panel_on_start"] or (args.get("installed") and not args.get("updated")):
        tk_root.after(300, show_panel)
    tk_root.after(100, pump_ui)
    try:
        tk_root.mainloop()
    finally:
        tk_root = None
        icon.stop()


if __name__ == "__main__":
    main()
