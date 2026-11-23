"""Con trỏ vòng xoay khi đang dịch / sửa: đổi đúng một lần, luôn trả lại (không đổi con trỏ thật khi test)."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from translate_vietnam import busycursor, config


class BusyCursorTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        marker = os.path.join(tempfile.mkdtemp(), "cursor_busy")
        for p in (patch.object(busycursor, "_apply", side_effect=lambda style: self.calls.append(("apply", style))),
                  patch.object(busycursor, "restore", side_effect=lambda: self.calls.append(("restore",))),
                  patch.object(busycursor, "MARKER", marker)):
            p.start()
            self.addCleanup(p.stop)
        self.marker = marker
        self.addCleanup(busycursor.shutdown)

    def test_overlapping_jobs_change_cursor_once_and_restore_once(self):
        with busycursor.busy("spin"):
            with busycursor.busy("spin"):
                pass
            self.assertEqual(self.calls, [("apply", "spin")])  # việc thứ hai xong: vẫn còn việc đang chạy
        self.assertEqual(self.calls, [("apply", "spin"), ("restore",)])

    def test_restored_even_when_job_fails(self):
        with self.assertRaises(RuntimeError):
            with busycursor.busy("arrow"):
                raise RuntimeError("mất mạng")
        self.assertEqual(self.calls, [("apply", "arrow"), ("restore",)])

    def test_off_style_does_nothing(self):
        with busycursor.busy("off"):
            pass
        self.assertEqual(self.calls, [])

    def test_safety_timer_restores_stuck_cursor(self):
        with patch.object(busycursor, "MAX_SECONDS", 0.05):
            busycursor.begin("spin")
            busycursor._timer.join(1)
        self.assertEqual(self.calls, [("apply", "spin"), ("restore",)])
        busycursor.end()  # việc treo xong muộn: không đổi gì thêm
        self.assertEqual(len(self.calls), 2)

    def test_leftover_from_crash_is_restored_at_startup(self):
        busycursor.restore_if_left_over()
        self.assertEqual(self.calls, [])  # lần trước thoát bình thường: không đụng con trỏ
        open(self.marker, "w").close()
        busycursor.restore_if_left_over()
        self.assertEqual(self.calls, [("restore",)])

    def test_shutdown_restores_running_job(self):
        busycursor.begin("spin")
        busycursor.shutdown()
        self.assertEqual(self.calls, [("apply", "spin"), ("restore",)])

    def test_setting_is_validated(self):
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"busy_cursor": "rocket"}, f)
        self.assertEqual(config.Settings(path)["busy_cursor"], "spin")


class CursorLoadTests(unittest.TestCase):
    """Nạp con trỏ thật của Windows (không thay con trỏ hệ thống)."""

    def test_spinner_loads_on_this_windows(self):
        for style in ("spin", "arrow"):
            with self.subTest(style=style):
                self.assertTrue(busycursor._load(style))

    def test_falls_back_to_windows_cursor_when_files_missing(self):
        with patch.object(busycursor, "_scheme_file", return_value=""), \
                patch.dict(os.environ, {"SystemRoot": tempfile.mkdtemp()}):
            self.assertTrue(busycursor._load("spin"))


if __name__ == "__main__":
    unittest.main()
