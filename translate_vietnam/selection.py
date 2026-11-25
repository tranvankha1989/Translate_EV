"""Tọa độ màn hình cho tính năng dịch vùng màn hình: con trỏ chuột, vùng làm việc của từng
màn hình, vị trí khung bản dịch, và nối dòng chữ bị ngắt khi đọc từ tài liệu."""
import ctypes
import re
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]

VK_LBUTTON, VK_RBUTTON, VK_MBUTTON, VK_ESCAPE = 0x01, 0x02, 0x04, 0x1B
MONITOR_DEFAULTTONEAREST = 2
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79


def key_down(vk) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def cursor_pos():
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def virtual_screen():
    """(x, y, rộng, cao) của toàn bộ các màn hình gộp lại."""
    return tuple(user32.GetSystemMetrics(i) for i in (SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
                                                       SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN))


def work_area(x, y):
    """Vùng làm việc (trừ thanh taskbar) của màn hình chứa điểm (x, y)."""
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), MONITOR_DEFAULTTONEAREST)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def popup_position(anchor, size, work, gap=8):
    """Góc trên trái của khung bản dịch: ngay dưới vùng anchor (trái, trên, phải, dưới).

    Dưới không đủ chỗ thì đặt phía trên vùng; cả hai đều không đủ thì sát đáy màn hình.
    """
    (a_left, a_top, _, a_bottom), (width, height), (left, top, right, bottom) = anchor, size, work
    px = max(left + 4, min(a_left, right - width - 4))
    py = a_bottom + gap
    if py + height > bottom - 4:
        py = a_top - gap - height
        if py < top + 4:
            py = max(top + 4, bottom - 4 - height)
    return px, py


def keep_on_screen(position, size, work, margin=4):
    """Khung đã được kéo đi chỗ khác rồi đổi cỡ (có bản dịch): giữ trọn trong vùng làm việc."""
    (x, y), (width, height), (left, top, right, bottom) = position, size, work
    x = max(left + margin, min(x, right - width - margin))
    y = max(top + margin, min(y, bottom - height - margin))
    return x, y


# ---------------------------------------------------------------- nối dòng chữ bị ngắt
_LIST_ITEM = re.compile(r"^(?:[-•*·▪●◦–]\s|\(?\d{1,3}[.)]\s|\(?[a-zA-Z][.)]\s)")
_SENTENCE_END = (".", "!", "?", ":", ";", "…")


def unwrap_lines(text: str) -> str:
    """Tài liệu (PDF...) ngắt dòng giữa câu: nối lại để dịch trôi chảy.

    Giữ đoạn trống, gạch đầu dòng / mục đánh số và chỗ xuống dòng sau dấu kết câu.
    """
    out = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw.split())
        if not line:
            if out and out[-1]:
                out.append("")
            continue
        if out and out[-1] and not _LIST_ITEM.match(line):
            prev = out[-1]
            if prev.endswith("-") and len(prev) > 1 and prev[-2].isalpha() and line[0].islower():
                out[-1] = prev + line  # từ bị ngắt bằng gạch nối ở cuối dòng
                continue
            if not prev.endswith(_SENTENCE_END) or line[0].islower():
                out[-1] = prev + " " + line
                continue
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)
