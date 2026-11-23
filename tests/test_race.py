"""Gemini chậm hoặc lỗi tạm thời: gửi thêm yêu cầu song song, lấy kết quả về trước."""
import threading
import time
import unittest
from unittest.mock import patch

from tests.test_features import FakeResponse, gemini_ok
from translate_vietnam import translator

FIRST, SECOND = translator.GEMINI_AUTO_MODELS


class TimedSession:
    """Phiên giả: mỗi model trả lời sau một số giây và một kết quả định trước."""

    def __init__(self, plan):
        self.plan = plan  # tên model -> (số giây chờ, FakeResponse)
        self.calls = []
        self.lock = threading.Lock()

    def post(self, url, **kwargs):
        name = url.split("/models/")[1].split(":")[0]
        with self.lock:
            self.calls.append(name)
        delay, response = self.plan[name]
        time.sleep(delay)
        return response


class RaceTests(unittest.TestCase):
    def setUp(self):
        translator.clear_gemini_cache()
        translator._missing_models.clear()
        translator.configure(gemini_api_key="", gemini_backup_keys=[], gemini_model="")
        translator.configure(gemini_api_key="k", gemini_model="auto")
        translator.resolved_model = FIRST
        self.events = []
        for p in (patch.object(translator, "HEDGE_AFTER", 0.15), patch.object(translator, "GEMINI_DEADLINE", 2.0),
                  patch.object(translator, "on_event", self.events.append)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(translator.configure, gemini_api_key="", gemini_backup_keys=[], gemini_model="")

    def translate(self, plan):
        session = TimedSession(plan)
        started = time.monotonic()
        with patch.object(translator, "_session", return_value=session):
            result = translator._gemini_translate_any("Hello", "vi")
        return result, time.monotonic() - started, session

    def test_fast_answer_sends_one_request(self):
        result, _, session = self.translate({FIRST: (0, gemini_ok("Một")), SECOND: (0, gemini_ok("Hai"))})
        self.assertEqual((result, session.calls), ("Một", [FIRST]))
        self.assertEqual(self.events, [])

    def test_slow_answer_is_beaten_by_parallel_request(self):
        result, seconds, session = self.translate({FIRST: (1.5, gemini_ok("Chậm")), SECOND: (0, gemini_ok("Nhanh"))})
        self.assertEqual(result, "Nhanh")
        self.assertLess(seconds, 1.0)
        self.assertEqual(session.calls, [FIRST, SECOND])
        self.assertIn("song song", translator.last_engine)
        self.assertTrue(any("chưa trả lời" in e for e in self.events))

    def test_server_error_retries_at_once_without_pausing_gemini(self):
        busy = FakeResponse(503, {"error": {"message": "The model is overloaded."}})
        result, seconds, _ = self.translate({FIRST: (0, busy), SECOND: (0, gemini_ok("Được"))})
        self.assertEqual(result, "Được")
        self.assertLess(seconds, 0.15)  # không chờ tới mốc gửi song song
        self.assertTrue(translator.gemini_ready())
        self.assertTrue(any("overloaded" in e for e in self.events))

    def test_missing_backup_model_does_not_stop_gemini(self):
        missing = FakeResponse(404, {"error": {"message": "not found"}})
        result, _, _ = self.translate({FIRST: (0.4, gemini_ok("Một")), SECOND: (0, missing)})
        self.assertEqual(result, "Một")
        self.assertIn(SECOND, translator._missing_models)
        self.assertTrue(translator.gemini_ready())

    def test_quota_error_is_not_raced(self):
        quota = FakeResponse(429, {"error": {"message": "Quota"}})
        with self.assertRaises(translator.GeminiError):
            self.translate({FIRST: (0, quota), SECOND: (0, gemini_ok("x"))})
        self.assertFalse(translator.gemini_ready())  # key hết lượt: chuyển dịch vụ như cũ

    def test_both_too_slow_hits_deadline(self):
        with patch.object(translator, "GEMINI_DEADLINE", 0.4):
            with self.assertRaises(translator.GeminiError):
                self.translate({FIRST: (1.5, gemini_ok("a")), SECOND: (1.5, gemini_ok("b"))})


if __name__ == "__main__":
    unittest.main()
