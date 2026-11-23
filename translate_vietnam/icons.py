"""Icon khay hệ thống (A / V / E / - và S khi đang sửa chính tả) và icon của app."""
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

MODE_COLOR = {"off": (120, 120, 120), "auto": (110, 68, 166), "vi": (200, 40, 40), "en": (40, 90, 200),
              "fix": (214, 110, 0)}
MODE_LETTER = {"off": "-", "auto": "A", "vi": "V", "en": "E", "fix": "S"}
PROGRESS_COLOR = (18, 125, 69)
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


@lru_cache(maxsize=8)
def _font(size):
    try:
        return ImageFont.truetype("arialbd.ttf", size)
    except OSError:
        return ImageFont.load_default()


@lru_cache(maxsize=256)
def tray_icon(mode, progress):
    """Icon khay: nền theo chế độ, phần trên tô xanh theo tiến độ dịch (0–100)."""
    img = Image.new("RGB", (64, 64), MODE_COLOR[mode])
    d = ImageDraw.Draw(img)
    rows = min(63, round(progress * 64 / 100)) if progress < 100 else 64
    if rows:
        d.rectangle((0, 0, 63, rows - 1), fill=PROGRESS_COLOR)
    d.text((32, 34), MODE_LETTER[mode], fill="white", font=_font(52), anchor="mm")
    return img


def app_icon(size=256):
    """Icon của app (file exe, cửa sổ Bảng điều khiển)."""
    big = size * 4  # vẽ to rồi thu nhỏ cho mịn
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 5, fill=MODE_COLOR["auto"] + (255,))
    d.text((big / 2, big / 2), "VE", fill="white", font=_font(int(big * 0.42)), anchor="mm")
    return img.resize((size, size), Image.LANCZOS)


def save_app_icon(path):
    app_icon(256).save(path, format="ICO", sizes=ICO_SIZES)


if __name__ == "__main__":
    import sys

    save_app_icon(sys.argv[1] if len(sys.argv) > 1 else "icon.ico")
