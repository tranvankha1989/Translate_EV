import hashlib
import os
import tempfile
import unittest
from unittest.mock import patch

from translate_vietnam import updater


class FakeResponse:
    def __init__(self, status=200, payload=None, text="", content=b""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self._content = content

    def json(self):
        return self._payload

    def iter_content(self, size):
        for i in range(0, len(self._content), size):
            yield self._content[i:i + size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self.routes[url]


EXE = b"MZ" + b"\x00" * 5000 + b"new version"
EXE_SHA = hashlib.sha256(EXE).hexdigest()


def release_payload(tag="v1.2.0", with_checksum=True, digest=None):
    assets = [{"name": "TranslateVietnam.exe", "size": len(EXE),
               "browser_download_url": "https://dl/TranslateVietnam.exe", "digest": digest}]
    if with_checksum:
        assets.append({"name": "TranslateVietnam.exe.sha256", "size": 90,
                       "browser_download_url": "https://dl/TranslateVietnam.exe.sha256"})
    return {"tag_name": tag, "body": "Sửa lỗi.", "html_url": "https://github.com/x/y/releases/tag/" + tag,
            "assets": assets}


class VersionTests(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version("1.2"), (1, 2, 0))
        self.assertIsNone(updater.parse_version("latest"))
        self.assertTrue(updater.is_newer("v1.0.1", "1.0.0"))
        self.assertTrue(updater.is_newer("1.10.0", "1.9.9"))  # so sánh số, không so chuỗi
        self.assertFalse(updater.is_newer("v1.0.0", "1.0.0"))
        self.assertFalse(updater.is_newer("v0.9", "1.0.0"))
        self.assertFalse(updater.is_newer("beta", "1.0.0"))


class FetchTests(unittest.TestCase):
    def test_fetch_latest_reads_assets(self):
        session = FakeSession({updater.API_URL: FakeResponse(payload=release_payload())})
        release = updater.fetch_latest(session)
        self.assertEqual((release.version, release.tag), ("1.2.0", "v1.2.0"))
        self.assertEqual(release.asset_size, len(EXE))
        self.assertEqual(release.checksum_url, "https://dl/TranslateVietnam.exe.sha256")

    def test_no_release_or_missing_exe(self):
        self.assertIsNone(updater.fetch_latest(FakeSession({updater.API_URL: FakeResponse(404)})))
        payload = release_payload()
        payload["assets"] = []
        self.assertIsNone(updater.fetch_latest(FakeSession({updater.API_URL: FakeResponse(payload=payload)})))

    def test_check_only_returns_newer(self):
        for tag, newer in (("v99.0.0", True), (f"v{updater.APP_VERSION}", False)):
            session = FakeSession({updater.API_URL: FakeResponse(payload=release_payload(tag))})
            self.assertEqual(updater.check(session) is not None, newer)

    def test_github_error_is_reported(self):
        with self.assertRaises(updater.UpdateError):
            updater.fetch_latest(FakeSession({updater.API_URL: FakeResponse(403)}))

    def test_test_override_only_accepts_localhost(self):
        with patch.dict(os.environ, {updater.TEST_API_ENV: "https://evil.example/latest"}):
            self.assertEqual(updater.api_url(), updater.API_URL)
        with patch.dict(os.environ, {updater.TEST_API_ENV: "http://127.0.0.1:8765/latest.json"}):
            self.assertEqual(updater.api_url(), "http://127.0.0.1:8765/latest.json")


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.dest = os.path.join(tempfile.mkdtemp(), "TranslateVietnam.update.exe")

    def release(self, **kwargs):
        session = FakeSession({updater.API_URL: FakeResponse(payload=release_payload(**kwargs))})
        return updater.fetch_latest(session)

    def session(self, exe=EXE, checksum=f"{EXE_SHA}  TranslateVietnam.exe\n"):
        return FakeSession({
            "https://dl/TranslateVietnam.exe": FakeResponse(content=exe),
            "https://dl/TranslateVietnam.exe.sha256": FakeResponse(text=checksum),
        })

    def test_verified_download(self):
        progress = []
        updater.download(self.release(), self.dest, progress.append, self.session())
        with open(self.dest, "rb") as f:
            self.assertEqual(f.read(), EXE)
        self.assertEqual(progress[-1], 1.0)
        self.assertFalse(os.path.exists(self.dest + ".part"))

    def test_checksum_mismatch_is_rejected_and_cleaned(self):
        with self.assertRaises(updater.UpdateError):
            updater.download(self.release(), self.dest, session=self.session(checksum="0" * 64))
        self.assertFalse(os.path.exists(self.dest))
        self.assertFalse(os.path.exists(self.dest + ".part"))

    def test_non_exe_or_truncated_is_rejected(self):
        bad = b"<html>error</html>" + b"x" * (len(EXE) - 18)
        with self.assertRaises(updater.UpdateError):
            updater.download(self.release(), self.dest,
                             session=self.session(exe=bad, checksum=hashlib.sha256(bad).hexdigest()))
        short = EXE[:100]
        with self.assertRaises(updater.UpdateError):
            updater.download(self.release(), self.dest,
                             session=self.session(exe=short, checksum=hashlib.sha256(short).hexdigest()))

    def test_github_digest_is_used_without_checksum_file(self):
        release = self.release(with_checksum=False, digest=f"sha256:{EXE_SHA}")
        session = FakeSession({"https://dl/TranslateVietnam.exe": FakeResponse(content=EXE)})
        updater.download(release, self.dest, session=session)
        self.assertTrue(os.path.exists(self.dest))

    def test_release_without_any_checksum_is_refused(self):
        release = self.release(with_checksum=False)
        with self.assertRaises(updater.UpdateError):
            updater.download(release, self.dest, session=FakeSession({}))


class WhatsNewTests(unittest.TestCase):
    def test_only_whats_new_section_as_plain_text(self):
        notes = ("## Có gì mới\n\n- Dòng **phiên bản** nhấp nháy.\n- Xem [trang](https://x.y) và `F8`.\n\n"
                 "## Cách cập nhật\n\n- Bấm Cập nhật.")
        self.assertEqual(updater.whats_new(notes), "• Dòng phiên bản nhấp nháy.\n• Xem trang và F8.")

    def test_without_section_or_notes(self):
        self.assertEqual(updater.whats_new("## What's Changed\r\n* Fix by someone"),
                         "What's Changed\n• Fix by someone")
        self.assertEqual(updater.whats_new(""), "Sửa lỗi và cải thiện.")


class ArgsTests(unittest.TestCase):
    def test_parse_args(self):
        args = updater.parse_args(["--install-update", r"C:\Apps\TranslateVietnam.exe", "--wait-pid", "12,34"])
        self.assertEqual(args["install-update"], r"C:\Apps\TranslateVietnam.exe")
        self.assertEqual(args["wait-pid"], [12, 34])
        self.assertEqual(updater.parse_args(["--updated", "1.2.0", "--cleanup", "x.update.exe"]),
                         {"updated": "1.2.0", "cleanup": "x.update.exe"})
        self.assertEqual(updater.parse_args([]), {})

    def test_update_path_and_safe_cleanup(self):
        self.assertEqual(updater.update_path(r"C:\Apps\TranslateVietnam.exe"),
                         r"C:\Apps\TranslateVietnam.update.exe")
        folder = tempfile.mkdtemp()
        keep = os.path.join(folder, "TranslateVietnam.exe")
        remove = os.path.join(folder, "TranslateVietnam.update.exe")
        for path in (keep, remove):
            with open(path, "wb") as f:
                f.write(b"MZ")
        updater.cleanup(keep, timeout=0)  # chỉ xóa file *.update.exe
        updater.cleanup(remove, timeout=1)
        self.assertTrue(os.path.exists(keep))
        self.assertFalse(os.path.exists(remove))

    def test_child_env_resets_pyinstaller_state(self):
        with patch.dict(os.environ, {"_PYI_APPLICATION_HOME_DIR": "x", "_MEIPASS2": "y"}):
            env = updater._clean_env()
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", env)
        self.assertNotIn("_MEIPASS2", env)
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")


if __name__ == "__main__":
    unittest.main()
