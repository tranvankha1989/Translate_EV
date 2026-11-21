"""Cài app vào Windows: người dùng chọn thư mục (mặc định C:\\Program Files\\Translate Vietnam).

- Chép exe vào thư mục đã chọn. Thư mục cần quyền quản trị (Program Files) thì chỉ bước chép
  chạy với quyền đó (Windows hỏi xác nhận); app vẫn chạy với quyền người dùng thường.
- Tạo lối tắt "Translate Vietnam" trong Start Menu và (tùy chọn) ngoài Desktop.
- Ghi mục gỡ cài đặt để app hiện trong Settings → Apps; vị trí cài được lưu ở đó (InstallLocation).
"""
import ctypes
import os
import sys
import uuid
import winreg
from ctypes import wintypes

import updater
from config import APP_NAME, APP_VERSION, UPDATE_REPO

EXE_NAME = "TranslateVietnam.exe"
FOLDER_NAME = "Translate Vietnam"
SHORTCUT_NAME = f"{APP_NAME}.lnk"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\TranslateVietnam"
OUR_FILES = (EXE_NAME, "app.log", "app.log.tmp")  # gỡ cài đặt chỉ xóa đúng các file này
CSIDL_DESKTOPDIRECTORY = 0x0010


class InstallCancelled(Exception):
    """Người dùng bấm "No" ở hộp hỏi quyền quản trị của Windows."""


# ------------------------------------------------------------------ đường dẫn

def default_install_dir():
    # App 64-bit: thư mục chuẩn là "Program Files" (không phải "Program Files (x86)").
    base = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"
    return os.path.join(base, FOLDER_NAME)


def normalize_install_dir(folder):
    """Chọn ổ D:\\ hay thư mục chung D:\\Apps thì cài vào thư mục con riêng "Translate Vietnam"."""
    folder = os.path.abspath(os.path.expandvars(folder.strip().strip('"')))
    name = os.path.basename(folder.rstrip("\\/")).lower()
    if name not in ("translate vietnam", "translatevietnam"):
        folder = os.path.join(folder, FOLDER_NAME)
    return folder


def start_menu_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    return os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs")


def desktop_dir():
    buffer = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None, 0, buffer) == 0:
        return buffer.value  # đúng cả khi Desktop được chuyển sang OneDrive
    return os.path.join(os.path.expanduser("~"), "Desktop")


def start_menu_shortcut():
    return os.path.join(start_menu_dir(), SHORTCUT_NAME)


def desktop_shortcut():
    return os.path.join(desktop_dir(), SHORTCUT_NAME)


def same_path(a, b):
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# ------------------------------------------------------------------ lối tắt (.lnk) qua COM IShellLink

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text):
    guid = _GUID()
    ctypes.memmove(ctypes.byref(guid), uuid.UUID(text).bytes_le, 16)
    return guid


_CLSID_SHELL_LINK = _guid("00021401-0000-0000-C000-000000000046")
_IID_SHELL_LINK_W = _guid("000214F9-0000-0000-C000-000000000046")
_IID_PERSIST_FILE = _guid("0000010B-0000-0000-C000-000000000046")
_HRESULT = ctypes.HRESULT  # lỗi COM được ctypes đổi thành OSError


def _method(obj, index, *argtypes, restype=_HRESULT):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


class _ShellLink:
    """IShellLinkW + IPersistFile, đủ để tạo và đọc lối tắt."""

    def __enter__(self):
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx(None, 0x2)  # STA; đã khởi tạo rồi thì bỏ qua
        ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        ole32.CoCreateInstance.restype = _HRESULT
        self.link, self.persist = ctypes.c_void_p(), ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(_CLSID_SHELL_LINK), None, 1, ctypes.byref(_IID_SHELL_LINK_W),
                               ctypes.byref(self.link))
        _method(self.link, 0, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
            self.link, ctypes.byref(_IID_PERSIST_FILE), ctypes.byref(self.persist))
        return self

    def __exit__(self, *exc):
        for obj in (self.persist, self.link):
            if obj:
                _method(obj, 2, restype=wintypes.ULONG)(obj)  # Release

    # Thứ tự hàm trong bảng IShellLinkW / IPersistFile theo Windows SDK.
    def set(self, target, workdir, description, icon):
        _method(self.link, 20, wintypes.LPCWSTR)(self.link, target)  # SetPath
        _method(self.link, 9, wintypes.LPCWSTR)(self.link, workdir)  # SetWorkingDirectory
        _method(self.link, 7, wintypes.LPCWSTR)(self.link, description)  # SetDescription
        _method(self.link, 17, wintypes.LPCWSTR, ctypes.c_int)(self.link, icon, 0)  # SetIconLocation

    def save(self, path):
        _method(self.persist, 6, wintypes.LPCWSTR, wintypes.BOOL)(self.persist, path, True)

    def load(self, path):
        _method(self.persist, 5, wintypes.LPCWSTR, wintypes.DWORD)(self.persist, path, 0)

    def target(self):
        buffer = ctypes.create_unicode_buffer(260)
        _method(self.link, 3, wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)(
            self.link, buffer, len(buffer), None, 0)  # GetPath
        return buffer.value


def create_shortcut(path, target):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _ShellLink() as link:
        link.set(target, os.path.dirname(target), "Dịch nhanh clipboard Việt – Anh", target)
        link.save(path)


def shortcut_target(path):
    with _ShellLink() as link:
        link.load(path)
        return link.target()


def remove_shortcuts(exe):
    """Xóa lối tắt Start Menu / Desktop nếu chúng trỏ tới exe của app."""
    for path in (start_menu_shortcut(), desktop_shortcut()):
        try:
            if os.path.exists(path) and same_path(shortcut_target(path), exe):
                os.remove(path)
        except OSError:
            pass


# ------------------------------------------------------------------ mục gỡ cài đặt (Settings → Apps)

def _read_value(name, key_path=None):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path or UNINSTALL_KEY) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def installed_version():
    return _read_value("DisplayVersion")


def installed_exe():
    """Exe đã cài (theo mục gỡ cài đặt), hoặc None nếu chưa cài."""
    location = _read_value("InstallLocation")
    return os.path.join(location, EXE_NAME) if location else None


def is_installed_copy(exe=None):
    return same_path(exe or sys.executable, installed_exe())


def register(exe, version=APP_VERSION):
    size_kb = max(1, os.path.getsize(exe) // 1024) if os.path.exists(exe) else 0
    values = {
        "DisplayName": (winreg.REG_SZ, APP_NAME),
        "DisplayVersion": (winreg.REG_SZ, version),
        "Publisher": (winreg.REG_SZ, APP_NAME),
        "DisplayIcon": (winreg.REG_SZ, exe),
        "InstallLocation": (winreg.REG_SZ, os.path.dirname(exe)),
        "UninstallString": (winreg.REG_SZ, f'"{exe}" --uninstall'),
        "QuietUninstallString": (winreg.REG_SZ, f'"{exe}" --uninstall --quiet'),
        "URLInfoAbout": (winreg.REG_SZ, f"https://github.com/{UPDATE_REPO}"),
        "NoModify": (winreg.REG_DWORD, 1),
        "NoRepair": (winreg.REG_DWORD, 1),
        "EstimatedSize": (winreg.REG_DWORD, size_kb),
    }
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, (kind, value) in values.items():
            winreg.SetValueEx(key, name, 0, kind, value)


def unregister():
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass


# ------------------------------------------------------------------ cài / sửa / gỡ

def version_tuple(text):
    parts = [int(p) if p.isdigit() else 0 for p in (text or "").split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def needs_admin(folder):
    return not updater.folder_writable(folder)


def install_to(folder, source=None, desktop=True, elevate=None):
    """Cài exe vào `folder`, ghi mục gỡ cài đặt và tạo lối tắt. Trả về đường dẫn exe đã cài.

    Ném InstallCancelled nếu người dùng từ chối quyền quản trị, OSError nếu chép thất bại.
    """
    source = source or sys.executable
    target = os.path.join(folder, EXE_NAME)
    current = installed_version()
    keep_newer = (same_path(installed_exe(), target) and os.path.exists(target)
                  and version_tuple(current) > version_tuple(APP_VERSION))
    if not keep_newer and not same_path(source, target):
        if updater.folder_writable(folder):
            os.makedirs(folder, exist_ok=True)
            updater.copy_with_retry(source, target, timeout=10)
        else:
            code = (elevate or updater.elevated_copy)(source, target)
            if code is None:
                raise InstallCancelled()
            if code != 0 or not os.path.exists(target):
                raise OSError("Không chép được ứng dụng vào thư mục cài đặt.")
    register(target, current if keep_newer else APP_VERSION)
    create_shortcut(start_menu_shortcut(), target)
    if desktop:
        create_shortcut(desktop_shortcut(), target)
    return target


def repair():
    """Mỗi lần bản đã cài khởi động: cập nhật số phiên bản, tạo lại lối tắt Start Menu nếu bị mất.

    Không tạo lại lối tắt Desktop, để tôn trọng người dùng đã tự xóa nó.
    """
    exe = installed_exe()
    if installed_version() != APP_VERSION:
        register(exe)
    if not os.path.exists(start_menu_shortcut()):
        create_shortcut(start_menu_shortcut(), exe)


def remove_files_later(folder, delay_seconds=4):
    """Sau khi app thoát: xóa đúng các file của app rồi xóa thư mục nếu đã trống.

    Không bao giờ xóa cả thư mục (người dùng có thể đã cài vào thư mục có file khác).
    Thư mục cần quyền quản trị thì Windows sẽ hỏi xác nhận.
    """
    files = " ".join(f'"{os.path.join(folder, name)}"' for name in OUR_FILES)
    updates = f'"{os.path.join(folder, "*.update.exe")}"'
    command = (f'ping 127.0.0.1 -n {delay_seconds + 1} >nul & del /f /q {files} {updates} >nul 2>&1 '
               f'& rmdir "{folder}" >nul 2>&1')
    verb = None if updater.folder_writable(folder) else "runas"
    return updater.shell_execute("cmd.exe", f'/c "{command}"', verb=verb)
