"""Cài đặt người dùng, lưu tại %APPDATA%\\TranslateVietnam\\config.json.

API key Gemini được mã hóa bằng Windows DPAPI: chỉ tài khoản Windows đã lưu
mới giải mã được, nên lỡ gửi file config cho người khác cũng không lộ key.
"""
import base64
import ctypes
import json
import os
import threading
from ctypes import wintypes

APP_NAME = "Translate Vietnam"
APP_VERSION = "1.0.5"  # tăng số này trước mỗi lần chạy release.bat
UPDATE_REPO = "tranvankha1989/Translate_EV"  # repo GitHub chứa các bản phát hành

MODES = ("off", "auto", "vi", "en")
DOMAINS = [
    "Kỹ thuật",
    "Công nghệ thông tin",
    "Điện – Điện tử",
    "Cơ khí – Chế tạo",
    "Xây dựng",
    "Y tế – Dược",
    "Pháp luật",
    "Kinh tế – Tài chính",
    "Marketing – Bán hàng",
    "Giáo dục",
    "Khoa học",
    "Đời sống hằng ngày",
]
GEMINI_MODELS = ["auto", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash"]
GEMINI_MODEL_LABELS = {"auto": "Tự động (Flash-Lite mới nhất)"}


def model_label(model: str) -> str:
    return GEMINI_MODEL_LABELS.get(model, model)


def model_from_label(text: str) -> str:
    text = text.strip()
    return next((model for model, label in GEMINI_MODEL_LABELS.items() if label == text), text)
SECRET_KEYS = ("gemini_api_key",)
SECRET_LIST_KEYS = ("gemini_backup_keys",)
MAX_BACKUP_KEYS = 3
_SECRET_PREFIX = "dpapi:"
DEFAULTS = {
    "mode": "auto",
    "last_on_mode": "auto",  # chế độ được bật lại khi bấm F10 hoặc bấm trái vào icon
    "trigger": "double",  # "double": nhấn Ctrl+C hai lần mới dịch; "single": mỗi lần copy đều dịch
    "hotkey_vi": "f8",
    "hotkey_en": "f9",
    "hotkey_toggle": "f10",
    "autostart": True,
    "show_panel_on_start": False,
    "skip_password_managers": True,
    "skip_sensitive_text": True,
    "excluded_apps": ["keepass.exe", "keepassxc.exe", "bitwarden.exe", "1password.exe"],
    "mymemory_email": "",
    "gemini_api_key": "",  # để trống: dùng Google Dịch
    "gemini_backup_keys": [],  # tối đa 3 key dự phòng (nên là project có bật thanh toán)
    "gemini_model": GEMINI_MODELS[0],
    "domain": DOMAINS[0],
    "notify_errors": True,
    "auto_update": True,  # tự kiểm tra bản mới trên GitHub mỗi ngày
    "run_without_install": False,  # người dùng đã chọn "Chạy không cài đặt": không hỏi cài nữa
    "gemini_usage_day": "",  # ngày tính hạn mức (giờ Thái Bình Dương) của bộ đếm bên dưới
    "gemini_usage_count": 0,  # chỉ lưu số lượt, không lưu nội dung
}
TRIGGERS = ("double", "single")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    function.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(data, len(data))
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = _Blob()
    if not function(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())  # 0x1 = CRYPTPROTECT_UI_FORBIDDEN
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def protect_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _SECRET_PREFIX + base64.b64encode(_dpapi(value.encode("utf-8"), True)).decode("ascii")
    except OSError:
        return value  # không mã hóa được thì vẫn lưu để app chạy được


def unprotect_secret(value: str) -> str:
    if not value.startswith(_SECRET_PREFIX):
        return value  # người dùng tự sửa file và dán key trực tiếp
    try:
        return _dpapi(base64.b64decode(value[len(_SECRET_PREFIX):]), False).decode("utf-8")
    except (OSError, ValueError):
        return ""  # file config chép từ máy/tài khoản khác: phải nhập lại key


def config_dir():
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "TranslateVietnam")


def _copy(value):
    return list(value) if isinstance(value, list) else value


class Settings:
    """Cài đặt an toàn khi dùng từ nhiều luồng; mỗi lần đổi đều ghi ngay xuống file."""

    def __init__(self, path=None):
        self.path = path or os.path.join(config_dir(), "config.json")
        self._lock = threading.RLock()
        self._data = {key: _copy(value) for key, value in DEFAULTS.items()}
        self.first_run = not os.path.exists(self.path)
        if not self.first_run:
            self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, ValueError):
            return  # file hỏng: dùng mặc định
        if not isinstance(stored, dict):
            return
        for key, default in DEFAULTS.items():
            value = stored.get(key)
            if isinstance(default, bool):
                if isinstance(value, bool):
                    self._data[key] = value
            elif isinstance(default, int):
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    self._data[key] = value
            elif isinstance(default, list):
                if isinstance(value, list):
                    items = [str(v).strip() for v in value if str(v).strip()]
                    if key in SECRET_LIST_KEYS:
                        items = [k for k in (unprotect_secret(v).strip() for v in items) if k]
                    self._data[key] = items
            elif isinstance(value, str):
                value = unprotect_secret(value) if key in SECRET_KEYS else value
                self._data[key] = value.strip()
        for key in ("gemini_model", "domain"):
            if not self._data[key]:
                self._data[key] = DEFAULTS[key]
        if self._data["trigger"] not in TRIGGERS:
            self._data["trigger"] = DEFAULTS["trigger"]
        if self._data["mode"] not in MODES:
            self._data["mode"] = DEFAULTS["mode"]
        if self._data["last_on_mode"] not in MODES or self._data["last_on_mode"] == "off":
            self._data["last_on_mode"] = DEFAULTS["last_on_mode"]

    def __getitem__(self, key):
        with self._lock:
            return _copy(self._data[key])

    def snapshot(self):
        with self._lock:
            return {key: _copy(value) for key, value in self._data.items()}

    def update(self, **changes):
        unknown = set(changes) - set(DEFAULTS)
        if unknown:
            raise KeyError(f"Cài đặt không tồn tại: {', '.join(sorted(unknown))}")
        with self._lock:
            for key, value in changes.items():
                self._data[key] = _copy(value)
            return self.save()

    def reset(self):
        with self._lock:
            self._data = {key: _copy(value) for key, value in DEFAULTS.items()}
            return self.save()

    def save(self):
        """Ghi file qua file tạm để không làm hỏng cài đặt nếu máy tắt giữa chừng."""
        with self._lock:
            stored = dict(self._data)
            for key in SECRET_KEYS:
                stored[key] = protect_secret(stored[key])
            for key in SECRET_LIST_KEYS:
                stored[key] = [protect_secret(v) for v in stored[key]]
            data = json.dumps(stored, ensure_ascii=False, indent=2)
            temp = self.path + ".tmp"
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(temp, "w", encoding="utf-8") as f:
                    f.write(data)
                os.replace(temp, self.path)
                return True
            except OSError:
                return False
