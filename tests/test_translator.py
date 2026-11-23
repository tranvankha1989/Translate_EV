import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from translate_vietnam import translator


class LanguageDetectionTests(unittest.TestCase):
    def test_trusts_service_language(self):
        self.assertEqual(translator.detect_source_language("hello", "vi-VN"), "vi")
        self.assertEqual(translator.detect_source_language("bonjour", "fr"), "fr")

    def test_short_ambiguous_text_defaults_to_english(self):
        for text in ("hello", "thank you", "OK"):
            with self.subTest(text=text):
                self.assertEqual(translator.detect_source_language(text), "en")

    def test_vietnamese_diacritics_override_short_text(self):
        self.assertEqual(translator.detect_source_language("Tôi khỏe."), "vi")

    def test_long_text_uses_confident_local_detection(self):
        with patch.object(translator, "detect_langs", return_value=[MockLanguage("fr", 0.96)]):
            self.assertEqual(
                translator.detect_source_language("Un texte assez long pour détecter la langue."),
                "fr",
            )

    def test_auto_fallback_uses_detected_source_language(self):
        def no_google(chunk, target):
            raise RuntimeError("Google unavailable")

        with patch.object(translator, "_google_cached", side_effect=no_google), patch.object(
            translator, "_mymemory_cached", side_effect=lambda chunk, target, source: f"{source}>{target}"
        ):
            self.assertEqual(
                translator.translate("This is a sufficiently long English sentence.", "auto"),
                "en>vi",
            )
            self.assertEqual(
                translator.translate("Tôi đang học tiếng Anh.", "auto"),
                "vi>en",
            )

    def test_cancelled_translation_does_not_call_fallback(self):
        cancelled = False

        def no_google(chunk, target):
            nonlocal cancelled
            cancelled = True
            raise RuntimeError("request ended after cancellation")

        with patch.object(translator, "_google_cached", side_effect=no_google), patch.object(
            translator, "_mymemory_cached", side_effect=AssertionError("stale fallback")
        ):
            with self.assertRaises(translator.TranslationCancelled):
                translator.translate("hello", "vi", is_cancelled=lambda: cancelled)

    def test_mymemory_fallback_splits_long_url_queries_without_losing_text(self):
        text = "Tôi thích dịch văn bản dài. " * 100
        chunks = list(translator._mymemory_chunks(text, "en", "vi"))
        self.assertEqual("".join(chunks), text)
        for chunk in chunks:
            with self.subTest(length=len(chunk)):
                self.assertLessEqual(len(chunk), translator._MYMEMORY_MAX_CHARS)
                query = urlencode({"q": chunk, "langpair": "en|vi"})
                self.assertLessEqual(len(query), translator._MYMEMORY_MAX_QUERY)

        with patch.object(translator, "_google_cached", side_effect=RuntimeError("offline")), patch.object(
            translator, "_mymemory_cached", side_effect=lambda part, target, source: part
        ) as fallback:
            self.assertEqual(translator._translate_chunk(text, "vi", "en", None), text)
            self.assertEqual(fallback.call_count, len(chunks))


class MockLanguage:
    def __init__(self, lang, probability):
        self.lang = lang
        self.prob = probability


if __name__ == "__main__":
    unittest.main()
