"""Dịch vùng màn hình (giữ Shift): nhận biết giữ Shift, vị trí khung, dịch ảnh, luồng trong app."""
import base64
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from tests.test_features import FakeResponse, FakeSession, gemini_ok
from translate_vietnam import app, config, keywatch, ocr, popup, region, selection, translator


class UnwrapTests(unittest.TestCase):
    def test_joins_lines_broken_mid_sentence(self):
        text = "Check the ink supply before\nreplacing the printhead. Then run\na nozzle check."
        self.assertEqual(selection.unwrap_lines(text),
                         "Check the ink supply before replacing the printhead. Then run a nozzle check.")

    def test_keeps_paragraphs_lists_and_sentence_ends(self):
        text = "Warning:\n\n- Turn off the power.\n- Wait 5 minutes.\n1) Open the cover\n\n\nDone."
        self.assertEqual(selection.unwrap_lines(text),
                         "Warning:\n\n- Turn off the power.\n- Wait 5 minutes.\n1) Open the cover\n\nDone.")

    def test_hyphenated_word_and_crlf(self):
        self.assertEqual(selection.unwrap_lines("The trans-\r\nlation is done.\r\n"), "The trans-lation is done.")
        self.assertEqual(selection.unwrap_lines("End of sentence.\nlowercase continues"),
                         "End of sentence. lowercase continues")
        self.assertEqual(selection.unwrap_lines("   \n  "), "")


class PlacementTests(unittest.TestCase):
    WORK = (0, 0, 1920, 1040)

    def test_below_region(self):
        self.assertEqual(selection.popup_position((500, 200, 900, 300), (400, 120), self.WORK), (500, 308))

    def test_above_region_when_no_room_below(self):
        self.assertEqual(selection.popup_position((500, 900, 900, 1000), (400, 120), self.WORK), (500, 772))

    def test_bottom_of_screen_when_region_is_tall(self):
        self.assertEqual(selection.popup_position((500, 50, 900, 1000), (400, 120), self.WORK), (500, 916))

    def test_stays_inside_screen_edges(self):
        self.assertEqual(selection.popup_position((1800, 200, 1900, 300), (400, 120), self.WORK)[0], 1516)
        # màn hình phụ bên trái (tọa độ âm)
        self.assertEqual(selection.popup_position((-1000, 100, -600, 200), (400, 120), (-1920, 0, 0, 1040)),
                         (-1000, 208))

    def test_moved_popup_stays_on_screen_when_it_grows(self):
        self.assertEqual(selection.keep_on_screen((300, 400), (400, 120), self.WORK), (300, 400))
        self.assertEqual(selection.keep_on_screen((1700, 980), (400, 300), self.WORK), (1516, 736))
        self.assertEqual(selection.keep_on_screen((-50, -20), (400, 120), self.WORK), (4, 4))

    def test_drag_in_any_direction(self):
        self.assertEqual(region.normalize((10, 50), (5, 20)), (5, 20, 10, 50))

    def test_wrapped_line_count(self):
        measure = len  # mỗi ký tự rộng 1 điểm ảnh
        self.assertEqual(popup.wrapped_line_count("one two three", 100, measure), 1)
        self.assertEqual(popup.wrapped_line_count("aaaa bbbb cccc", 9, measure), 2)
        self.assertEqual(popup.wrapped_line_count("a\n\nb", 10, measure), 3)
        self.assertEqual(popup.wrapped_line_count("x" * 25, 10, measure), 3)


class FakeTimers:
    def __init__(self):
        self.pending = []

    def __call__(self, seconds, fn):
        self.pending.append(fn)

    def fire(self):
        pending, self.pending = self.pending, []
        for fn in pending:
            fn()


class KeyHoldTests(unittest.TestCase):
    SHIFT, C = keywatch.VK_SHIFT, keywatch.VK_C

    def make(self, alone=True, key_name="shift"):
        self.now = 0.0
        self.timers = FakeTimers()
        self.fired, self.watch = [], []
        hold = keywatch.KeyHold(key_name=key_name, start_timer=self.timers, alone=lambda: alone,
                                clock=lambda: self.now)
        hold.enabled = True
        hold.on_hold = lambda: self.fired.append(True)
        hold.watch_mouse = self.watch.append
        return hold

    def test_holding_shift_alone_opens_once(self):
        hold = self.make()
        hold.key(self.SHIFT, True)
        self.now = 0.5
        hold.key(self.SHIFT, True)  # Windows tự lặp phím: không hẹn giờ thêm
        self.assertEqual(len(self.timers.pending), 1)
        hold.mouse(0)  # chỉ di chuột: vẫn mở
        self.timers.fire()
        self.assertEqual(self.fired, [True])
        self.assertEqual(self.watch, [True])  # nghe chuột từ lúc bắt đầu giữ Shift
        hold.key(self.SHIFT, False)
        self.assertEqual(self.watch, [True, False])

    def test_other_key_while_holding_does_not_open(self):
        hold = self.make()
        hold.key(self.SHIFT, True)
        hold.key(self.C, True)  # Shift+C: gõ chữ hoa
        self.timers.fire()
        self.assertEqual(self.fired, [])
        self.assertEqual(self.watch, [True, False])

    def test_mouse_wheel_or_click_while_holding_does_not_open(self):
        for flags in (0x0400, 0x0001):  # Shift+lăn chuột, Shift+bấm chuột trái
            hold = self.make()
            hold.key(self.SHIFT, True)
            hold.mouse(flags)
            self.timers.fire()
            self.assertEqual(self.fired, [])
            self.assertEqual(self.watch, [True, False])

    def test_released_early_or_pressed_again(self):
        hold = self.make()
        hold.key(self.SHIFT, True)
        hold.key(self.SHIFT, False)
        self.timers.fire()
        self.assertEqual(self.fired, [])  # nhả trước khi đủ thời gian
        hold.key(self.SHIFT, True)
        hold.key(self.SHIFT, False)
        self.now = 0.2
        hold.key(self.SHIFT, True)
        self.timers.fire()
        self.assertEqual(self.fired, [True])  # chỉ lần giữ cuối cùng được tính

    def test_missed_key_up_does_not_block_next_hold(self):
        hold = self.make()
        hold.key(self.SHIFT, True)
        self.timers.fire()
        self.now = 5.0
        hold.key(self.SHIFT, True)  # lần nhả trước bị mất (ví dụ màn hình khóa)
        self.timers.fire()
        self.assertEqual(self.fired, [True, True])

    def test_disabled_or_with_shift_does_nothing(self):
        hold = self.make(alone=False)  # đang giữ thêm Shift / nút chuột
        hold.key(self.SHIFT, True)
        self.timers.fire()
        self.assertEqual(self.fired, [])
        hold = self.make()
        hold.enabled = False
        hold.key(self.SHIFT, True)
        self.assertEqual(self.timers.pending, [])

    def test_right_shift_only(self):
        hold = self.make(key_name="rshift")
        hold.key(self.SHIFT, True, right=False)  # Shift trái: không tính
        self.assertEqual(self.timers.pending, [])
        hold.key(self.SHIFT, False, right=False)
        hold.key(self.SHIFT, True, right=True)
        self.timers.fire()
        self.assertEqual(self.fired, [True])

    def test_left_shift_while_holding_right_shift_does_not_open(self):
        hold = self.make(key_name="rshift")
        hold.key(self.SHIFT, True, right=True)
        hold.key(self.SHIFT, True, right=False)
        self.timers.fire()
        self.assertEqual(self.fired, [])

    def test_ctrl_key_ignores_shift(self):
        hold = self.make(key_name="ctrl")
        hold.key(self.SHIFT, True)
        hold.key(self.SHIFT, False)
        self.assertEqual(self.timers.pending, [])
        hold.key(keywatch.VK_CONTROL, True, right=True)  # Ctrl phải cũng là Ctrl
        self.timers.fire()
        self.assertEqual(self.fired, [True])

    def test_key_can_be_changed_while_running(self):
        hold = self.make()
        hold.key_name = "rctrl"
        hold.key(keywatch.VK_CONTROL, True, right=False)
        self.assertEqual(self.timers.pending, [])
        hold.key(keywatch.VK_CONTROL, False, right=False)
        hold.key(keywatch.VK_CONTROL, True, right=True)
        self.timers.fire()
        self.assertEqual(self.fired, [True])

    def test_alone_checks_the_chosen_key(self):
        def alone(name, down):
            with patch.object(keywatch, "_down", side_effect=lambda vk: vk in down):
                return keywatch._key_alone(name)
        k = keywatch
        self.assertTrue(alone("shift", {k.VK_SHIFT, k.VK_RSHIFT}))
        self.assertFalse(alone("shift", {k.VK_SHIFT, k.VK_CONTROL}))  # Ctrl+Shift của bộ gõ
        self.assertTrue(alone("rshift", {k.VK_SHIFT, k.VK_RSHIFT}))
        self.assertFalse(alone("rshift", {k.VK_SHIFT, k.VK_LSHIFT}))
        self.assertFalse(alone("rshift", {k.VK_SHIFT, k.VK_LSHIFT, k.VK_RSHIFT}))
        self.assertTrue(alone("ctrl", {k.VK_CONTROL, k.VK_LCONTROL}))
        self.assertFalse(alone("ctrl", {k.VK_CONTROL, k.VK_MENU}))  # AltGr
        self.assertTrue(alone("rctrl", {k.VK_CONTROL, k.VK_RCONTROL}))
        self.assertFalse(alone("rctrl", {k.VK_CONTROL, k.VK_LCONTROL}))
        self.assertFalse(alone("shift", {k.VK_SHIFT, k.VK_LBUTTON}))


class TranslateImageTests(unittest.TestCase):
    def setUp(self):
        translator.clear_gemini_cache()
        translator._google_cached.cache_clear()
        translator.configure(gemini_api_key="", gemini_backup_keys=[], gemini_model="", gemini_domain="")
        translator.configure(gemini_api_key="test-key", gemini_model="gemini-3.5-flash-lite",
                             gemini_domain="Kỹ thuật máy in công nghiệp")
        self.addCleanup(translator.configure, gemini_api_key="", gemini_backup_keys=[],
                        gemini_model="", gemini_domain="")

    def no_ocr(self, png):
        raise AssertionError("không cần Windows đọc chữ")

    def test_gemini_reads_and_translates_image(self):
        session = FakeSession(gemini_ok("Kiểm tra đầu in."))
        with patch.object(translator, "_session", return_value=session):
            self.assertEqual(translator.translate_image(b"PNGDATA", ocr=self.no_ocr), "Kiểm tra đầu in.")
            self.assertEqual(translator.translate_image(b"PNGDATA", ocr=self.no_ocr), "Kiểm tra đầu in.")
        self.assertEqual(len(session.calls), 1)  # lần hai lấy từ bộ nhớ, không tốn lượt
        body = session.calls[0][1]["json"]
        parts = body["contents"][0]["parts"]
        self.assertEqual(parts[0]["inlineData"], {"mimeType": "image/png",
                                                  "data": base64.b64encode(b"PNGDATA").decode()})
        self.assertEqual(parts[1]["text"], translator.IMAGE_PROMPT)
        instruction = body["systemInstruction"]["parts"][0]["text"]
        self.assertIn("Kỹ thuật máy in công nghiệp", instruction)
        self.assertIn("otherwise translate it into Vietnamese", instruction)

    def test_no_text_in_image(self):
        with patch.object(translator, "_session", return_value=FakeSession(gemini_ok("NO_TEXT"))):
            with self.assertRaises(translator.NoTextFound):
                translator.translate_image(b"blank", ocr=self.no_ocr)

    def test_quota_error_falls_back_to_windows_ocr_and_google(self):
        quota = FakeResponse(429, {"error": {"code": 429, "message": "Quota exceeded"}})
        with patch.object(translator, "_session", return_value=FakeSession(quota)), \
                patch.object(translator, "_google_cached", side_effect=lambda c, t: (f"google:{c}", "en")):
            result = translator.translate_image(b"img", ocr=lambda png: "Check the pump.")
        self.assertEqual(result, "google:Check the pump.")

    def test_without_key_uses_ocr_and_reports_empty(self):
        translator.configure(gemini_api_key="")
        with patch.object(translator, "_session", side_effect=AssertionError("no Gemini")):
            with self.assertRaises(translator.NoTextFound):
                translator.translate_image(b"img", ocr=lambda png: "  ")


class FakePopup:
    def __init__(self):
        self.calls = []

    def open(self, request, anchor, width_hint=None):
        self.calls.append(("open", request, anchor, width_hint))

    def finish(self, request, message, title=""):
        self.calls.append(("finish", request, message, title))

    def close(self):
        self.calls.append(("close",))


class RegionFlowTests(unittest.TestCase):
    BOX = (10, 20, 210, 70)

    def setUp(self):
        self.settings = config.Settings(os.path.join(tempfile.mkdtemp(), "config.json"))
        self.popup = FakePopup()
        self.ui = []
        for p in (
            patch.object(app, "settings", self.settings),
            patch.object(app, "quick_popup", self.popup),
            patch.object(app, "ui_call", side_effect=self.ui.append),
            patch.object(app, "save_gemini_usage"),
            patch.object(app, "log"),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.image = Image.new("RGB", (200, 50), "white")

    def run_ui(self):
        for fn in self.ui:
            fn()
        return [c for c in self.popup.calls if c[0] != "close"]

    def test_translation_shown_below_region(self):
        with patch.object(translator, "translate_image", return_value="Kiểm tra đầu in.") as translate_image:
            app.translate_region(self.BOX, self.image)
        self.assertTrue(translate_image.call_args[0][0].startswith(b"\x89PNG"))
        calls = self.run_ui()
        request = calls[0][1]
        self.assertEqual(calls[0], ("open", request, self.BOX, 200))  # khung rộng theo vùng đã chọn
        self.assertEqual(calls[-1], ("finish", request, "Kiểm tra đầu in.", "Tiếng Việt"))

    def test_windows_ocr_route_is_labelled(self):
        def fake(png, *, ocr, is_cancelled):
            return "Kiểm tra bơm." if ocr(png) == "Check the pump." else "sai"

        with patch.object(translator, "translate_image", side_effect=fake), \
                patch.object(app.ocr, "windows_ocr", return_value="Check the\npump.") as windows_ocr:
            app.translate_region(self.BOX, self.image)
        enlarged = Image.open(io.BytesIO(windows_ocr.call_args[0][0]))
        self.assertEqual(enlarged.size, (400, 100))  # ảnh nhỏ được phóng to cho Windows đọc
        finish = self.run_ui()[-1]
        self.assertEqual(finish[2], "Kiểm tra bơm.")
        self.assertIn("Windows đọc chữ", finish[3])

    def test_errors_are_shown_in_popup(self):
        cases = [
            (translator.NoTextFound(), "Không thấy chữ"),
            (ocr.OcrError("no OCR"), "bộ đọc chữ của Windows"),
            (RuntimeError("offline"), "Không dịch được"),
        ]
        for error, expected in cases:
            self.ui.clear()
            self.popup.calls.clear()
            with patch.object(translator, "translate_image", side_effect=error):
                app.translate_region(self.BOX, self.image)
            self.assertIn(expected, self.run_ui()[-1][2])
        self.ui.clear()
        app.translate_region(self.BOX, None)
        self.assertIn("Không chụp được", self.run_ui()[-1][2])

    def test_large_image_is_shrunk_for_gemini(self):
        png = app.png_bytes(Image.new("RGB", (5000, 100), "white"), max_side=app.REGION_MAX_SIDE)
        self.assertEqual(Image.open(io.BytesIO(png)).size, (2400, 48))

    def test_hold_ctrl_closes_old_popup_and_starts_picker(self):
        started = []

        class Picker:
            def start(self, on_done):
                started.append(on_done)

        with patch.object(app, "region_picker", Picker()):
            app.start_region_pick()
            for fn in self.ui:
                fn()
        self.assertEqual(self.popup.calls, [("close",)])
        self.assertEqual(started, [app.on_region_picked])

    def test_settings_hotkey_and_keyboard_listening(self):
        self.assertTrue(config.DEFAULTS["region_on_hold"])
        self.assertEqual(config.DEFAULTS["hotkey_region"], "")
        self.assertEqual(app.hotkey_bindings()["hotkey_region"], ("", app.start_region_pick))
        self.assertIn("hotkey_region", app.HOTKEY_KEYS)
        self.addCleanup(setattr, keywatch.hold, "enabled", keywatch.hold.enabled)
        with patch.object(app, "listener_hwnd", None):
            app.apply_keywatch()
            self.assertTrue(keywatch.hold.enabled)
            self.settings.update(region_on_hold=False)
            app.apply_keywatch()
            self.assertFalse(keywatch.hold.enabled)

    def test_hold_key_and_time_settings(self):
        self.assertEqual((config.DEFAULTS["region_hold_key"], config.DEFAULTS["region_hold_ms"]), ("shift", 600))
        self.assertEqual(set(config.HOLD_KEYS), set(keywatch.HOLD_KEY_VK))
        for attr in ("enabled", "key_name", "seconds"):
            self.addCleanup(setattr, keywatch.hold, attr, getattr(keywatch.hold, attr))
        with patch.object(app, "listener_hwnd", None):
            app.apply_keywatch()
            self.assertEqual((keywatch.hold.key_name, keywatch.hold.seconds), ("shift", 0.6))
            app.Controller().set_option("region_hold_key", "rctrl")
            app.Controller().set_option("region_hold_ms", 1000)
            self.assertEqual((keywatch.hold.key_name, keywatch.hold.seconds), ("rctrl", 1.0))
        self.assertEqual((self.settings["region_hold_key"], self.settings["region_hold_ms"]), ("rctrl", 1000))

    def test_bad_hold_settings_fall_back_to_default(self):
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"region_hold_key": "alt", "region_hold_ms": 99999}, f)
        s = config.Settings(path)
        self.assertEqual((s["region_hold_key"], s["region_hold_ms"]), ("shift", 600))
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"region_hold_key": "rshift", "region_hold_ms": 1500}, f)
        s = config.Settings(path)
        self.assertEqual((s["region_hold_key"], s["region_hold_ms"]), ("rshift", 1500))


if __name__ == "__main__":
    unittest.main()
