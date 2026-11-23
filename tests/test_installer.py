import ctypes
import os
import shutil
import sys
import tempfile
import unittest
import winreg
from unittest.mock import patch

from translate_vietnam import config
from translate_vietnam import installer
from translate_vietnam import updater

TEST_KEY = r"Software\TranslateVietnamTest\Uninstall"


def delete_test_key():
    for path in (TEST_KEY, r"Software\TranslateVietnamTest\Run", r"Software\TranslateVietnamTest"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass


class InstallerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = os.path.join(self.tmp, "Downloads", "TranslateVietnam.exe")
        os.makedirs(os.path.dirname(self.source))
        with open(self.source, "wb") as f:
            f.write(b"MZ new build")
        self.start_menu = os.path.join(self.tmp, "StartMenu", "Translate Vietnam.lnk")
        self.desktop = os.path.join(self.tmp, "Desktop", "Translate Vietnam.lnk")
        for target, value in ((installer, "UNINSTALL_KEY"),):
            p = patch.object(target, value, TEST_KEY)
            p.start()
            self.addCleanup(p.stop)
        for name, value in (("start_menu_shortcut", self.start_menu), ("desktop_shortcut", self.desktop)):
            p = patch.object(installer, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        delete_test_key()
        self.addCleanup(delete_test_key)


class PathTests(unittest.TestCase):
    def test_default_dir_is_64bit_program_files(self):
        folder = installer.default_install_dir()
        self.assertTrue(folder.endswith(r"Program Files\Translate Vietnam"), folder)
        self.assertNotIn("(x86)", folder)

    def test_generic_folders_get_an_app_subfolder(self):
        self.assertEqual(installer.normalize_install_dir("D:\\"), r"D:\Translate Vietnam")
        self.assertEqual(installer.normalize_install_dir(r"D:\Apps"), r"D:\Apps\Translate Vietnam")
        self.assertEqual(installer.normalize_install_dir(r'"D:\Apps\Translate Vietnam"'),
                         r"D:\Apps\Translate Vietnam")

    def test_folder_writable(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertTrue(updater.folder_writable(tmp))
        self.assertTrue(updater.folder_writable(os.path.join(tmp, "not", "yet", "created")))
        if not ctypes.windll.shell32.IsUserAnAdmin():
            self.assertFalse(updater.folder_writable(installer.default_install_dir()))

    def test_parse_install_args(self):
        args = updater.parse_args(["--uninstall", "--quiet"])
        self.assertEqual(args, {"uninstall": True, "quiet": True})
        args = updater.parse_args(["--install-to", r"D:\Apps", "--no-desktop", "--copy-to", r"C:\x.exe"])
        self.assertEqual(args, {"install-to": r"D:\Apps", "no-desktop": True, "copy-to": r"C:\x.exe"})


class InstallTests(InstallerTestCase):
    def test_install_to_writable_folder(self):
        folder = os.path.join(self.tmp, "Apps", "Translate Vietnam")
        target = installer.install_to(folder, source=self.source, desktop=True)
        self.assertEqual(target, os.path.join(folder, "TranslateVietnam.exe"))
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"MZ new build")
        self.assertEqual(installer.installed_version(), config.APP_VERSION)
        self.assertTrue(installer.same_path(installer.installed_exe(), target))
        self.assertTrue(installer.is_installed_copy(target))
        self.assertFalse(installer.is_installed_copy(self.source))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as key:
            self.assertEqual(winreg.QueryValueEx(key, "UninstallString")[0], f'"{target}" --uninstall')
            self.assertEqual(winreg.QueryValueEx(key, "DisplayName")[0], config.APP_NAME)
        for shortcut in (self.start_menu, self.desktop):
            self.assertTrue(installer.same_path(installer.shortcut_target(shortcut), target))

    def test_without_desktop_shortcut(self):
        installer.install_to(os.path.join(self.tmp, "A"), source=self.source, desktop=False)
        self.assertTrue(os.path.exists(self.start_menu))
        self.assertFalse(os.path.exists(self.desktop))

    def test_protected_folder_uses_elevated_copy(self):
        folder = os.path.join(self.tmp, "ProgramFiles", "Translate Vietnam")
        calls = []

        def elevate(source, target):
            calls.append((source, target))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(source, target)
            return 0

        with patch.object(updater, "folder_writable", return_value=False):
            target = installer.install_to(folder, source=self.source, elevate=elevate)
        self.assertEqual(calls, [(self.source, target)])
        self.assertTrue(os.path.exists(target))

    def test_declined_or_failed_elevation(self):
        folder = os.path.join(self.tmp, "ProgramFiles", "Translate Vietnam")
        with patch.object(updater, "folder_writable", return_value=False):
            with self.assertRaises(installer.InstallCancelled):
                installer.install_to(folder, source=self.source, elevate=lambda s, t: None)
            with self.assertRaises(OSError):
                installer.install_to(folder, source=self.source, elevate=lambda s, t: 1)
        self.assertIsNone(installer.installed_exe())  # không ghi mục cài đặt khi thất bại

    def test_never_downgrades_a_newer_install(self):
        folder = os.path.join(self.tmp, "Apps", "Translate Vietnam")
        target = installer.install_to(folder, source=self.source)
        with open(target, "wb") as f:
            f.write(b"MZ newer build")
        installer.register(target, "99.0.0")
        installer.install_to(folder, source=self.source)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"MZ newer build")
        self.assertEqual(installer.installed_version(), "99.0.0")

    def test_repair_updates_version_and_start_menu_only(self):
        target = installer.install_to(os.path.join(self.tmp, "Apps"), source=self.source)
        installer.register(target, "0.0.1")
        os.remove(self.start_menu)
        os.remove(self.desktop)
        installer.repair()
        self.assertEqual(installer.installed_version(), config.APP_VERSION)
        self.assertTrue(os.path.exists(self.start_menu))
        self.assertFalse(os.path.exists(self.desktop))  # tôn trọng người dùng đã xóa lối tắt Desktop

    def test_remove_shortcuts_only_removes_ours(self):
        target = installer.install_to(os.path.join(self.tmp, "Apps"), source=self.source)
        other = os.path.join(self.tmp, "other.exe")
        with open(other, "wb") as f:
            f.write(b"MZ")
        installer.create_shortcut(self.desktop, other)  # lối tắt cùng tên nhưng của app khác
        installer.remove_shortcuts(target)
        self.assertFalse(os.path.exists(self.start_menu))
        self.assertTrue(os.path.exists(self.desktop))
        installer.unregister()
        self.assertIsNone(installer.installed_exe())


class MaybeInstallTests(InstallerTestCase):
    def setUp(self):
        super().setUp()
        from translate_vietnam import app
        self.app = app
        self.settings = config.Settings(os.path.join(self.tmp, "config.json"))
        for obj, name, value in ((app, "settings", self.settings), (sys, "frozen", True),
                                 (sys, "executable", self.source)):
            p = patch.object(obj, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)
        self.launched = []
        p = patch.object(app, "launch_installed", side_effect=lambda target, args: self.launched.append(target))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(app, "show_message")
        self.message = p.start()
        self.addCleanup(p.stop)

    def test_not_frozen_or_portable_runs_normally(self):
        with patch.object(sys, "frozen", False):
            self.assertFalse(self.app.maybe_install({}))
        self.assertFalse(self.app.maybe_install({"portable": True}))

    def test_dialog_install_hands_over_to_installed_copy(self):
        folder = os.path.join(self.tmp, "Apps", "Translate Vietnam")
        with patch.object(self.app, "ask_install", return_value=("install", folder, False)):
            self.assertTrue(self.app.maybe_install({}))
        self.assertEqual(self.launched, [os.path.join(folder, "TranslateVietnam.exe")])

    def test_cancel_and_portable_choices(self):
        with patch.object(self.app, "ask_install", return_value=None):
            self.assertTrue(self.app.maybe_install({}))  # Hủy: thoát
        with patch.object(self.app, "ask_install", return_value=("portable",)):
            self.assertFalse(self.app.maybe_install({}))
        self.assertTrue(self.settings["run_without_install"])
        with patch.object(self.app, "ask_install", side_effect=AssertionError("không hỏi lại")):
            self.assertFalse(self.app.maybe_install({}))
        self.assertEqual(self.launched, [])

    def test_declined_admin_runs_without_install(self):
        with patch.object(self.app, "ask_install", return_value=("install", r"C:\Program Files\Translate Vietnam", True)), \
                patch.object(installer, "install_to", side_effect=installer.InstallCancelled):
            self.assertFalse(self.app.maybe_install({}))
        self.message.assert_called_once()
        self.assertEqual(self.launched, [])

    def test_already_installed_opens_installed_copy_without_asking(self):
        folder = os.path.join(self.tmp, "Apps", "Translate Vietnam")
        target = installer.install_to(folder, source=self.source, desktop=False)
        with patch.object(self.app, "ask_install", side_effect=AssertionError("không hỏi")):
            self.assertTrue(self.app.maybe_install({}))
        self.assertEqual(self.launched, [target])

    def test_newer_download_upgrades_installed_copy(self):
        folder = os.path.join(self.tmp, "Apps", "Translate Vietnam")
        target = installer.install_to(folder, source=self.source, desktop=False)
        installer.register(target, "0.0.1")
        with open(target, "wb") as f:
            f.write(b"MZ old")
        self.assertTrue(self.app.maybe_install({}))
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"MZ new build")
        self.assertEqual(installer.installed_version(), config.APP_VERSION)

    def test_installed_copy_does_not_install_again(self):
        folder = os.path.join(self.tmp, "Apps", "Translate Vietnam")
        target = installer.install_to(folder, source=self.source, desktop=False)
        with patch.object(sys, "executable", target):
            self.assertFalse(self.app.maybe_install({}))


class AutostartRemovalTests(unittest.TestCase):
    def test_only_removes_entry_pointing_into_folder(self):
        from translate_vietnam import app
        run_key = r"Software\TranslateVietnamTest\Run"
        self.addCleanup(delete_test_key)
        with patch.object(app, "RUN_KEY", run_key):
            for command, removed in ((r'"C:\Apps\Translate Vietnam\TranslateVietnam.exe"', True),
                                     (r'"C:\Other\TranslateVietnam.exe"', False)):
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_key) as key:
                    winreg.SetValueEx(key, app.RUN_VALUE, 0, winreg.REG_SZ, command)
                app.remove_autostart_for(r"C:\Apps\Translate Vietnam")
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as key:
                    try:
                        winreg.QueryValueEx(key, app.RUN_VALUE)
                        exists = True
                    except FileNotFoundError:
                        exists = False
                self.assertEqual(exists, not removed, command)


if __name__ == "__main__":
    unittest.main()
