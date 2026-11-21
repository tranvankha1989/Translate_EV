"""Tự cập nhật qua GitHub Releases (máy người dùng không cần git).

Quy trình (chỉ với bản exe):
1. Hỏi API GitHub bản phát hành mới nhất, so với APP_VERSION.
2. Tải TranslateVietnam.exe của bản mới về cạnh exe đang chạy, tên *.update.exe,
   rồi kiểm tra kích thước, chữ ký "MZ" và mã SHA-256 (file .sha256 đi kèm bản phát hành).
3. Chạy file vừa tải với --install-update; app cũ thoát.
4. File mới chờ app cũ thoát hẳn, chép đè lên exe cũ, mở lại exe đó với --updated.
5. Exe đã cập nhật xóa file *.update.exe khi khởi động.
"""
import ctypes
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

import requests

from config import APP_VERSION, UPDATE_REPO

ASSET_NAME = "TranslateVietnam.exe"
CHECKSUM_NAME = ASSET_NAME + ".sha256"
API_URL = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{UPDATE_REPO}/releases/latest"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": f"TranslateVietnam/{APP_VERSION}"}
# Chỉ để kiểm thử: trỏ tới máy chủ giả lập trên chính máy này (không nhận địa chỉ khác).
TEST_API_ENV = "TRANSLATE_VIETNAM_UPDATE_API"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
SYNCHRONIZE = 0x00100000


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    tag: str
    notes: str
    page_url: str
    asset_url: str
    asset_size: int
    checksum_url: str | None
    digest: str | None  # "sha256:..." do GitHub tự tính, nếu API có trả về


def parse_version(text):
    """"v1.2.3" -> (1, 2, 3); None nếu không phải số phiên bản."""
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", (text or "").strip(), re.I)
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def is_newer(remote, current=APP_VERSION):
    remote_v, current_v = parse_version(remote), parse_version(current)
    return bool(remote_v and current_v and remote_v > current_v)


def api_url():
    override = os.environ.get(TEST_API_ENV, "")
    if override.startswith(("http://127.0.0.1:", "http://localhost:")):
        return override
    return API_URL


def fetch_latest(session=None, timeout=10):
    """Bản phát hành mới nhất (không tính bản nháp / thử nghiệm), hoặc None nếu chưa có."""
    session = session or requests.Session()
    r = session.get(api_url(), headers=HEADERS, timeout=timeout)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise UpdateError(f"GitHub trả về HTTP {r.status_code}")
    data = r.json()
    assets = {asset.get("name"): asset for asset in data.get("assets") or []}
    exe = assets.get(ASSET_NAME)
    version = data.get("tag_name") or ""
    if not exe or not parse_version(version):
        return None
    checksum = assets.get(CHECKSUM_NAME)
    return Release(
        version=version.lstrip("vV"),
        tag=version,
        notes=(data.get("body") or "").strip(),
        page_url=data.get("html_url") or RELEASES_PAGE,
        asset_url=exe.get("browser_download_url") or "",
        asset_size=int(exe.get("size") or 0),
        checksum_url=checksum.get("browser_download_url") if checksum else None,
        digest=exe.get("digest"),
    )


def check(session=None):
    """Bản mới hơn bản đang chạy, hoặc None."""
    release = fetch_latest(session)
    return release if release and is_newer(release.version) else None


def parse_checksum(text):
    match = re.search(r"\b[0-9a-fA-F]{64}\b", text or "")
    return match.group(0).lower() if match else None


def expected_sha256(release, session=None):
    if release.checksum_url:
        session = session or requests.Session()
        r = session.get(release.checksum_url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=15)
        if r.status_code == 200:
            value = parse_checksum(r.text)
            if value:
                return value
    if release.digest and release.digest.lower().startswith("sha256:"):
        return parse_checksum(release.digest[7:])
    raise UpdateError("Bản phát hành thiếu mã kiểm tra SHA-256, không an toàn để cài.")


def can_self_update():
    return bool(getattr(sys, "frozen", False))


def update_path(exe_path):
    return os.path.splitext(exe_path)[0] + ".update.exe"


def download(release, dest, on_progress=None, session=None):
    """Tải và kiểm tra file exe mới. Lỗi ở bất kỳ bước nào thì xóa file dở dang."""
    session = session or requests.Session()
    expected = expected_sha256(release, session)
    digest = hashlib.sha256()
    received = 0
    partial = dest + ".part"
    try:
        with session.get(release.asset_url, headers={"User-Agent": HEADERS["User-Agent"]},
                         stream=True, timeout=(10, 60)) as r:
            if r.status_code != 200:
                raise UpdateError(f"Không tải được bản cập nhật (HTTP {r.status_code})")
            with open(partial, "wb") as f:
                for block in r.iter_content(256 * 1024):
                    if not block:
                        continue
                    if received == 0 and not block.startswith(b"MZ"):
                        raise UpdateError("File tải về không phải chương trình Windows.")
                    f.write(block)
                    digest.update(block)
                    received += len(block)
                    if on_progress and release.asset_size:
                        on_progress(min(1.0, received / release.asset_size))
        if release.asset_size and received != release.asset_size:
            raise UpdateError("File tải về không đủ dung lượng.")
        if digest.hexdigest() != expected:
            raise UpdateError("Mã SHA-256 không khớp, đã hủy cập nhật.")
        os.replace(partial, dest)
        return dest
    except BaseException:
        try:
            os.remove(partial)
        except OSError:
            pass
        raise


def _clean_env():
    """Môi trường cho exe khác: bỏ biến nội bộ của PyInstaller để exe mới tự giải nén riêng."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI_", "_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _launch(args):
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, close_fds=True, creationflags=flags, env=_clean_env(),
                     cwd=os.path.dirname(args[0]))


def start_install(new_exe, exe_path):
    """Chạy file mới ở chế độ cài đặt; app hiện tại phải thoát ngay sau đó."""
    pids = [os.getpid()]
    if getattr(sys, "frozen", False):
        pids.append(os.getppid())  # bản exe 1 file: tiến trình cha cũng giữ file exe
    _launch([new_exe, "--install-update", exe_path, "--wait-pid", ",".join(map(str, pids))])


def wait_for_pids(pids, timeout=30.0):
    deadline = time.monotonic() + timeout
    for pid in pids:
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            continue  # tiến trình đã thoát
        try:
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            kernel32.WaitForSingleObject(handle, remaining)
        finally:
            kernel32.CloseHandle(handle)


def install_update(target, wait_pids, version=APP_VERSION, timeout=30.0):
    """Chạy trong file *.update.exe: chép chính nó đè lên exe cũ rồi mở lại exe đó."""
    wait_for_pids(wait_pids, timeout)
    source = sys.executable
    deadline = time.monotonic() + timeout
    while True:
        try:
            shutil.copyfile(source, target)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)
    _launch([target, "--updated", version, "--cleanup", source])


def cleanup(path, timeout=20.0):
    """Xóa file *.update.exe (có thể còn bị khóa vài giây trong lúc tiến trình cài thoát)."""
    if not path or not os.path.basename(path).lower().endswith(".update.exe"):
        return
    deadline = time.monotonic() + timeout
    while os.path.exists(path):
        try:
            os.remove(path)
            return
        except OSError:
            if time.monotonic() >= deadline:
                return
            time.sleep(0.5)


def parse_args(argv):
    """Đọc các tham số --install-update, --wait-pid, --updated, --cleanup."""
    result = {}
    i = 0
    while i < len(argv):
        name = argv[i]
        if name in ("--install-update", "--wait-pid", "--updated", "--cleanup") and i + 1 < len(argv):
            result[name.lstrip("-")] = argv[i + 1]
            i += 2
        else:
            i += 1
    if "wait-pid" in result:
        result["wait-pid"] = [int(p) for p in result["wait-pid"].split(",") if p.strip().isdigit()]
    return result
