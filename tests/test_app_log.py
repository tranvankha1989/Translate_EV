import os
import tempfile
import unittest
from unittest.mock import patch

from translate_vietnam import app


class LogRotationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.path = os.path.join(self.folder, "app.log")
        patcher = patch.object(app, "_log_dirs", return_value=[self.folder])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_small_log_is_appended(self):
        app.log("dòng 1")
        app.log("dòng 2")
        with open(self.path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith("dòng 2"))

    def test_full_log_drops_oldest_lines_and_keeps_newest(self):
        with patch.object(app, "LOG_MAX_BYTES", 1000), patch.object(app, "LOG_KEEP_BYTES", 500):
            with open(self.path, "w", encoding="utf-8") as f:
                for i in range(100):
                    f.write(f"cũ {i:03d} tiếng Việt\n")
            self.assertGreaterEqual(os.path.getsize(self.path), 1000)
            app.log("mới nhất")
            size = os.path.getsize(self.path)
            with open(self.path, encoding="utf-8") as f:  # still valid UTF-8
                lines = f.read().splitlines()
        self.assertLess(size, 1000)
        self.assertTrue(lines[0].startswith("cũ "))  # cut at a line boundary
        self.assertNotIn("cũ 000 tiếng Việt", lines)
        self.assertIn("cũ 099 tiếng Việt", lines)
        self.assertTrue(lines[-1].endswith("mới nhất"))

    def test_falls_back_when_first_folder_is_not_writable(self):
        blocked = os.path.join(self.folder, "blocked")
        with open(blocked, "w") as f:  # a file, so it cannot be used as a folder
            f.write("x")
        fallback = os.path.join(self.folder, "fallback")
        with patch.object(app, "_log_dirs", return_value=[blocked, fallback]):
            app.log("dự phòng")
        with open(os.path.join(fallback, "app.log"), encoding="utf-8") as f:
            self.assertIn("dự phòng", f.read())


if __name__ == "__main__":
    unittest.main()
