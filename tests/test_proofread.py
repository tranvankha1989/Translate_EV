"""Sửa chính tả khi nhấn Ctrl+X, lĩnh vực mặc định máy in công nghiệp."""
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from tests.test_features import FakeSession, gemini_ok
from translate_vietnam import app, busycursor, config, filters, keywatch, proofreader, translator

PRINTER = "Kỹ thuật máy in công nghiệp"


class KeyWatchTests(unittest.TestCase):
    def setUp(self):
        keywatch.consume_cut()
        keywatch.record("copy", float("-inf"))

    def test_classify_key(self):
        self.assertEqual(keywatch.classify_key(keywatch.VK_X, ctrl=True), "cut")
        self.assertEqual(keywatch.classify_key(keywatch.VK_C, ctrl=True), "copy")
        self.assertEqual(keywatch.classify_key(keywatch.VK_INSERT, ctrl=True), "copy")
        self.assertIsNone(keywatch.classify_key(keywatch.VK_X, ctrl=False))  # gõ chữ x
        self.assertIsNone(keywatch.classify_key(keywatch.VK_X, ctrl=True, shift=True))
        self.assertIsNone(keywatch.classify_key(keywatch.VK_X, ctrl=True, alt=True))  # AltGr
        self.assertIsNone(keywatch.classify_key(keywatch.VK_X, ctrl=True, win=True))

    def test_cut_pending_window_and_later_copy(self):
        keywatch.record("cut", 100.0)
        self.assertTrue(keywatch.cut_pending(100.3))
        self.assertFalse(keywatch.cut_pending(100.0 + keywatch.CUT_WINDOW + 0.1))  # quá lâu
        keywatch.record("copy", 100.5)
        self.assertFalse(keywatch.cut_pending(100.6))  # Ctrl+C sau Ctrl+X: là copy
        keywatch.record("cut", 101.0)
        keywatch.consume_cut()
        self.assertFalse(keywatch.cut_pending(101.1))


class ClassifyChangeTests(unittest.TestCase):
    def test_cut_goes_to_proofread_even_when_translation_is_off(self):
        for mode in (app.AUTO, app.OFF):
            self.assertEqual(app.classify_change(mode, "double", True, "hai từ", 10.0, None, 0.0), "proofread")

    def test_cut_without_letters_is_a_normal_copy(self):
        self.assertEqual(app.classify_change(app.AUTO, "double", True, None, 10.0, None, 0.0), "record")
        self.assertEqual(app.classify_change(app.AUTO, "double", True, "12 34", 10.0, None, 0.0), "record")
        self.assertEqual(app.classify_change(app.OFF, "double", True, None, 10.0, None, 0.0), "ignore")

    def test_copy_keeps_old_translation_rules(self):
        self.assertEqual(app.classify_change(app.AUTO, "double", False, "x", 10.3, "x", 10.0), "translate")
        self.assertEqual(app.classify_change(app.AUTO, "single", False, "x", 10.0, None, 0.0), "debounce")
        self.assertEqual(app.classify_change(app.OFF, "single", False, "x", 10.0, None, 0.0), "ignore")


class ProofreaderTests(unittest.TestCase):
    def setUp(self):
        translator.clear_gemini_cache()
        translator.configure(gemini_api_key="k", gemini_backup_keys=[], gemini_model="gemini-3.5-flash-lite",
                             gemini_domain=PRINTER)
        self.addCleanup(translator.configure, gemini_api_key="", gemini_backup_keys=[],
                        gemini_model="", gemini_domain="")

    def test_skip_reason(self):
        self.assertEqual(proofreader.skip_reason(""), "đoạn quá ngắn")
        self.assertEqual(proofreader.skip_reason("  chữ  "), "đoạn quá ngắn")
        self.assertIsNone(proofreader.skip_reason("hai chữ"))
        self.assertIn("quá dài", proofreader.skip_reason("ab " * 8000))

    def test_request_keeps_whitespace_and_uses_cache(self):
        session = FakeSession(gemini_ok("Tôi muốn sửa lỗi chính tả."))
        with patch.object(translator, "_session", return_value=session):
            text = "\ntoi muuon sua loi chinh ta\n"
            self.assertEqual(proofreader.proofread(text), "\nTôi muốn sửa lỗi chính tả.\n")
            self.assertEqual(proofreader.proofread(text), "\nTôi muốn sửa lỗi chính tả.\n")  # cache
        self.assertEqual(len(session.calls), 1)
        _, kwargs = session.calls[0]
        instruction = kwargs["json"]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("copy editor", instruction)
        self.assertIn("never translate", instruction)
        self.assertIn(PRINTER, instruction)
        self.assertIn("toi muuon sua loi chinh ta", kwargs["json"]["contents"][0]["parts"][0]["text"])

    def test_abnormal_results_are_rejected(self):
        cases = (("Câu này viết sai chíng tả.", "This sentence has a spelling mistake."),  # đổi ngôn ngữ
                 ("Kiểm tra đầu in.", "Kiểm tra đầu in. " * 20))  # trả lời / viết thêm quá nhiều
        for original, answer in cases:
            translator.clear_gemini_cache()
            with self.subTest(answer=answer[:20]), \
                    patch.object(translator, "_session", return_value=FakeSession(gemini_ok(answer))):
                with self.assertRaises(proofreader.ProofreadRejected):
                    proofreader.proofread(original)

    def test_translation_prompt_describes_printer_field(self):
        instruction = translator._gemini_instruction("vi", PRINTER)
        self.assertIn("industrial printers", instruction)
        self.assertNotIn("industrial printers", translator._gemini_instruction("vi", "Y tế – Dược"))


class DoProofreadTests(unittest.TestCase):
    """Luồng đầy đủ: đọc clipboard, lọc, gọi Gemini, ghi bản đã sửa vào clipboard."""

    def setUp(self):
        self.settings = config.Settings(os.path.join(tempfile.mkdtemp(), "config.json"))
        translator.clear_gemini_cache()
        translator.configure(gemini_api_key="k", gemini_model="gemini-3.5-flash-lite", gemini_domain=PRINTER)
        self.addCleanup(translator.configure, gemini_api_key="", gemini_model="", gemini_domain="")
        self.written = []
        patches = (
            patch.object(app, "settings", self.settings),
            patch.object(app, "clipboard_sequence", return_value=7),
            patch.object(app, "replace_clipboard", side_effect=lambda text, seq, job: self.written.append(text) or True),
            patch.object(app, "save_gemini_usage"),
            patch.object(app, "notify"),
            patch.object(filters, "clipboard_is_private", return_value=False),
            patch.object(filters, "clipboard_owner_app", return_value="winword.exe"),
            patch.object(busycursor, "_apply"),  # không đổi con trỏ thật khi chạy test
            patch.object(busycursor, "restore"),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_job(self, clipboard, answer="Đã sửa đúng chính tả."):
        session = FakeSession(gemini_ok(answer))
        with patch.object(app.pyperclip, "paste", return_value=clipboard), \
                patch.object(translator, "_session", return_value=session):
            app.do_proofread(7, threading.Event())
        return session

    def test_fixed_text_replaces_clipboard(self):
        self.run_job("da sua dung chinh ta")
        self.assertEqual(self.written, ["Đã sửa đúng chính tả."])

    def test_excluded_app_and_missing_key_keep_clipboard(self):
        with patch.object(filters, "clipboard_owner_app", return_value="code.exe"):
            self.assertEqual(self.run_job("x = foo(bar)  # sửa").calls, [])
        translator.configure(gemini_api_key="")
        self.assertEqual(self.run_job("da sua dung chinh ta").calls, [])
        app.notify.assert_called_once()  # nhắc nhập API key Gemini
        self.assertEqual(self.written, [])


class DomainMigrationTests(unittest.TestCase):
    def load(self, stored):
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stored, f)
        return config.Settings(path)

    def test_old_default_moves_to_printer_once(self):
        self.assertEqual(self.load({"domain": "Kỹ thuật"})["domain"], PRINTER)
        self.assertEqual(self.load({"domain": "Kỹ thuật", "domain_default_migrated": True})["domain"], "Kỹ thuật")
        self.assertEqual(self.load({"domain": "Y tế – Dược"})["domain"], "Y tế – Dược")
        self.assertEqual(config.DOMAINS[0], PRINTER)
        self.assertEqual(translator.DEFAULT_DOMAIN, PRINTER)


if __name__ == "__main__":
    unittest.main()
