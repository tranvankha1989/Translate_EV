"""Chọn vùng màn hình cần dịch, giống công cụ chụp màn hình của Windows.

Màn hình bị phủ mờ, con trỏ thành dấu chữ thập; kéo chuột quanh đoạn chữ, vùng đang chọn
hiện rõ. Thả chuột: chụp vùng đó. Esc hoặc chuột phải: hủy. Chạy trên luồng giao diện (tkinter).
"""
import tkinter as tk

from PIL import ImageGrab

from . import selection

KEY = "#ff00fe"  # màu "trong suốt": vùng đang chọn không bị phủ mờ
SHADE = "#0b0f14"
ALPHA = 0.45
OUTLINE = "#4da3ff"
MIN_SIZE = 6  # điểm ảnh: kéo ngắn hơn coi như bấm nhầm, hủy
HIDE_MS = 80  # chờ lớp phủ biến mất khỏi màn hình rồi mới chụp
POLL_MS = 40


def normalize(start, end):
    """Hai điểm kéo chuột -> (trái, trên, phải, dưới)."""
    (x0, y0), (x1, y1) = start, end
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


class RegionPicker:
    def __init__(self, root):
        self.root = root
        self.win = None
        self.active = False
        self.on_done = None  # hàm(vùng, ảnh): vùng None khi hủy
        self._start = None
        self._origin = (0, 0)
        self._poll_job = None

    def _build(self):
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", ALPHA)
        win.attributes("-transparentcolor", KEY)
        canvas = tk.Canvas(win, bg=SHADE, highlightthickness=0, bd=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        canvas.bind("<ButtonPress-1>", self._press)
        canvas.bind("<B1-Motion>", self._drag)
        canvas.bind("<ButtonRelease-1>", self._release)
        canvas.bind("<ButtonPress-3>", lambda e: self.cancel())
        win.bind("<Escape>", lambda e: self.cancel())
        self.win, self.canvas = win, canvas
        self.rect = canvas.create_rectangle(0, 0, 0, 0, fill=KEY, outline=OUTLINE, width=2, state="hidden")
        self.hint_box = canvas.create_rectangle(0, 0, 0, 0, fill="#000000", outline="")
        self.hint = canvas.create_text(0, 0, fill="#ffffff", font=("Segoe UI", 13),
                                       text="Kéo chuột quanh đoạn chữ cần dịch   ·   Esc: hủy")

    def start(self, on_done):
        """Hiện lớp phủ trên mọi màn hình. Đang chọn dở thì bỏ qua lần gọi mới."""
        if self.active:
            return
        if not self.win:
            self._build()
        self.on_done = on_done
        x, y, width, height = selection.virtual_screen()
        self._origin, self._start = (x, y), None
        self.win.geometry(f"{width}x{height}+{x}+{y}")
        self.canvas.itemconfigure(self.rect, state="hidden")
        cx, cy = selection.cursor_pos()
        left, top, right, _ = selection.work_area(cx, cy)
        hx, hy = (left + right) // 2 - x, top + 48 - y
        self.canvas.coords(self.hint, hx, hy)
        x0, y0, x1, y1 = self.canvas.bbox(self.hint) or (hx, hy, hx, hy)
        self.canvas.coords(self.hint_box, x0 - 16, y0 - 8, x1 + 16, y1 + 8)
        self.canvas.itemconfigure(self.hint, state="normal")
        self.canvas.itemconfigure(self.hint_box, state="normal")
        self.active = True
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self._poll_job = self.win.after(POLL_MS, self._poll)

    def _poll(self):
        """Esc khi lớp phủ chưa lấy được focus (Windows đôi khi không cho app chạy nền lấy focus)."""
        self._poll_job = None
        if not self.active:
            return
        if selection.key_down(selection.VK_ESCAPE):
            self.cancel()
            return
        self._poll_job = self.win.after(POLL_MS, self._poll)

    def _press(self, event):
        self._start = (event.x_root, event.y_root)
        self.canvas.itemconfigure(self.hint, state="hidden")
        self.canvas.itemconfigure(self.hint_box, state="hidden")
        self.canvas.coords(self.rect, event.x, event.y, event.x, event.y)
        self.canvas.itemconfigure(self.rect, state="normal")

    def _drag(self, event):
        if self._start is None:
            return
        ox, oy = self._origin
        left, top, right, bottom = normalize(self._start, (event.x_root, event.y_root))
        self.canvas.coords(self.rect, left - ox, top - oy, right - ox, bottom - oy)

    def _release(self, event):
        if self._start is None:
            return
        box = normalize(self._start, (event.x_root, event.y_root))
        if box[2] - box[0] < MIN_SIZE or box[3] - box[1] < MIN_SIZE:
            self.cancel()
        else:
            self._finish(box)

    def cancel(self):
        self._finish(None)

    def _finish(self, box):
        if not self.active:
            return
        self.active = False
        if self._poll_job:
            self.win.after_cancel(self._poll_job)
            self._poll_job = None
        self.win.withdraw()
        if box is None:
            self.on_done(None, None)
            return
        self.root.update_idletasks()
        self.win.after(HIDE_MS, lambda: self._grab(box))

    def _grab(self, box):
        try:
            image = ImageGrab.grab(bbox=box, all_screens=True)
        except Exception:
            image = None
        self.on_done(box, image)
