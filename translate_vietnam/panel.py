"""Bảng điều khiển: cửa sổ gọn, có nút "Mở rộng" để hiện thêm tùy chọn."""
import ctypes
import os
import re
import threading
import tkinter as tk
import webbrowser
from ctypes import wintypes
from tkinter import messagebox, ttk

from . import hotkeys
from . import updater
from .config import (APP_NAME, APP_VERSION, ASSETS_DIR, AUTHOR_NAME, AUTHOR_ZALO_URL, DESIGN_CREDIT, DOMAINS,
                     GEMINI_MODELS, HOLD_KEYS, HOLD_MS_CHOICES, MAX_BACKUP_KEYS, model_from_label, model_label)

MODE_CHOICES = [("auto", "Tự động hai chiều"), ("vi", "Sang tiếng Việt"), ("en", "Sang tiếng Anh"), ("off", "Tắt")]
MODE_BY_LABEL = {label: mode for mode, label in MODE_CHOICES}
TRIGGER_CHOICES = [("double", "Nhấn Ctrl+C hai lần"), ("single", "Mỗi lần copy")]
TRIGGER_BY_LABEL = {label: trigger for trigger, label in TRIGGER_CHOICES}
LABEL_BY_TRIGGER = dict(TRIGGER_CHOICES)
CURSOR_CHOICES = [("spin", "Vòng xoay"), ("arrow", "Mũi tên kèm vòng xoay"), ("off", "Không đổi")]
CURSOR_BY_LABEL = {label: style for style, label in CURSOR_CHOICES}
LABEL_BY_CURSOR = dict(CURSOR_CHOICES)
LABEL_BY_MODE = dict(MODE_CHOICES)
HOLD_KEY_BY_LABEL = {label: key for key, label in HOLD_KEYS.items()}


def hold_ms_label(ms):
    """600 -> "0,6 giây", 1000 -> "1 giây"."""
    return f"{ms / 1000:g}".replace(".", ",") + " giây"


HOLD_MS_BY_LABEL = {hold_ms_label(ms): ms for ms in HOLD_MS_CHOICES}
LABEL_WIDTH = 13  # cột nhãn của các phần trong Bảng điều khiển thẳng hàng nhau
HOTKEY_NAMES = {"hotkey_toggle": "Bật/tắt dịch", "hotkey_vi": "Sang tiếng Việt", "hotkey_en": "Sang tiếng Anh",
                "hotkey_region": "Dịch vùng màn hình"}
GEMINI_KEY_URL = "https://aistudio.google.com/apikey"
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

_user32 = ctypes.WinDLL("user32")
_user32.GetKeyState.argtypes = [ctypes.c_int]
_user32.GetKeyState.restype = ctypes.c_short


def _down(vk):
    return bool(_user32.GetKeyState(vk) & 0x8000)


class HotkeyEntry(ttk.Entry):
    """Ô nhập phím tắt: bấm vào ô rồi nhấn tổ hợp phím mới."""

    def __init__(self, master, panel, key):
        self.var = tk.StringVar()
        super().__init__(master, textvariable=self.var, width=24, justify="center")
        self.panel, self.key = panel, key
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self.bind("<KeyPress>", self._key)
        for sequence in ("<<Paste>>", "<<Cut>>", "<Button-2>"):
            self.bind(sequence, lambda e: "break")
        self.refresh()

    def refresh(self):
        self.var.set(hotkeys.display(self.panel.c.get(self.key)))

    def _focus_in(self, _event):
        # Tạm nhả phím toàn cục, nếu không Windows sẽ "nuốt" phím trước khi ô này nhận được.
        self.panel.c.suspend_hotkeys()
        self.var.set("Nhấn phím mới…")

    def _focus_out(self, _event):
        self.refresh()
        self.panel.show_hotkey_errors(self.panel.c.resume_hotkeys())

    def _key(self, event):
        vk = event.keycode  # trên Windows, Tk trả về virtual-key code
        ctrl, alt, shift = _down(0x11), _down(0x12), _down(0x10)
        win = _down(0x5B) or _down(0x5C)
        if vk in _MODIFIER_VKS:
            return "break"
        if vk == 0x09 and not (ctrl or alt or win):
            return None  # Tab: chuyển sang ô khác
        if vk == 0x1B:  # Esc: giữ phím cũ
            self.panel.root.focus_set()
            return "break"
        if vk in (0x08, 0x2E):  # Backspace / Delete: bỏ phím
            spec = ""
        else:
            try:
                spec = hotkeys.spec_from_vk(vk, ctrl=ctrl, alt=alt, shift=shift, win=win)
            except ValueError as e:
                self.panel.set_status(str(e), error=True)
                return "break"
        error = self.panel.c.set_hotkey(self.key, spec)
        if error:
            self.panel.set_status(error, error=True)
        else:
            self.panel.set_status(f"{HOTKEY_NAMES[self.key]}: {hotkeys.display(spec)}")
        self.panel.root.focus_set()  # rời ô -> đăng ký lại phím
        return "break"


class ControlPanel:
    def __init__(self, root, controller, window_icon=None):
        self.root, self.c = root, controller
        self.expanded = False
        self.positioned = False
        self.bool_vars = {}
        self.hotkey_entries = []
        root.title(f"{APP_NAME} {APP_VERSION}")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        if window_icon is not None:
            root.iconphoto(True, window_icon)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Hint.TLabel", foreground="#666666")
        style.configure("Link.TLabel", foreground="#0b57d0")
        style.configure("Error.TLabel", foreground="#b3261e")

        outer = ttk.Frame(root, padding=10)
        outer.grid(sticky="nsew")
        outer.columnconfigure(0, weight=1)
        self._build_header(outer)
        self._build_basic(outer)
        self.ext = ttk.Frame(outer)
        self.ext.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self._build_extended(self.ext)
        self.ext.grid_remove()
        self.refresh_all()

    # ---------- dựng giao diện ----------
    def _build_header(self, outer):
        """Dòng trên cùng: tên app và phiên bản. Có bản mới thì nhấp nháy, bấm vào là cập nhật."""
        bg = ttk.Style(self.root).lookup("TFrame", "background") or "SystemButtonFace"
        self._header_bg = bg
        header = tk.Frame(outer, bg=bg)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header.columnconfigure(0, weight=1)
        name = tk.Frame(header, bg=bg)
        name.grid(row=0, column=0, sticky="w")
        tk.Label(name, text=APP_NAME, font=("Segoe UI", 10, "bold"), bg=bg).pack(side="left")
        tk.Label(name, text=DESIGN_CREDIT, font=("Segoe UI", 8), fg="#666666", bg=bg).pack(
            side="left", padx=(6, 0), pady=(2, 0))
        self.version_badge = tk.Label(header, font=("Segoe UI", 9), bd=0, padx=8, pady=2)
        self.version_badge.grid(row=0, column=1, sticky="e")
        self.version_badge.bind("<Button-1>", lambda e: self._badge_clicked())
        self.version_badge.bind("<Enter>", lambda e: self._badge_hover(True))
        self.version_badge.bind("<Leave>", lambda e: self._badge_hover(False))
        self.update_release = None
        self.updating = False
        self.checking = False
        self._tip = None
        self._tip_job = None
        self._toast = None
        self._toast_job = None
        self._update_dialog = None
        self._about_dialog = None
        self._blink_job = None
        self._blink_on = False
        self._badge_normal()

    def _build_basic(self, outer):
        basic = ttk.Frame(outer)
        basic.grid(row=1, column=0, sticky="ew")
        basic.columnconfigure(0, weight=1)

        # Chung cho cả dịch chữ và dịch hình ảnh
        common = ttk.LabelFrame(basic, text="Chung", padding=(10, 4))
        common.grid(row=0, column=0, sticky="ew")
        common.columnconfigure(1, weight=1)
        ttk.Label(common, text="Lĩnh vực:", width=LABEL_WIDTH).grid(row=0, column=0, sticky="w", pady=3)
        self.domain_var = tk.StringVar()
        domain_box = ttk.Combobox(common, textvariable=self.domain_var, values=DOMAINS, width=26)
        domain_box.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=3)
        for sequence in ("<<ComboboxSelected>>", "<FocusOut>", "<Return>"):
            domain_box.bind(sequence, lambda e: self._save_gemini())
        self.engine_label = ttk.Label(common, style="Hint.TLabel", wraplength=300)
        self.engine_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 2))

        # Dịch chữ: bôi đen rồi copy
        text = ttk.LabelFrame(basic, text="Dịch chữ (bôi đen rồi copy)", padding=(10, 4))
        text.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        text.columnconfigure(1, weight=1)

        ttk.Label(text, text="Chế độ dịch:", width=LABEL_WIDTH).grid(row=0, column=0, sticky="w", pady=3)
        self.mode_var = tk.StringVar()
        mode_box = ttk.Combobox(text, textvariable=self.mode_var, state="readonly", width=26,
                                values=[label for _, label in MODE_CHOICES])
        mode_box.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=3)
        mode_box.bind("<<ComboboxSelected>>", lambda e: self.c.set_mode(MODE_BY_LABEL[self.mode_var.get()]))

        ttk.Label(text, text="Dịch khi:", width=LABEL_WIDTH).grid(row=1, column=0, sticky="w", pady=3)
        self.trigger_var = tk.StringVar()
        trigger_box = ttk.Combobox(text, textvariable=self.trigger_var, state="readonly", width=26,
                                   values=[label for _, label in TRIGGER_CHOICES])
        trigger_box.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=3)
        trigger_box.bind("<<ComboboxSelected>>",
                         lambda e: self.c.set_option("trigger", TRIGGER_BY_LABEL[self.trigger_var.get()]))

        ttk.Label(text, text="Phím bật/tắt:", width=LABEL_WIDTH).grid(row=2, column=0, sticky="w", pady=3)
        entry = HotkeyEntry(text, self, "hotkey_toggle")
        entry.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=3)
        self.hotkey_entries.append(entry)

        self._check(text, "proofread_on_cut", "Nhấn Ctrl+X: sửa chính tả, câu văn (cần Gemini)").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(2, 2))

        # Dịch hình ảnh: giữ phím, kéo khung quanh vùng chữ trên màn hình
        image = ttk.LabelFrame(basic, text="Dịch hình ảnh (vùng trên màn hình)", padding=(10, 4))
        image.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        image.columnconfigure(1, weight=1)

        self._check(image, "region_on_hold", "Giữ phím để chọn vùng màn hình cần dịch").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.bool_vars["region_on_hold"].trace_add("write", lambda *a: self._sync_region_state())

        ttk.Label(image, text="Phím giữ:", width=LABEL_WIDTH).grid(row=1, column=0, sticky="w", pady=3)
        self.hold_key_var = tk.StringVar()
        self.hold_key_box = ttk.Combobox(image, textvariable=self.hold_key_var, state="readonly", width=26,
                                         values=list(HOLD_KEYS.values()))
        self.hold_key_box.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=3)
        self.hold_key_box.bind("<<ComboboxSelected>>", lambda e: self.c.set_option(
            "region_hold_key", HOLD_KEY_BY_LABEL[self.hold_key_var.get()]))

        ttk.Label(image, text="Thời gian giữ:", width=LABEL_WIDTH).grid(row=2, column=0, sticky="w", pady=3)
        self.hold_ms_var = tk.StringVar()
        self.hold_ms_box = ttk.Combobox(image, textvariable=self.hold_ms_var, state="readonly", width=26,
                                        values=list(HOLD_MS_BY_LABEL))
        self.hold_ms_box.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(3, 4))
        self.hold_ms_box.bind("<<ComboboxSelected>>", lambda e: self.c.set_option(
            "region_hold_ms", HOLD_MS_BY_LABEL[self.hold_ms_var.get()]))


        system = ttk.Frame(outer)
        system.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self._check(system, "autostart", "Khởi động cùng Windows").grid(row=0, column=0, sticky="w")
        self._check(system, "show_panel_on_start", "Hiện bảng điều khiển khi khởi động").grid(
            row=1, column=0, sticky="w")

        buttons = ttk.Frame(outer)
        buttons.grid(row=3, column=0, pady=(8, 0))
        ttk.Button(buttons, text="Đóng", width=13, command=self.hide).grid(row=0, column=0)
        ttk.Button(buttons, text="Thoát app", width=13, command=self.c.quit).grid(row=0, column=1, padx=6)
        self.expand_button = ttk.Button(buttons, text="Mở rộng >>", width=13, command=self.toggle_expanded)
        self.expand_button.grid(row=0, column=2)
        ttk.Button(buttons, text="Hướng dẫn", width=13, command=self.show_help).grid(row=1, column=0, pady=(6, 0))
        ttk.Button(buttons, text="Giới thiệu", width=13, command=self.show_about).grid(
            row=1, column=1, padx=6, pady=(6, 0))
        ttk.Button(buttons, text="Mặc định", width=13, command=self.reset).grid(row=1, column=2, pady=(6, 0))

        self.status = ttk.Label(outer, style="Hint.TLabel", wraplength=330)
        self.status.grid(row=4, column=0, sticky="w", pady=(6, 0))

    def _build_extended(self, ext):
        ext.columnconfigure(0, weight=1)
        tabs = ttk.Notebook(ext)
        tabs.grid(row=0, column=0, sticky="ew")

        gemini = ttk.Frame(tabs, padding=10)
        tabs.add(gemini, text="AI Gemini")
        gemini.columnconfigure(1, weight=1)
        ttk.Label(gemini, text="Dịch theo lĩnh vực bằng AI Gemini (tùy chọn).").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        ttk.Label(gemini, text="API key:").grid(row=1, column=0, sticky="w", pady=3)
        self.key_var = tk.StringVar()
        self.key_entry = ttk.Entry(gemini, textvariable=self.key_var, show="•", width=30)
        self.key_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=3)
        for sequence in ("<FocusOut>", "<Return>"):
            self.key_entry.bind(sequence, lambda e: self._save_gemini())
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(gemini, text="Hiện", variable=self.show_key,
                        command=lambda: self.key_entry.configure(show="" if self.show_key.get() else "•")).grid(
            row=1, column=2, padx=(6, 0))
        ttk.Label(gemini, text="Model:").grid(row=2, column=0, sticky="w", pady=3)
        self.model_var = tk.StringVar()
        model_box = ttk.Combobox(gemini, textvariable=self.model_var, width=28,
                                 values=[model_label(m) for m in GEMINI_MODELS])
        model_box.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=3)
        for sequence in ("<<ComboboxSelected>>", "<FocusOut>", "<Return>"):
            model_box.bind(sequence, lambda e: self._save_gemini())
        actions = ttk.Frame(gemini)
        actions.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.check_button = ttk.Button(actions, text="Kiểm tra key", command=self.check_key)
        self.check_button.grid(row=0, column=0)
        link = ttk.Label(actions, text="Lấy API key miễn phí", style="Link.TLabel", cursor="hand2")
        link.grid(row=0, column=1, padx=(10, 0))
        link.bind("<Button-1>", lambda e: webbrowser.open(GEMINI_KEY_URL))
        ttk.Label(gemini, style="Hint.TLabel", wraplength=320, text=(
            "Để trống API key: dùng Google Dịch. Gemini hết lượt miễn phí hoặc lỗi thì app tự "
            "chuyển sang Google Dịch. Lưu ý: ở gói miễn phí, Google có thể dùng nội dung gửi lên "
            "để cải thiện sản phẩm.")).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        backup = ttk.Frame(tabs, padding=10)
        tabs.add(backup, text="Key dự phòng")
        backup.columnconfigure(1, weight=1)
        ttk.Label(backup, style="Hint.TLabel", wraplength=320, text=(
            "Khi key chính hết lượt hoặc lỗi, app lần lượt dùng các key dự phòng, hết cả mới "
            "chuyển sang Google Dịch.")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self.backup_vars, self.backup_entries = [], []
        for i in range(MAX_BACKUP_KEYS):
            ttk.Label(backup, text=f"Key dự phòng {i + 1}:").grid(row=i + 1, column=0, sticky="w", pady=3)
            var = tk.StringVar()
            entry = ttk.Entry(backup, textvariable=var, show="•", width=28)
            entry.grid(row=i + 1, column=1, sticky="ew", padx=(8, 0), pady=3)
            for sequence in ("<FocusOut>", "<Return>"):
                entry.bind(sequence, lambda e: self._save_gemini())
            self.backup_vars.append(var)
            self.backup_entries.append(entry)
        self.show_backup = tk.BooleanVar(value=False)
        ttk.Checkbutton(backup, text="Hiện", variable=self.show_backup, command=self._toggle_backup_visibility).grid(
            row=1, column=2, padx=(6, 0))
        ttk.Label(backup, style="Hint.TLabel", wraplength=320, text=(
            "Lưu ý: lượt miễn phí tính theo project, nên các key trong cùng một project dùng chung "
            "lượt. Nên dùng key của project có bật thanh toán làm dự phòng. Dùng nhiều project/tài "
            "khoản miễn phí để vượt giới hạn là trái điều khoản của Google và có thể bị khóa key.")).grid(
            row=MAX_BACKUP_KEYS + 1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        keys = ttk.Frame(tabs, padding=10)
        tabs.add(keys, text="Phím tắt")
        keys.columnconfigure(1, weight=1)
        for row, key in enumerate(("hotkey_vi", "hotkey_en", "hotkey_region")):
            ttk.Label(keys, text=HOTKEY_NAMES[key] + ":").grid(row=row, column=0, sticky="w", pady=3)
            entry = HotkeyEntry(keys, self, key)
            entry.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=3)
            self.hotkey_entries.append(entry)
        ttk.Label(keys, style="Hint.TLabel", wraplength=320,
                  text="Bấm vào ô rồi nhấn phím mới. Esc: giữ phím cũ, Backspace: bỏ phím.").grid(
            row=3, column=0, columnspan=2, sticky="w")

        privacy = ttk.Frame(tabs, padding=10)
        tabs.add(privacy, text="Bảo vệ dữ liệu")
        self._check(privacy, "skip_password_managers", "Bỏ qua nội dung từ trình quản lý mật khẩu").grid(
            row=0, column=0, sticky="w")
        self._check(privacy, "skip_sensitive_text", "Bỏ qua mật khẩu, mã token, URL, đường dẫn, email").grid(
            row=1, column=0, sticky="w")
        ttk.Label(privacy, text="Không tự dịch khi copy từ các app (mỗi dòng một tên):").grid(
            row=2, column=0, sticky="w", pady=(4, 2))
        self.apps_text = self._apps_box(privacy, "excluded_apps")
        self.apps_text.grid(row=3, column=0, sticky="ew")
        ttk.Label(privacy, text="Không sửa chính tả khi cắt từ các app:").grid(
            row=4, column=0, sticky="w", pady=(6, 2))
        self.proofread_apps_text = self._apps_box(privacy, "proofread_excluded_apps")
        self.proofread_apps_text.grid(row=5, column=0, sticky="ew")
        ttk.Label(privacy, style="Hint.TLabel", wraplength=320, text=(
            "Mặc định bỏ qua trình soạn code, cửa sổ lệnh và Excel để không làm hỏng code, số liệu.")).grid(
            row=6, column=0, sticky="w", pady=(2, 0))

        other = ttk.Frame(tabs, padding=10)
        tabs.add(other, text="Khác")
        other.columnconfigure(0, weight=1)
        ttk.Label(other, style="Hint.TLabel", wraplength=320, text=(
            "Khi Google Dịch bận, app dùng MyMemory. Nhập email để tăng hạn mức miễn phí "
            "của MyMemory (email được gửi kèm yêu cầu dịch tới MyMemory).")).grid(
            row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(other, text="Email cho MyMemory (tùy chọn):").grid(row=1, column=0, sticky="w")
        self.email_var = tk.StringVar()
        email = ttk.Entry(other, textvariable=self.email_var, width=40)
        email.grid(row=2, column=0, sticky="ew", pady=(2, 4))
        for sequence in ("<FocusOut>", "<Return>"):
            email.bind(sequence, lambda e: self._save_email())
        self._check(other, "notify_errors", "Thông báo khi không dịch được").grid(row=3, column=0, sticky="w")
        cursor_row = ttk.Frame(other)
        cursor_row.grid(row=4, column=0, sticky="w", pady=(4, 0))
        ttk.Label(cursor_row, text="Con trỏ khi đang dịch, sửa:").grid(row=0, column=0, sticky="w")
        self.cursor_var = tk.StringVar()
        cursor_box = ttk.Combobox(cursor_row, textvariable=self.cursor_var, state="readonly", width=22,
                                  values=[label for _, label in CURSOR_CHOICES])
        cursor_box.grid(row=0, column=1, padx=(8, 0))
        cursor_box.bind("<<ComboboxSelected>>",
                        lambda e: self.c.set_option("busy_cursor", CURSOR_BY_LABEL[self.cursor_var.get()]))
        ttk.Button(other, text="Mở thư mục log", command=self.c.open_log_folder).grid(
            row=5, column=0, sticky="w", pady=(6, 0))
        # Kiểm tra cập nhật: bấm vào dòng phiên bản trên cùng (app vẫn tự kiểm tra mỗi ngày).
        location = self.c.install_location()
        if location:
            ttk.Label(other, style="Hint.TLabel", wraplength=320,
                      text=f"Đã cài tại: {location}\nGỡ cài đặt trong Cài đặt Windows → Ứng dụng.").grid(
                row=9, column=0, sticky="w", pady=(6, 0))
        elif self.c.can_install():
            ttk.Button(other, text="Cài vào máy…", command=self._install_now).grid(
                row=9, column=0, sticky="w", pady=(6, 0))

    def _install_now(self):
        if messagebox.askyesno(f"Cài đặt – {APP_NAME}",
                               "App sẽ khởi động lại và hỏi thư mục cài đặt. Tiếp tục?", parent=self.root):
            self.c.restart_to_install()

    def _check(self, parent, key, text):
        var = tk.BooleanVar()
        self.bool_vars[key] = var
        return ttk.Checkbutton(parent, text=text, variable=var,
                               command=lambda: self.c.set_option(key, var.get()))

    def _apps_box(self, parent, key):
        """Ô nhập danh sách tên app (mỗi dòng một tên), tự lưu khi rời ô."""
        box = tk.Text(parent, height=3, width=40, font="TkDefaultFont", relief="flat",
                      highlightthickness=1, highlightbackground="#a0a0a0", highlightcolor="#0b57d0")
        box.bind("<FocusOut>", lambda e: self.c.set_excluded_apps(re.split(r"[\n,;]+", box.get("1.0", "end")), key))
        return box

    # ---------- hiển thị ----------
    def show(self):
        """Mở Bảng điều khiển ở giữa màn hình (đang mở sẵn thì chỉ đưa lên trên, không dời chỗ)."""
        was_hidden = self.root.state() == "withdrawn"
        self.refresh_all()
        center = was_hidden or not self.positioned
        if center:
            self.root.update_idletasks()
            self._center(self.root.winfo_reqwidth(), self.root.winfo_reqheight())
            self.positioned = True
        self.root.deiconify()
        if center:
            self._center_visible()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def _center(self, width, height):
        x = (self.root.winfo_screenwidth() - width) // 2
        y = (self.root.winfo_screenheight() - height) // 2
        self.root.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _center_visible(self):
        """Căn phần cửa sổ nhìn thấy vào giữa vùng làm việc (màn hình trừ thanh taskbar).

        Windows 10/11 có viền trong suốt quanh cửa sổ nên phải đo phần nhìn thấy qua DWM.
        """
        user32, dwmapi = ctypes.windll.user32, ctypes.windll.dwmapi
        work = wintypes.RECT()
        if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0):  # SPI_GETWORKAREA
            return
        for _ in range(2):
            self.root.update_idletasks()
            hwnd = int(self.root.wm_frame(), 16)
            rect = wintypes.RECT()
            if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
                if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return
            dx = (work.left + work.right) // 2 - (rect.left + rect.right) // 2
            dy = (work.top + work.bottom) // 2 - (rect.top + rect.bottom) // 2
            if abs(dx) <= 1 and abs(dy) <= 1:
                return
            match = re.search(r"([+-]-?\d+)([+-]-?\d+)$", self.root.geometry())
            if not match:
                return
            x, y = int(match.group(1)) + dx, int(match.group(2)) + dy
            self.root.geometry(f"+{max(work.left, x)}+{max(work.top, y)}")

    def hide(self):
        self._hide_toast()
        self._hide_badge_tip()
        for box, key in ((self.apps_text, "excluded_apps"), (self.proofread_apps_text, "proofread_excluded_apps")):
            self.c.set_excluded_apps(re.split(r"[\n,;]+", box.get("1.0", "end")), key)
        self._save_email()
        self._save_gemini()
        self.root.withdraw()

    def toggle_expanded(self):
        self.expanded = not self.expanded
        if self.expanded:
            self.ext.grid()
            self.expand_button.configure(text="Thu gọn <<")
        else:
            self.ext.grid_remove()
            self.expand_button.configure(text="Mở rộng >>")

    def set_status(self, text, error=False):
        self.status.configure(text=text, style="Error.TLabel" if error else "Hint.TLabel")

    def show_hotkey_errors(self, errors):
        if errors:
            self.set_status("Không dùng được phím: " + "; ".join(
                f"{HOTKEY_NAMES.get(key, key)} ({hotkeys.display(self.c.get(key))}) {why}"
                for key, why in errors.items()), error=True)

    def refresh_mode(self):
        self.mode_var.set(LABEL_BY_MODE[self.c.current_mode()])
        self.refresh_engine()

    def refresh_engine(self):
        self.engine_label.configure(text=self.c.engine_summary())

    def refresh_all(self):
        self.refresh_mode()
        self.trigger_var.set(LABEL_BY_TRIGGER[self.c.get("trigger")])
        self.domain_var.set(self.c.get("domain"))
        self.key_var.set(self.c.get("gemini_api_key"))
        backups = self.c.get("gemini_backup_keys")
        for i, var in enumerate(self.backup_vars):
            var.set(backups[i] if i < len(backups) else "")
        self.model_var.set(model_label(self.c.get("gemini_model")))
        self.email_var.set(self.c.get("mymemory_email"))
        self.cursor_var.set(LABEL_BY_CURSOR.get(self.c.get("busy_cursor"), CURSOR_CHOICES[0][1]))
        self.hold_key_var.set(HOLD_KEYS.get(self.c.get("region_hold_key"), HOLD_KEYS["shift"]))
        self.hold_ms_var.set(hold_ms_label(self.c.get("region_hold_ms")))
        self.refresh_checks()
        for entry in self.hotkey_entries:
            if self.root.focus_get() is not entry:
                entry.refresh()
        for box, key in ((self.apps_text, "excluded_apps"), (self.proofread_apps_text, "proofread_excluded_apps")):
            box.delete("1.0", "end")
            box.insert("1.0", "\n".join(self.c.get(key)))
        self.refresh_engine()

    def refresh_checks(self):
        """Cập nhật các ô tích (ví dụ khi bật / tắt sửa chính tả từ menu khay)."""
        for key, var in self.bool_vars.items():
            var.set(self.c.get(key))

    def _sync_region_state(self):
        """Tắt "Giữ phím để chọn vùng" thì hai ô Phím giữ / Thời gian giữ mờ đi."""
        state = "readonly" if self.bool_vars["region_on_hold"].get() else "disabled"
        for box in (self.hold_key_box, self.hold_ms_box):
            box.configure(state=state)

    # ---------- lưu cài đặt ----------
    def _save_email(self):
        email = self.email_var.get().strip()
        if email and not _EMAIL.fullmatch(email):
            self.set_status("Email chưa đúng định dạng, chưa lưu.", error=True)
            return
        if email != self.c.get("mymemory_email"):
            self.c.set_option("mymemory_email", email)
            self.set_status("Đã lưu email MyMemory." if email else "Đã xóa email MyMemory.")

    def _toggle_backup_visibility(self):
        for entry in self.backup_entries:
            entry.configure(show="" if self.show_backup.get() else "•")

    def _backup_keys(self):
        main = self.key_var.get().strip()
        result = []
        for var in self.backup_vars:
            key = var.get().strip()
            if key and key != main and key not in result:
                result.append(key)
        return result

    def _save_gemini(self):
        changes = {
            "gemini_api_key": self.key_var.get().strip(),
            "gemini_backup_keys": self._backup_keys(),
            "gemini_model": model_from_label(self.model_var.get()),
            "domain": self.domain_var.get().strip(),
        }
        changes = {key: value for key, value in changes.items() if value != self.c.get(key)}
        if changes:
            self.c.set_gemini(**changes)
            self.refresh_engine()

    def check_key(self):
        """Kiểm tra key chính và mọi key dự phòng (mỗi key tốn 1 lượt)."""
        self._save_gemini()
        model = self.c.get("gemini_model")
        keys = [("Key chính", self.c.get("gemini_api_key"))] + [
            (f"Dự phòng {i}", key) for i, key in enumerate(self.c.get("gemini_backup_keys"), 1)]
        keys = [(label, key) for label, key in keys if key]
        if not keys:
            self.set_status("Chưa nhập API key Gemini.", error=True)
            return
        self.check_button.configure(state="disabled")
        self.set_status("Đang kiểm tra key Gemini…")

        def work():
            results = [(label, *self.c.check_gemini(key, model)) for label, key in keys]
            ok = all(passed for _, passed, _ in results)
            message = "\n".join(f"{label}: {text}" for label, _, text in results)

            def done():
                self.check_button.configure(state="normal")
                self.set_status(message, error=not ok)
                self.refresh_engine()

            self.c.ui_call(done)

        threading.Thread(target=work, daemon=True).start()

    def reset(self):
        if not messagebox.askyesno(APP_NAME, "Khôi phục toàn bộ cài đặt về mặc định?\n"
                                   "API key Gemini và email đã nhập sẽ bị xóa.", parent=self.root):
            return
        errors = self.c.reset_defaults()
        self.refresh_all()
        self.set_status("Đã khôi phục cài đặt mặc định.")
        self.show_hotkey_errors(errors)

    # ---------- cập nhật qua GitHub (dòng phiên bản trên cùng) ----------
    def _set_badge(self, text, bg, fg, cursor=""):
        self.version_badge.configure(text=text, bg=bg, fg=fg, cursor=cursor)

    def _badge_normal(self):
        """Bình thường: "Phiên bản x.y.z", bấm vào để kiểm tra bản mới."""
        self._set_badge(f"Phiên bản {APP_VERSION}", self._header_bg, "#666666", "hand2")

    def _badge_hover(self, inside):
        if not self.update_release and not self.updating and not self.checking:
            self.version_badge.configure(fg="#0b57d0" if inside else "#666666")
        if inside:
            self._tip_job = self.root.after(400, self._show_badge_tip)
        else:
            self._hide_badge_tip()

    def _badge_tip_text(self):
        if self.updating or self.checking:
            return None
        if self.update_release:
            return "Bấm để xem có gì mới và nâng cấp"
        return "Bấm để kiểm tra bản mới"

    def _show_badge_tip(self):
        """Gợi ý nhỏ dưới dòng phiên bản để người dùng biết dòng này bấm được."""
        self._tip_job = None
        text = self._badge_tip_text()
        if not text or self._tip is not None:
            return
        tip = tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=text, bg="#fffbe6", fg="#333333", bd=1, relief="solid",
                 font=("Segoe UI", 9), padx=6, pady=2).pack()
        tip.update_idletasks()
        badge = self.version_badge
        x = badge.winfo_rootx() + badge.winfo_width() - tip.winfo_reqwidth()
        y = badge.winfo_rooty() + badge.winfo_height() + 4
        tip.geometry(f"+{max(0, x)}+{y}")
        self._tip = tip

    def _hide_badge_tip(self):
        if self._tip_job:
            self.root.after_cancel(self._tip_job)
            self._tip_job = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def _stop_blink(self):
        if self._blink_job:
            self.root.after_cancel(self._blink_job)
            self._blink_job = None

    def _blink(self):
        """Nhấp nháy đỏ để người dùng chú ý có bản mới."""
        release = self.update_release
        if not release or self.updating:
            self._blink_job = None
            return
        self._blink_on = not self._blink_on
        bg, fg = ("#d93025", "white") if self._blink_on else ("#fce8e6", "#b3261e")
        self._set_badge(f"↑ Cập nhật lên {release.version}", bg, fg, "hand2")
        self._blink_job = self.root.after(550, self._blink)

    def show_update_available(self, release):
        self.update_release = release
        self._stop_blink()
        if self.updating:
            return
        if not release:
            self._badge_normal()
            return
        self._blink()

    def _badge_clicked(self):
        """Có bản mới: hỏi nâng cấp. Chưa có: kiểm tra trên GitHub."""
        self._hide_badge_tip()
        if self.updating or self.checking:
            return
        if self.update_release:
            self.confirm_update(self.update_release)
        else:
            self.check_updates_now()

    def confirm_update(self, release):
        """Cửa sổ "Có bản mới": hiện tính năng mới, người dùng bấm "Cập nhật ngay" để cập nhật."""
        if self.updating:
            return
        if self._update_dialog is not None and self._update_dialog.winfo_exists():
            self._update_dialog.lift()
            return
        self.show()
        self_update = self.c.can_self_update()
        win = tk.Toplevel(self.root)
        self._update_dialog = win
        win.title(f"Cập nhật – {APP_NAME}")
        win.resizable(False, False)
        win.transient(self.root)
        frame = ttk.Frame(win, padding=14)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text=f"Có bản mới {release.version}", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text=f"Bạn đang dùng bản {APP_VERSION}. Bạn có muốn nâng cấp không?",
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(frame, text="Có gì mới:").grid(row=2, column=0, columnspan=2, sticky="w")
        notes = tk.Text(frame, width=54, height=9, wrap="word", font="TkDefaultFont", relief="flat",
                        padx=8, pady=6, highlightthickness=1, highlightbackground="#c0c0c0")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=notes.yview)
        notes.configure(yscrollcommand=scroll.set)
        notes.insert("1.0", updater.whats_new(release.notes))
        notes.configure(state="disabled")
        notes.grid(row=3, column=0, sticky="nsew", pady=(2, 8))
        scroll.grid(row=3, column=1, sticky="ns", pady=(2, 8))
        ttk.Label(frame, style="Hint.TLabel", wraplength=400, text=(
            "App sẽ tải bản mới từ GitHub, kiểm tra an toàn rồi tự khởi động lại. Cài đặt của bạn được giữ nguyên."
            if self_update else
            "Bạn đang chạy từ mã nguồn nên app không tự cập nhật được; nút bên dưới mở trang tải bản mới.")).grid(
            row=4, column=0, columnspan=2, sticky="w")

        def close():
            win.grab_release()
            win.destroy()
            self._update_dialog = None

        def update_now():
            close()
            self.start_update(release)

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ok = ttk.Button(buttons, text="Đồng ý" if self_update else "Mở trang tải", command=update_now)
        ok.grid(row=0, column=0)
        ttk.Button(buttons, text="Để sau", command=close).grid(row=0, column=1, padx=(6, 0))
        win.bind("<Return>", lambda e: update_now())
        win.bind("<Escape>", lambda e: close())
        win.protocol("WM_DELETE_WINDOW", close)
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + 30
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        win.grab_set()
        ok.focus_set()

    def check_updates_now(self):
        """Bấm "Phiên bản x.y.z": kiểm tra GitHub. Mới nhất thì báo; có bản mới thì nhấp nháy chờ nâng cấp."""
        self.checking = True
        self._set_badge("Đang kiểm tra…", "#e8f0fe", "#0b57d0", "watch")

        def work():
            try:
                release, error = self.c.check_update(), None
            except Exception as e:  # UpdateError và lỗi mạng
                release, error = None, str(e)

            def done():
                self.checking = False
                if error:
                    self._badge_normal()
                    self.toast(f"Không kiểm tra được bản cập nhật. {error}", kind="error", duration=5000)
                elif release:
                    self.show_update_available(release)  # dòng phiên bản nhấp nháy
                    self.toast(f"Có bản mới v{release.version}. Bấm vào dòng phiên bản để nâng cấp.", kind="info",
                               duration=4500)
                else:
                    self._badge_normal()
                    self.toast(f"Bạn đang sử dụng phiên bản mới nhất (v{APP_VERSION}).")

            self.c.ui_call(done)

        threading.Thread(target=work, daemon=True).start()

    # ---------- thông báo nhỏ ở góc, tự ẩn sau vài giây ----------
    TOAST_STYLES = {  # nền, màu viền/biểu tượng, biểu tượng
        "success": ("#1f2d27", "#3fb950", "✓"),
        "info": ("#1f2733", "#58a6ff", "↑"),
        "error": ("#2d1f1f", "#f85149", "!"),
    }

    def toast(self, text, kind="success", duration=3500):
        """Thông báo nhỏ ở góc trên bên phải Bảng điều khiển; tự mờ dần rồi ẩn, bấm vào để ẩn ngay."""
        self._hide_toast()
        bg, accent, symbol = self.TOAST_STYLES[kind]
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        box = tk.Frame(win, bg=bg, highlightthickness=1, highlightbackground=accent, highlightcolor=accent)
        box.pack()
        tk.Label(box, text=symbol, bg=bg, fg=accent, font=("Segoe UI", 11, "bold")).pack(
            side="left", padx=(10, 6), pady=7)
        tk.Label(box, text=text, bg=bg, fg="#e6edf3", font=("Segoe UI", 9), justify="left",
                 wraplength=max(200, self.root.winfo_width() - 70)).pack(side="left", padx=(0, 12), pady=7)
        for widget in (win, box, *box.winfo_children()):
            widget.bind("<Button-1>", lambda e: self._hide_toast())
        win.update_idletasks()
        x = self.root.winfo_rootx() + self.root.winfo_width() - win.winfo_reqwidth() - 10
        y = self.version_badge.winfo_rooty() + self.version_badge.winfo_height() + 6
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        self._toast = win
        self._toast_job = self.root.after(duration, lambda: self._fade_toast(win, 1.0))

    def _fade_toast(self, win, alpha):
        if win is not self._toast or not win.winfo_exists():
            return
        alpha -= 0.12
        if alpha <= 0:
            self._hide_toast()
            return
        win.attributes("-alpha", alpha)
        self._toast_job = self.root.after(30, lambda: self._fade_toast(win, alpha))

    def _hide_toast(self):
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
            self._toast_job = None
        if self._toast is not None:
            if self._toast.winfo_exists():
                self._toast.destroy()
            self._toast = None

    def start_update(self, release):
        """Bấm một lần là cập nhật: tải, kiểm tra, cài rồi tự khởi động lại (tiến độ hiện ngay tại dòng phiên bản)."""
        if self.updating:
            return
        if not self.c.can_self_update():
            webbrowser.open(release.page_url)
            self.set_status("Bạn đang chạy từ mã nguồn nên app không tự cập nhật được: đã mở trang tải bản mới.")
            return
        self.updating = True
        self._stop_blink()
        busy = ("#e8f0fe", "#0b57d0", "watch")
        self._set_badge("Đang tải… 0%", *busy)
        self.set_status(f"Đang tải bản {release.version} từ GitHub…")

        def progress(fraction):
            self.c.ui_call(lambda: self._set_badge(f"Đang tải… {int(fraction * 100)}%", *busy))

        def installing():
            self._set_badge("Đang cài đặt…", *busy)
            self.set_status("Đang cài bản cập nhật, app sẽ tự khởi động lại…")

        def work():
            try:
                self.c.install_update(release, progress)
                self.c.ui_call(installing)
            except Exception as e:
                message = str(e)

                def failed():
                    self.updating = False
                    self.set_status(message, error=True)
                    self.show_update_available(release)  # nhấp nháy lại để thử lần nữa
                    if messagebox.askyesno(f"Cập nhật – {APP_NAME}",
                                           f"{message}\n\nMở trang GitHub để tải bản mới thủ công?",
                                           parent=self.root):
                        webbrowser.open(release.page_url)

                self.c.ui_call(failed)

        threading.Thread(target=work, daemon=True).start()

    # ---------- hướng dẫn / thông tin ----------
    def show_help(self):
        vi, en, toggle = (hotkeys.display(self.c.get(k)) for k in ("hotkey_vi", "hotkey_en", "hotkey_toggle"))
        region_key = self.c.get("hotkey_region")
        region_line = (f"• {hotkeys.display(region_key)}: chọn vùng màn hình để dịch (như giữ phím).\n"
                       if region_key else "")
        hold_key = HOLD_KEYS.get(self.c.get("region_hold_key"), HOLD_KEYS["shift"])
        hold_time = hold_ms_label(self.c.get("region_hold_ms"))
        messagebox.showinfo(f"Hướng dẫn – {APP_NAME}", (
            "Cách dùng:\n"
            "1. Bôi đen văn bản và nhấn nhanh Ctrl+C hai lần. Copy một lần là copy bình thường, "
            "không dịch. (Nếu chọn \"Dịch khi: Mỗi lần copy\" thì chỉ cần nhấn một lần.)\n"
            "2. Chờ icon ở khay hệ thống tô xanh đủ 100%: clipboard lúc này là bản dịch.\n"
            "3. Nhấn Ctrl+V để dán bản dịch.\n\n"
            "Dịch vùng màn hình (file PDF không cho copy, PDF scan, ảnh, phần mềm...):\n"
            f"1. Giữ riêng phím {hold_key} khoảng {hold_time}: màn hình mờ đi, con trỏ thành dấu +.\n"
            "2. Kéo chuột bao quanh đoạn chữ cần dịch rồi thả (Esc hoặc chuột phải: hủy).\n"
            "3. Bản dịch hiện trong khung nhỏ ngay dưới vùng đó. Bấm \"Sao chép\" để lấy bản dịch; "
            "Esc hoặc bấm ra ngoài khung để đóng. Muốn dời khung: giữ chuột ở dòng trên cùng của khung "
            "rồi kéo đi chỗ khác.\n"
            "Gemini đọc và dịch chữ trong ảnh (1 lượt mỗi lần). Gemini hết lượt: Windows đọc chữ rồi "
            "Google Dịch dịch. Bật / tắt, đổi phím giữ và thời gian giữ ở phần \"Dịch hình ảnh\" "
            "trong Bảng điều khiển.\n\n"
            "Sửa chính tả, câu văn (cần API key Gemini):\n"
            "1. Bôi đen đoạn văn và nhấn Ctrl+X.\n"
            "2. Chờ icon ở khay hệ thống hiện chữ S màu cam và tô xanh đủ 100%.\n"
            "3. Nhấn Ctrl+V: dán bản đã sửa đúng chính tả, đúng dấu, câu văn tự nhiên, giữ nguyên ý "
            "và giọng văn của bạn.\n"
            "Nếu chưa sửa xong hoặc không sửa được, Ctrl+V dán lại đúng đoạn đã cắt, không mất chữ. "
            "Bật / tắt ở phần Điều khiển hoặc menu chuột phải của icon.\n\n"
            "Chế độ dịch:\n"
            "• Tự động hai chiều (A): tiếng Việt → tiếng Anh, ngôn ngữ khác → tiếng Việt.\n"
            "• Sang tiếng Việt (V) / Sang tiếng Anh (E): luôn dịch theo một chiều.\n"
            "• Tắt (–): copy bình thường, không dịch.\n\n"
            "Phím tắt:\n"
            f"• {vi}: chuyển sang chế độ Sang tiếng Việt và dịch luôn clipboard.\n"
            f"• {en}: chuyển sang chế độ Sang tiếng Anh và dịch luôn clipboard.\n"
            f"• {toggle}: tắt dịch / bật lại chế độ trước đó.\n"
            + region_line +
            "• Bấm trái vào icon: tắt / bật dịch. Chuột phải: menu.\n\n"
            "Dịch theo lĩnh vực:\n"
            "Nhập API key Gemini ở phần Mở rộng rồi chọn Lĩnh vực. Không có key thì app "
            "dùng Google Dịch (không theo lĩnh vực).\n\n"
            "Tiết kiệm lượt Gemini: chỉ lần nhấn Ctrl+C hai lần mới gọi Gemini; copy lại đoạn đã dịch "
            "trong lúc app đang chạy thì không tốn lượt. Số lượt đã dùng hiện ở phần Điều khiển.\n\n"
            "Lưu ý: nếu chọn \"Dịch khi: Mỗi lần copy\", mọi văn bản bạn copy đều bị thay bằng bản "
            "dịch; muốn copy–dán bình thường, hãy tắt dịch trước."), parent=self.root)

    def show_about(self):
        """Cửa sổ Giới thiệu: về app, tác giả và liên hệ qua Zalo (bấm mở hoặc quét mã QR)."""
        if self._about_dialog is not None and self._about_dialog.winfo_exists():
            self._about_dialog.lift()
            return
        self.show()
        win = tk.Toplevel(self.root)
        self._about_dialog = win
        win.title(f"Giới thiệu – {APP_NAME}")
        win.resizable(False, False)
        win.transient(self.root)
        frame = ttk.Frame(win, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)

        def heading(row, text):
            ttk.Label(frame, text=text, font=("Segoe UI", 10, "bold")).grid(
                row=row, column=0, sticky="w", pady=(12, 2))

        ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=f"Phiên bản {APP_VERSION}", style="Hint.TLabel").grid(row=1, column=0, sticky="w")

        heading(2, "Giới thiệu")
        ttk.Label(frame, wraplength=430, justify="left", text=ABOUT_TEXT).grid(row=3, column=0, sticky="w")

        heading(4, "Tác giả")
        author = ttk.Frame(frame)
        author.grid(row=5, column=0, sticky="ew")
        author.columnconfigure(0, weight=1)
        info = ttk.Frame(author)
        info.grid(row=0, column=0, sticky="nw")
        ttk.Label(info, text=AUTHOR_NAME, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(info, text=AUTHOR_ROLE, style="Hint.TLabel", wraplength=230).grid(row=1, column=0, sticky="w")
        ttk.Label(info, wraplength=230, justify="left", text=(
            "Góp ý, báo lỗi hoặc cần hỗ trợ cài đặt, vui lòng nhắn tin qua Zalo: bấm nút bên dưới "
            "hoặc quét mã QR bằng ứng dụng Zalo trên điện thoại.")).grid(row=2, column=0, sticky="w", pady=(8, 8))
        ttk.Button(info, text="Nhắn tin qua Zalo", width=20,
                   command=lambda: webbrowser.open(AUTHOR_ZALO_URL)).grid(row=3, column=0, sticky="w")

        def copy_link():
            win.clipboard_clear()
            win.clipboard_append(AUTHOR_ZALO_URL)
            self.toast("Đã sao chép liên kết Zalo.")

        ttk.Button(info, text="Sao chép liên kết", width=20, command=copy_link).grid(
            row=4, column=0, sticky="w", pady=(6, 0))
        qr = _load_image(os.path.join(ASSETS_DIR, "zalo_qr.png"), 190, win)
        if qr is not None:
            win.qr_image = qr  # giữ tham chiếu, nếu không Tk sẽ xóa ảnh
            box = ttk.Frame(author)
            box.grid(row=0, column=1, sticky="ne", padx=(16, 0))
            tk.Label(box, image=qr, bd=1, relief="solid", bg="white").grid(row=0, column=0)
            ttk.Label(box, text="Quét bằng Zalo để nhắn tin", style="Hint.TLabel").grid(
                row=1, column=0, pady=(4, 0))

        heading(6, "Quyền riêng tư")
        ttk.Label(frame, wraplength=430, justify="left", text=(
            "Văn bản chỉ được gửi đi (tới Gemini, Google Dịch hoặc MyMemory) khi bạn dịch hay sửa chính tả. "
            "App không lưu nội dung bạn copy hoặc cắt. Nội dung từ trình quản lý mật khẩu và chuỗi giống "
            "mật khẩu được bỏ qua theo cài đặt Bảo vệ dữ liệu.")).grid(row=7, column=0, sticky="w")
        ttk.Label(frame, style="Hint.TLabel", wraplength=430, justify="left", text=(
            f"Cài đặt: {self.c.config_path}\nNhật ký lỗi: {self.c.log_path()}")).grid(
            row=8, column=0, sticky="w", pady=(8, 0))

        bottom = ttk.Frame(frame)
        bottom.grid(row=9, column=0, sticky="ew", pady=(14, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, text=f"© 2026 {AUTHOR_NAME}. Bảo lưu mọi quyền.", style="Hint.TLabel").grid(
            row=0, column=0, sticky="w")

        def close():
            win.destroy()
            self._about_dialog = None

        close_button = ttk.Button(bottom, text="Đóng", width=12, command=close)
        close_button.grid(row=0, column=1, sticky="e")
        win.bind("<Escape>", lambda e: close())
        win.protocol("WM_DELETE_WINDOW", close)
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + 20
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        close_button.focus_set()


ABOUT_TEXT = (
    f"{APP_NAME} là công cụ nhỏ chạy nền trên Windows, giúp đọc và soạn văn bản Việt – Anh nhanh hơn "
    "ngay trong lúc làm việc. Nhấn nhanh Ctrl+C hai lần để dịch, hoặc nhấn Ctrl+X để sửa chính tả và "
    "câu văn, rồi Ctrl+V để dán kết quả, không cần mở trình duyệt hay chuyển cửa sổ. Bản dịch dùng đúng "
    "thuật ngữ theo lĩnh vực bạn chọn, mặc định là kỹ thuật máy in công nghiệp."
)
AUTHOR_ROLE = "Kỹ thuật viên sửa chữa máy in công nghiệp"


def _load_image(path, size, master):
    """Ảnh PNG thu nhỏ vừa khung size x size cho Tk; None nếu không đọc được."""
    try:
        from PIL import Image, ImageTk

        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((size, size), Image.LANCZOS)
            return ImageTk.PhotoImage(img, master=master)
    except (OSError, ImportError, ValueError):
        return None


def ask_install_options(default_folder, normalize, needs_admin, window_icon=None):
    """Cửa sổ cài đặt lần đầu. Trả về ("install", thư mục, tạo_desktop), ("portable",) hoặc None (hủy)."""
    root = tk.Tk()
    root.title(f"Cài đặt {APP_NAME}")
    root.resizable(False, False)
    if window_icon is not None:
        icon = window_icon(root)
        root.iconphoto(True, icon)
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    style.configure("Hint.TLabel", foreground="#666666")
    style.configure("Warn.TLabel", foreground="#9a6700")
    result = {"value": None}

    frame = ttk.Frame(root, padding=16)
    frame.grid(sticky="nsew")
    frame.columnconfigure(0, weight=1)
    ttk.Label(frame, text=f"Cài đặt {APP_NAME} {APP_VERSION}", font=("Segoe UI", 12, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(frame, style="Hint.TLabel", wraplength=440, text=(
        "Ứng dụng sẽ được cài vào máy, có lối tắt trong Start Menu và gỡ được trong "
        "Cài đặt Windows → Ứng dụng.")).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 12))
    ttk.Label(frame, text="Thư mục cài đặt:").grid(row=2, column=0, columnspan=2, sticky="w")
    folder_var = tk.StringVar(value=default_folder)
    ttk.Entry(frame, textvariable=folder_var, width=52).grid(row=3, column=0, sticky="ew", pady=(2, 0))

    def browse():
        from tkinter import filedialog
        current = folder_var.get()
        start = current if os.path.isdir(current) else os.path.dirname(current)
        chosen = filedialog.askdirectory(parent=root, initialdir=start, title="Chọn ổ đĩa / thư mục cài đặt")
        if chosen:
            folder_var.set(normalize(chosen))

    ttk.Button(frame, text="Thay đổi…", command=browse).grid(row=3, column=1, padx=(6, 0), pady=(2, 0))
    admin_hint = ttk.Label(frame, style="Warn.TLabel", wraplength=440)
    admin_hint.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def update_hint(*_):
        folder = folder_var.get().strip()
        admin_hint.configure(text=(
            "Thư mục này cần quyền quản trị: Windows sẽ hỏi xác nhận khi cài đặt và mỗi lần cập nhật."
            if folder and needs_admin(folder) else ""))

    folder_var.trace_add("write", update_hint)
    update_hint()
    desktop_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(frame, text="Tạo lối tắt ngoài Desktop", variable=desktop_var).grid(
        row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def finish(value):
        result["value"] = value
        root.destroy()

    def install():
        folder = folder_var.get().strip()
        if not folder:
            return
        finish(("install", normalize(folder), desktop_var.get()))

    buttons = ttk.Frame(frame)
    buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(16, 0))
    ok = ttk.Button(buttons, text="Cài đặt", command=install)
    ok.grid(row=0, column=0)
    ttk.Button(buttons, text="Chạy không cài đặt", command=lambda: finish(("portable",))).grid(
        row=0, column=1, padx=(6, 0))
    ttk.Button(buttons, text="Hủy", command=lambda: finish(None)).grid(row=0, column=2, padx=(6, 0))
    root.bind("<Return>", lambda e: install())
    root.bind("<Escape>", lambda e: finish(None))
    root.protocol("WM_DELETE_WINDOW", lambda: finish(None))
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 2
    root.geometry(f"+{max(0, x)}+{max(0, y)}")
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    ok.focus_set()
    root.mainloop()
    return result["value"]
