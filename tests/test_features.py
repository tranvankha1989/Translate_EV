import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from translate_vietnam import config
from translate_vietnam import filters
from translate_vietnam import hotkeys
from translate_vietnam import translator


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "sub", "config.json")

    def test_first_run_uses_defaults_and_round_trips(self):
        settings = config.Settings(self.path)
        self.assertTrue(settings.first_run)
        self.assertEqual(settings["mode"], "auto")
        self.assertEqual(settings["domain"], "Kỹ thuật máy in công nghiệp")
        self.assertTrue(settings.update(mode="vi", domain="Y tế – Dược", excluded_apps=["code.exe"]))
        again = config.Settings(self.path)
        self.assertFalse(again.first_run)
        self.assertEqual((again["mode"], again["domain"], again["excluded_apps"]),
                         ("vi", "Y tế – Dược", ["code.exe"]))

    def test_api_key_is_encrypted_on_disk(self):
        settings = config.Settings(self.path)
        settings.update(gemini_api_key="AIza-secret-123")
        with open(self.path, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("AIza-secret-123", raw)
        self.assertIn("dpapi:", raw)
        self.assertEqual(config.Settings(self.path)["gemini_api_key"], "AIza-secret-123")

    def test_invalid_values_fall_back_to_defaults(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"mode": "bogus", "last_on_mode": "off", "autostart": "yes",
                       "domain": "", "gemini_api_key": "dpapi:not-base64!"}, f)
        settings = config.Settings(self.path)
        self.assertEqual(settings["mode"], "auto")
        self.assertEqual(settings["last_on_mode"], "auto")
        self.assertIs(settings["autostart"], True)
        self.assertEqual(settings["domain"], "Kỹ thuật máy in công nghiệp")
        self.assertEqual(settings["gemini_api_key"], "")

    def test_corrupt_file_does_not_crash(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(config.Settings(self.path)["mode"], "auto")

    def test_returned_lists_are_copies(self):
        settings = config.Settings(self.path)
        settings["excluded_apps"].append("x.exe")
        self.assertNotIn("x.exe", settings["excluded_apps"])


class FilterTests(unittest.TestCase):
    def test_sensitive_text_is_detected(self):
        for text in ("P@ssw0rd!", "https://example.com/a?b=1", "www.example.com", "an@example.com",
                     r"C:\Program Files\App\app.exe", "/usr/local/bin", "ghp_A1b2C3d4E5f6G7h8I9j0",
                     "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"):
            with self.subTest(text=text):
                self.assertTrue(filters.looks_sensitive(text))

    def test_normal_text_is_not_sensitive(self):
        for text in ("Hello", "internationalization", "Xin chào các bạn", "Đường", "Check the gauge.",
                     "Open https://example.com to read more", "line one\nline two", "COVID"):
            with self.subTest(text=text):
                self.assertFalse(filters.looks_sensitive(text))

    def test_app_names_are_normalized(self):
        self.assertEqual(filters.normalize_app_names(["KeePass", r'"C:\Apps\Code.exe"', "", "keepass.exe"]),
                         ["keepass.exe", "code.exe"])

    def test_skip_reason_order_and_manual_bypass(self):
        with patch.object(filters, "clipboard_is_private", return_value=False), \
                patch.object(filters, "clipboard_owner_app", return_value="code.exe"):
            self.assertEqual(filters.skip_reason("hello world", excluded_apps=["Code"]), "copy từ code.exe")
            self.assertIsNone(filters.skip_reason("hello world", excluded_apps=["Code"], manual=True))
            self.assertIsNotNone(filters.skip_reason("P@ssw0rd!"))
            self.assertIsNone(filters.skip_reason("P@ssw0rd!", skip_sensitive=False))
        with patch.object(filters, "clipboard_is_private", return_value=True):
            self.assertEqual(filters.skip_reason("hello", manual=True), "nội dung riêng tư")
            self.assertIsNone(filters.skip_reason("hello", skip_private=False, manual=True))


class HotkeyTests(unittest.TestCase):
    def test_parse_and_normalize(self):
        self.assertEqual(hotkeys.parse("f8"), (hotkeys.MOD_NOREPEAT, 0x77))
        self.assertEqual(hotkeys.normalize("Shift + Ctrl + T"), "ctrl+shift+t")
        self.assertEqual(hotkeys.normalize(""), "")
        self.assertEqual(hotkeys.display("ctrl+shift+t"), "Ctrl+Shift+T")
        self.assertEqual(hotkeys.display("f10"), "F10")
        self.assertEqual(hotkeys.display(""), "Không dùng")

    def test_rejects_unsafe_or_invalid_keys(self):
        for spec in ("t", "shift+t", "alt+f4", "f25", "ctrl", "ctrl+pause"):
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    hotkeys.parse(spec)

    def test_spec_from_vk(self):
        self.assertEqual(hotkeys.spec_from_vk(0x79), "f10")
        self.assertEqual(hotkeys.spec_from_vk(0x54, ctrl=True, shift=True), "ctrl+shift+t")
        with self.assertRaises(ValueError):
            hotkeys.spec_from_vk(0x54)  # chữ cái không kèm Ctrl/Alt/Win
        with self.assertRaises(ValueError):
            hotkeys.spec_from_vk(0x13, ctrl=True)  # Pause


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def gemini_ok(text):
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


class GeminiTests(unittest.TestCase):
    def setUp(self):
        translator.clear_gemini_cache()
        translator._google_cached.cache_clear()
        translator.configure(gemini_api_key="", gemini_backup_keys=[], gemini_model="", gemini_domain="")
        translator.configure(gemini_api_key="test-key", gemini_model="gemini-3.5-flash-lite",
                             gemini_domain="Cơ khí – Chế tạo")
        self.addCleanup(translator.configure, gemini_api_key="", gemini_backup_keys=[],
                        gemini_model="", gemini_domain="")

    def test_request_shape_and_key_in_header(self):
        session = FakeSession(gemini_ok("<text>\nKiểm tra đồng hồ áp suất.\n</text>"))
        with patch.object(translator, "_session", return_value=session):
            result = translator.gemini_translate("\nCheck the pressure gauge.\n", "vi")
        self.assertEqual(result, "\nKiểm tra đồng hồ áp suất.\n")  # giữ xuống dòng, bỏ thẻ <text>
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://generativelanguage.googleapis.com/v1beta/models/"
                              "gemini-3.5-flash-lite:generateContent")
        self.assertNotIn("test-key", url)
        self.assertEqual(kwargs["headers"], {"x-goog-api-key": "test-key"})
        instruction = kwargs["json"]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("Cơ khí – Chế tạo", instruction)
        self.assertIn("into Vietnamese", instruction)

    def test_auto_mode_picks_direction_and_uses_gemini(self):
        session = FakeSession(gemini_ok("I am checking the pump."), gemini_ok("Tôi đang kiểm tra máy bơm."))
        with patch.object(translator, "_session", return_value=session), \
                patch.object(translator, "_google_cached", side_effect=AssertionError("no Google")):
            self.assertEqual(translator.translate("Tôi đang kiểm tra máy bơm.", "auto"), "I am checking the pump.")
            self.assertEqual(translator.translate("I am checking the pump.", "auto"), "Tôi đang kiểm tra máy bơm.")
        self.assertIn("into English", session.calls[0][1]["json"]["systemInstruction"]["parts"][0]["text"])
        self.assertIn("into Vietnamese", session.calls[1][1]["json"]["systemInstruction"]["parts"][0]["text"])

    def test_quota_error_falls_back_to_google_and_pauses_gemini(self):
        quota = FakeResponse(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota",
                                             "details": [{"retryDelay": "120s"}]}})
        session = FakeSession(quota)
        with patch.object(translator, "_session", return_value=session), \
                patch.object(translator, "_google_cached", side_effect=lambda c, t: (f"google:{c}", "en")):
            self.assertEqual(translator.translate("hello there", "vi"), "google:hello there")
            self.assertFalse(translator.gemini_ready())
            self.assertGreaterEqual(translator._key_state["test-key"]["retry_at"] - time.monotonic(), 115)
            self.assertIn("hết lượt", translator.last_gemini_error)
            # Trong thời gian tạm dừng, không gọi Gemini nữa.
            self.assertEqual(translator.translate("second one", "vi"), "google:second one")
        self.assertEqual(len(session.calls), 1)

    def test_instruction_explains_chat_messages(self):
        to_en = translator._gemini_instruction("en", "Chung")
        to_vi = translator._gemini_instruction("vi", "Chung")
        self.assertIn("@mentions", to_en)
        self.assertIn("a = anh", to_en)
        self.assertIn("@mentions", to_vi)
        self.assertNotIn("a = anh", to_vi)  # bảng viết tắt tiếng Việt chỉ cần khi dịch sang tiếng Anh

    def test_untranslated_vietnamese_is_sent_again(self):
        chat = "A @Anh Tú còn a e nào chưa có việc nhờ tạo Logo cho ĐHL nha a"
        english = "@Anh Tú, does anyone still have no work? Please ask them to make a logo for ĐHL."
        session = FakeSession(gemini_ok(chat[2:]), gemini_ok(english))
        with patch.object(translator, "_session", return_value=session), \
                patch.object(translator, "_google_cached", side_effect=AssertionError("no Google")):
            self.assertEqual(translator.translate(chat, "auto"), english)
            self.assertEqual(translator.translate(chat, "auto"), english)  # nhớ bản đã dịch, không gọi lại
        self.assertEqual(len(session.calls), 2)
        retry_instruction = session.calls[1][1]["json"]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("NOT English yet", retry_instruction)

    def test_untranslated_twice_falls_back_to_free_service(self):
        chat = "a e nào rảnh thì nhắn em nha"
        session = FakeSession(gemini_ok(chat), gemini_ok(chat))
        with patch.object(translator, "_session", return_value=session), \
                patch.object(translator, "_google_cached", side_effect=lambda c, t: (f"google:{c}", "vi")):
            self.assertEqual(translator.translate(chat, "en"), f"google:{chat}")
        self.assertEqual(len(session.calls), 2)
        self.assertNotIn((chat, ("translate", "en", translator.gemini_domain), translator.gemini_model),
                         translator._gemini_cache)

    def test_english_with_vietnamese_names_is_not_untranslated(self):
        self.assertFalse(translator._looks_untranslated(
            "Gửi anh Nguyễn Đức Thắng", "To Mr. Nguyễn Đức Thắng", "en"))
        self.assertFalse(translator._looks_untranslated("Cảm ơn anh Tú", "Thanks, Tú", "en"))
        self.assertFalse(translator._looks_untranslated("Xin chào", "Xin chào", "vi"))
        self.assertTrue(translator._looks_untranslated(
            "A @Anh Tú còn a e nào chưa có việc", "@Anh Tú còn a e nào chưa có việc", "en"))

    def test_auto_model_uses_latest_alias(self):
        translator.configure(gemini_model="auto")
        session = FakeSession(gemini_ok("Bơm"))
        with patch.object(translator, "_session", return_value=session):
            self.assertEqual(translator.gemini_translate("Pump", "vi"), "Bơm")
        self.assertIn("/models/gemini-flash-lite-latest:generateContent", session.calls[0][0])

    def test_auto_model_falls_back_when_alias_is_gone(self):
        translator.configure(gemini_model="auto")
        missing = FakeResponse(404, {"error": {"code": 404, "message": "models/x is not found"}})
        session = FakeSession(missing, gemini_ok("Bơm"))
        with patch.object(translator, "_session", return_value=session):
            self.assertEqual(translator.gemini_translate("Pump", "vi"), "Bơm")
        self.assertIn("/models/gemini-3.5-flash-lite:generateContent", session.calls[1][0])

    def test_auto_model_does_not_retry_on_quota_error(self):
        translator.configure(gemini_model="auto")
        quota = FakeResponse(429, {"error": {"code": 429, "message": "Quota"}})
        session = FakeSession(quota)
        with patch.object(translator, "_session", return_value=session):
            with self.assertRaises(translator.GeminiError):
                translator.gemini_translate("Pump", "vi")
        self.assertEqual(len(session.calls), 1)

    def test_model_labels_round_trip(self):
        self.assertEqual(config.DEFAULTS["gemini_model"], "auto")
        self.assertEqual(config.model_from_label(config.model_label("auto")), "auto")
        self.assertEqual(config.model_from_label(" gemini-3.6-flash "), "gemini-3.6-flash")

    def test_new_key_retries_gemini_immediately(self):
        translator._key_state["test-key"] = {"retry_at": time.monotonic() + 600, "level": 1}
        self.assertFalse(translator.gemini_ready())
        translator.configure(gemini_api_key="another-key")
        self.assertTrue(translator.gemini_ready())

    def test_no_key_uses_google(self):
        translator.configure(gemini_api_key="")
        with patch.object(translator, "_session", side_effect=AssertionError("no Gemini")), \
                patch.object(translator, "_google_cached", side_effect=lambda c, t: (f"google:{c}", "en")):
            self.assertEqual(translator.translate("hello", "vi"), "google:hello")

    def test_check_gemini_messages(self):
        with patch.object(translator, "_session", return_value=FakeSession(gemini_ok("Kiểm tra đồng hồ."))):
            ok, message = translator.check_gemini("k", "gemini-3.5-flash-lite")
        self.assertTrue(ok)
        self.assertIn("Kiểm tra đồng hồ.", message)
        bad = FakeResponse(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})
        with patch.object(translator, "_session", return_value=FakeSession(bad)):
            ok, message = translator.check_gemini("k", "gemini-3.5-flash-lite")
        self.assertFalse(ok)
        self.assertIn("API key not valid", message)


def quota_error():
    return FakeResponse(429, {"error": {"code": 429, "message": "Quota exceeded"}})


class BackupKeyTests(unittest.TestCase):
    def setUp(self):
        translator.clear_gemini_cache()
        translator._google_cached.cache_clear()
        translator.configure(gemini_api_key="main", gemini_backup_keys=["b1", "b2", "main", " "],
                             gemini_model="gemini-3.5-flash-lite")
        self.addCleanup(translator.configure, gemini_api_key="", gemini_backup_keys=[], gemini_model="")

    def keys_used(self, session):
        return [kwargs["headers"]["x-goog-api-key"] for _, kwargs in session.calls]

    def test_keys_are_ordered_and_deduplicated(self):
        self.assertEqual(translator.gemini_keys(),
                         [("key chính", "main"), ("key dự phòng 1", "b1"), ("key dự phòng 2", "b2")])

    def test_quota_on_main_key_switches_to_backup_and_remembers(self):
        session = FakeSession(quota_error(), gemini_ok("Một"), gemini_ok("Hai"))
        with patch.object(translator, "_session", return_value=session):
            self.assertEqual(translator.translate("one", "vi"), "Một")
            self.assertEqual(translator.active_key_label, "key dự phòng 1")
            self.assertEqual(translator.translate("two", "vi"), "Hai")
        self.assertEqual(self.keys_used(session), ["main", "b1", "b1"])  # không thử lại key chính ngay

    def test_all_keys_exhausted_falls_back_to_google(self):
        session = FakeSession(quota_error(), quota_error(), quota_error())
        with patch.object(translator, "_session", return_value=session), \
                patch.object(translator, "_google_cached", side_effect=lambda c, t: (f"google:{c}", "en")):
            self.assertEqual(translator.translate("hello", "vi"), "google:hello")
            self.assertFalse(translator.gemini_ready())
            self.assertEqual(translator.translate("again", "vi"), "google:again")
        self.assertEqual(self.keys_used(session), ["main", "b1", "b2"])

    def test_invalid_key_is_skipped(self):
        bad = FakeResponse(400, {"error": {"code": 400, "message": "API key not valid."}})
        session = FakeSession(bad, gemini_ok("Một"))
        with patch.object(translator, "_session", return_value=session):
            self.assertEqual(translator.translate("one", "vi"), "Một")
        self.assertEqual(self.keys_used(session), ["main", "b1"])

    def test_network_error_does_not_burn_through_backups(self):
        import requests

        class DownSession:
            calls = 0

            def post(self, *args, **kwargs):
                DownSession.calls += 1
                raise requests.ConnectionError("offline")

        with patch.object(translator, "_session", return_value=DownSession()), \
                patch.object(translator, "_google_cached", side_effect=lambda c, t: (f"google:{c}", "en")):
            self.assertEqual(translator.translate("hello", "vi"), "google:hello")
        self.assertEqual(DownSession.calls, 2)  # thử lại ngay một lần với key chính, không dùng key dự phòng
        self.assertFalse(translator.gemini_ready())

    def test_cache_is_shared_between_keys(self):
        session = FakeSession(gemini_ok("Một"))
        with patch.object(translator, "_session", return_value=session):
            translator.translate("one", "vi")
            translator._key_state["main"] = {"retry_at": time.monotonic() + 600, "level": 1}
            self.assertEqual(translator.translate("one", "vi"), "Một")
        self.assertEqual(len(session.calls), 1)

    def test_backup_keys_are_encrypted_on_disk(self):
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        settings = config.Settings(path)
        settings.update(gemini_backup_keys=["AIza-backup-1", "AIza-backup-2"])
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("AIza-backup", raw)
        self.assertEqual(config.Settings(path)["gemini_backup_keys"], ["AIza-backup-1", "AIza-backup-2"])


class SavingQuotaTests(unittest.TestCase):
    def test_double_copy_detection(self):
        from translate_vietnam import app
        self.assertEqual(app.classify_copy("single", "x", 10.0, None, 0.0), "debounce")
        self.assertEqual(app.classify_copy("double", "x", 10.0, None, 0.0), "record")  # lần đầu
        self.assertEqual(app.classify_copy("double", "x", 10.3, "x", 10.0), "translate")  # lần hai
        self.assertEqual(app.classify_copy("double", "y", 10.3, "x", 10.0), "record")  # nội dung khác
        self.assertEqual(app.classify_copy("double", "x", 11.0, "x", 10.0), "record")  # quá chậm
        self.assertEqual(app.classify_copy("double", "x", 10.01, "x", 10.0), "record")  # app tự ghi 2 lần
        self.assertEqual(app.classify_copy("double", None, 10.3, None, 10.0), "record")  # không phải chữ

    def test_quota_day_uses_pacific_time_with_dst(self):
        from datetime import datetime, timezone
        utc = timezone.utc
        # Mùa đông (UTC-8): 07:59 UTC vẫn là ngày hôm trước ở California.
        self.assertEqual(translator.quota_day(datetime(2026, 11, 21, 7, 59, tzinfo=utc)), "2026-11-20")
        self.assertEqual(translator.quota_day(datetime(2026, 11, 21, 8, 0, tzinfo=utc)), "2026-11-21")
        # Mùa hè (UTC-7).
        self.assertEqual(translator.quota_day(datetime(2026, 7, 1, 6, 59, tzinfo=utc)), "2026-06-30")
        self.assertEqual(translator.quota_day(datetime(2026, 7, 1, 7, 0, tzinfo=utc)), "2026-07-01")
        reset = translator.quota_reset_local(datetime(2026, 11, 21, 3, 0, tzinfo=utc))
        self.assertEqual(reset.astimezone(utc), datetime(2026, 11, 21, 8, 0, tzinfo=utc))

    def test_usage_counter_counts_successful_calls_only(self):
        translator.clear_gemini_cache()
        translator.load_gemini_usage("", 0)
        translator.configure(gemini_api_key="k", gemini_model="gemini-3.5-flash-lite")
        self.addCleanup(translator.configure, gemini_api_key="", gemini_model="")
        quota = FakeResponse(429, {"error": {"message": "Quota"}})
        session = FakeSession(gemini_ok("A"), quota)
        with patch.object(translator, "_session", return_value=session):
            translator.gemini_translate("a", "vi")
            with self.assertRaises(translator.GeminiError):
                translator.gemini_translate("b", "vi")
        self.assertEqual(translator.gemini_usage_today(), 1)
        translator.load_gemini_usage("2000-01-01", 99)  # ngày cũ: không tính
        self.assertEqual(translator.gemini_usage_today(), 0)

    def test_long_text_is_one_gemini_call_and_google_fallback_is_split(self):
        translator.clear_gemini_cache()
        translator._google_cached.cache_clear()
        translator.configure(gemini_api_key="k", gemini_model="gemini-3.5-flash-lite")
        self.addCleanup(translator.configure, gemini_api_key="", gemini_model="")
        text = ("Line of text to translate.\n" * 400)  # ~10.800 ký tự
        session = FakeSession(gemini_ok("x"))
        with patch.object(translator, "_session", return_value=session):
            translator.translate(text, "vi")
        self.assertEqual(len(session.calls), 1)
        translator.clear_gemini_cache()
        sizes = []
        with patch.object(translator, "_session",
                          return_value=FakeSession(FakeResponse(503, {"error": {"message": "busy"}}))), \
                patch.object(translator, "_google_cached",
                             side_effect=lambda c, t: (sizes.append(len(c)) or c, "en")):
            self.assertEqual(translator.translate(text, "vi"), text)
        self.assertGreater(len(sizes), 1)
        self.assertTrue(all(size <= translator._MAX_CHUNK for size in sizes))

    def test_trigger_setting_is_validated(self):
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"trigger": "triple", "gemini_usage_count": -5}, f)
        settings = config.Settings(path)
        self.assertEqual(settings["trigger"], "double")
        self.assertEqual(settings["gemini_usage_count"], 0)


class FallbackServiceTests(unittest.TestCase):
    def tearDown(self):
        translator._google_retry_at = 0.0
        translator._google_fail_level = 0
        translator.configure(mymemory_email="")

    def test_google_backoff_escalates_and_resets(self):
        translator._google_retry_at, translator._google_fail_level = 0.0, 0
        translator._google_backoff()
        first = translator._google_retry_at - time.monotonic()
        translator._google_retry_at = 0.0
        translator._google_backoff()
        second = translator._google_retry_at - time.monotonic()
        self.assertAlmostEqual(first, 30, delta=2)
        self.assertAlmostEqual(second, 60, delta=2)

    def test_mymemory_email_is_sent(self):
        translator.configure(mymemory_email="me@example.com")
        self.assertEqual(translator._mymemory_extra(), {"de": "me@example.com"})
        translator.configure(mymemory_email="")
        self.assertEqual(translator._mymemory_extra(), {})


if __name__ == "__main__":
    unittest.main()
