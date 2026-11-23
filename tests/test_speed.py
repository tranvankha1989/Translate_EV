"""Tăng tốc dịch: kiểm tra Gemini lúc mở app, dùng lại kết nối, nhớ model chạy được."""
import threading
import time
import unittest
from unittest.mock import patch

import requests

from tests.test_features import FakeResponse, gemini_ok
from translate_vietnam import app, translator

FIRST, SECOND = translator.GEMINI_AUTO_MODELS


class RecordingSession:
    """Phiên HTTP giả: get() trả theo tên model, post() trả bản dịch; ghi lại mọi lời gọi."""

    def __init__(self, get_status=None, get_error=None):
        self.get_status = get_status or {}
        self.get_error = get_error
        self.gets, self.posts = [], []

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        if self.get_error:
            raise self.get_error
        name = url.rsplit("/", 1)[-1]
        status = self.get_status.get(name, 200)
        payload = {"error": {"message": "API key not valid. Please pass a valid API key."}} if status == 400 else {}
        return FakeResponse(status, payload)

    def post(self, url, **kwargs):
        self.posts.append(url)
        return gemini_ok("Xin chào")


class PrepareGeminiTests(unittest.TestCase):
    def setUp(self):
        translator.clear_gemini_cache()
        translator.configure(gemini_api_key="", gemini_backup_keys=[], gemini_model="")
        translator.configure(gemini_api_key="k", gemini_model="auto")
        self.addCleanup(translator.configure, gemini_api_key="", gemini_backup_keys=[], gemini_model="")

    def test_startup_check_finds_working_model_and_translation_calls_it_directly(self):
        session = RecordingSession(get_status={FIRST: 404})
        with patch.object(translator, "_session", return_value=session):
            self.assertEqual(translator.prepare_gemini(), SECOND)
            translator.gemini_translate("Hello", "vi")
        self.assertEqual(len(session.posts), 1)  # không phải thử tên model không còn
        self.assertIn(SECOND, session.posts[0])
        self.assertEqual([kwargs["headers"] for _, kwargs in session.gets], [{"x-goog-api-key": "k"}] * 2)

    def test_invalid_key_is_reported_at_startup(self):
        with patch.object(translator, "_session", return_value=RecordingSession(get_status={FIRST: 400})):
            self.assertIsNone(translator.prepare_gemini())
        self.assertFalse(translator.gemini_ready())
        self.assertIn("báo lỗi", translator.last_gemini_error)

    def test_offline_startup_does_not_block_gemini(self):
        error = requests.ConnectionError("offline")
        with patch.object(translator, "_session", return_value=RecordingSession(get_error=error)):
            self.assertIsNone(translator.prepare_gemini())
        self.assertTrue(translator.gemini_ready())

    def test_changing_key_or_model_forgets_found_model(self):
        with patch.object(translator, "_session", return_value=RecordingSession(get_status={FIRST: 404})):
            translator.prepare_gemini()
        self.assertEqual(translator._model_candidates("auto"), (SECOND, FIRST))
        translator.configure(gemini_api_key="k2")
        self.assertEqual(translator._model_candidates("auto"), (FIRST, SECOND))
        self.assertEqual(translator._model_candidates("gemini-3.6-flash"), ("gemini-3.6-flash",))

    def test_keep_warm_sends_no_api_key(self):
        session = RecordingSession()
        with patch.object(translator, "_session", return_value=session):
            translator.keep_warm()
        url, kwargs = session.gets[0]
        self.assertEqual(url, translator.GEMINI_LIST_URL)
        self.assertNotIn("headers", kwargs)


class SharedSessionTests(unittest.TestCase):
    def test_all_threads_share_one_session(self):
        sessions = []
        threads = [threading.Thread(target=lambda: sessions.append(translator._session())) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len({id(s) for s in sessions}), 1)
        self.assertIs(sessions[0], translator._session())


class DurationLogTests(unittest.TestCase):
    def test_log_has_seconds_and_engine_but_no_content(self):
        lines = []
        with patch.object(app, "log", side_effect=lines.append), \
                patch.object(translator, "last_engine", "Gemini gemini-flash-lite-latest"):
            app.log_duration("dịch", "nội dung bí mật", time.monotonic() - 1.31)
        self.assertEqual(len(lines), 1)
        self.assertIn("15 ký tự trong 1,3 giây", lines[0])
        self.assertIn("Gemini gemini-flash-lite-latest", lines[0])
        self.assertNotIn("bí mật", lines[0])


if __name__ == "__main__":
    unittest.main()
