"""Khung bản dịch nhanh: hiện ngay dưới vùng màn hình vừa chọn, giống các app tra từ.

Khung không lấy focus của cửa sổ đang đọc. Giữ chuột ở dòng trên cùng (hoặc mép khung) rồi kéo
để dời khung đi chỗ khác. Đóng khi nhấn Esc, bấm chuột ra ngoài khung hoặc bấm "Đóng".
Mọi hàm ở đây chạy trên luồng giao diện (tkinter).
"""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from . import selection

BG = "#ffffff"
BORDER = "#8c96a3"
FG = "#1f2328"
MUTED = "#5f6b7a"
ACCENT = "#0b57d0"
SELECT_BG = "#cfe0fc"
MIN_WIDTH = 240  # điểm ảnh ở 100% (96 DPI)
MAX_WIDTH = 760  # khung rộng theo vùng đã chọn, tối đa chừng này
MAX_LINES = 16  # dài hơn thì cuộn
POLL_MS = 40
LOADING = "Đang đọc và dịch…"


def wrapped_line_count(text, width, measure):
    """Số dòng hiển thị khi ngắt theo từ trong khung rộng width điểm ảnh.

    measure(chuỗi) trả về độ rộng điểm ảnh. Chỉ là ước lượng ban đầu; khung đo lại sau khi hiện.
    """
    space = measure(" ")
    count = 0
    for paragraph in text.split("\n"):
        count += 1
        used = 0
        for word in paragraph.split(" "):
            size = measure(word)
            if used and used + space + size > width:
                count += 1
                used = size
            else:
                used += (space if used else 0) + size
            while used > width:  # một từ dài hơn cả khung: Tk ngắt giữa từ
                count += 1
                used -= width
    return count


class QuickPopup:
    def __init__(self, root, copy_text):
        self.root = root
        self.copy_text = copy_text  # chép bản dịch vào clipboard (app lo việc không dịch lại lần nữa)
        self.win = None
        self.request = 0
        self.anchor = (0, 0, 0, 0)
        self.width_hint = None
        self.result = ""
        self._shown = False
        self._was_pressed = False
        self._poll_job = None
        self._lines = 1
        self._moved = False  # người dùng đã kéo khung đi chỗ khác: không tự đặt lại dưới vùng nữa
        self._drag_from = None

    # ---------- dựng khung ----------
    def _build(self):
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        self.font = tkfont.Font(root=self.root, family="Segoe UI", size=11)
        small = tkfont.Font(root=self.root, family="Segoe UI", size=9)
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BORDER)
        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        head = tk.Frame(body, bg=BG)
        head.pack(fill="x", padx=(10, 8), pady=(6, 0))
        self.title = tk.Label(head, bg=BG, fg=MUTED, font=small, anchor="w")
        self.title.pack(side="left")
        self._link(head, "Đóng", small, self.close).pack(side="right")
        self.copy_button = self._link(head, "Sao chép", small, self._copy)
        area = tk.Frame(body, bg=BG)
        area.pack(fill="both", expand=True, padx=(10, 4), pady=(2, 8))
        self.text = tk.Text(area, wrap="word", font=self.font, bg=BG, fg=FG, relief="flat", bd=0,
                            highlightthickness=0, padx=0, pady=0, insertwidth=0, width=30, height=1,
                            selectbackground=SELECT_BG, selectforeground=FG, inactiveselectbackground=SELECT_BG,
                            spacing1=1, spacing3=1)
        self.scroll = ttk.Scrollbar(area, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=self.scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        for widget in (body, head, self.title, area):  # chữ bản dịch vẫn bôi đen được, không kéo khung
            widget.configure(cursor="fleur")
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
        win.bind("<Escape>", lambda e: self.close())
        self.win = win

    def _link(self, parent, text, font, command):
        label = tk.Label(parent, text=text, bg=BG, fg=ACCENT, font=font, cursor="hand2")
        label.bind("<Button-1>", lambda e: command())
        return label

    # ---------- hiển thị ----------
    def open(self, request, anchor, width_hint=None):
        """Lần dịch mới: hiện "Đang đọc và dịch…" ngay dưới vùng anchor (trái, trên, phải, dưới)."""
        self.request, self.anchor, self.width_hint = request, anchor, width_hint
        self._moved = False  # lần dịch mới: hiện lại ngay dưới vùng mới chọn
        self._render(LOADING, "", done=False)

    def finish(self, request, message, title=""):
        """Bản dịch (hoặc thông báo lỗi) của lần dịch request."""
        if request != self.request or not self._shown:
            return  # đã có lần dịch mới hơn, hoặc người dùng đã đóng khung
        self._render(message, title, done=True)

    def _render(self, message, title, *, done):
        if not self.win:
            self._build()
        self.result = message if done else ""
        self.title.configure(text=title)
        if done and message:
            self.copy_button.configure(text="Sao chép")
            self.copy_button.pack(side="right", padx=(0, 14))
        else:
            self.copy_button.pack_forget()
        text = self.text
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", message)
        text.configure(state="disabled")
        text.yview_moveto(0)
        smallest, largest = int(MIN_WIDTH * self.scale), int(MAX_WIDTH * self.scale)
        cap = max(smallest, min(largest, self.width_hint or largest))
        longest = max(self.font.measure(line) for line in message.split("\n"))
        width = max(smallest, min(cap, longest + 4))
        zero = self.font.measure("0") or 8
        chars = max(1, -(-width // zero))
        text.configure(width=chars)
        self._set_lines(wrapped_line_count(message, chars * zero, self.font.measure))
        if not self._shown:
            self._shown = True
            self._was_pressed = self._mouse_pressed()
            self.win.deiconify()
            self._poll_job = self.win.after(POLL_MS, self._poll)
        self.win.lift()
        self.win.after_idle(self._check_lines)

    def _set_lines(self, lines):
        self._lines = max(1, lines)
        self.text.configure(height=min(MAX_LINES, self._lines))
        if self._lines > MAX_LINES:
            self.scroll.pack(side="right", fill="y")
        else:
            self.scroll.pack_forget()
        self._place()

    def _check_lines(self):
        """Đo lại số dòng thật sau khi khung đã hiện (ước lượng ban đầu có thể lệch một dòng)."""
        if not self._shown:
            return
        try:
            counted = self.text.count("1.0", "end", "update", "displaylines")
        except tk.TclError:
            return
        if isinstance(counted, tuple):
            counted = counted[0] if counted else 0
        counted = int(counted or 0)
        if counted and counted != self._lines:
            self._set_lines(counted)

    def _place(self):
        self.win.update_idletasks()
        size = (self.win.winfo_reqwidth(), self.win.winfo_reqheight())
        if self._moved:
            x, y = self.win.winfo_rootx(), self.win.winfo_rooty()
            work = selection.work_area(x + size[0] // 2, y + min(size[1] // 2, 20))
            x, y = selection.keep_on_screen((x, y), size, work)
        else:
            left, top, right, bottom = self.anchor
            work = selection.work_area((left + right) // 2, (top + bottom) // 2)
            x, y = selection.popup_position(self.anchor, size, work, gap=int(8 * self.scale))
        self.win.geometry(f"+{x}+{y}")

    # ---------- kéo khung ----------
    def _drag_start(self, event):
        self._drag_from = (event.x_root - self.win.winfo_rootx(), event.y_root - self.win.winfo_rooty())

    def _drag_move(self, event):
        if not self._drag_from:
            return
        dx, dy = self._drag_from
        self._moved = True
        self.win.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _drag_end(self, event):
        self._drag_from = None

    # ---------- đóng khung ----------
    def _mouse_pressed(self):
        return any(selection.key_down(vk) for vk in (selection.VK_LBUTTON, selection.VK_RBUTTON,
                                                     selection.VK_MBUTTON))

    def _inside(self, point):
        x, y = point
        left, top = self.win.winfo_rootx(), self.win.winfo_rooty()
        return left <= x < left + self.win.winfo_width() and top <= y < top + self.win.winfo_height()

    def _poll(self):
        """Esc hoặc bấm chuột ra ngoài khung: đóng (không cần lấy focus của cửa sổ đang đọc)."""
        self._poll_job = None
        if not self._shown:
            return
        if selection.key_down(selection.VK_ESCAPE):
            self.close()
            return
        pressed = self._mouse_pressed()
        if pressed and not self._was_pressed and not self._inside(selection.cursor_pos()):
            self.close()
            return
        self._was_pressed = pressed
        self._poll_job = self.win.after(POLL_MS, self._poll)

    def close(self):
        if self._poll_job:
            self.win.after_cancel(self._poll_job)
            self._poll_job = None
        self._shown = False
        if self.win:
            self.win.withdraw()

    def _copy(self):
        if self.result:
            self.copy_text(self.result)
            self.copy_button.configure(text="Đã chép")
            self.win.after(1200, lambda: self.copy_button.winfo_exists() and self.copy_button.configure(
                text="Sao chép"))
