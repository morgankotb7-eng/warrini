"""Builds the example guide (fictional "NileDesk" portal, made-up data) used for the demo video.

Run: python examples/make_example.py [fonts_dir]
It needs the Inter font (OFL). Pass the folder holding Inter-Regular.otf etc.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent / "vpn-password"
FONTS = Path(sys.argv[1] if len(sys.argv) > 1 else "/usr/share/fonts/opentype/inter")
W, H = 1600, 900
INK, MUTED, LINE, BG = (17, 24, 39), (100, 116, 139), (226, 232, 240), (246, 248, 251)
BLUE, BLUE_D, GREEN = (37, 99, 235), (29, 78, 216), (22, 163, 74)


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"Inter-{weight}.otf"), size)


def shell(active: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 64), fill=(15, 23, 42))
    d.rounded_rectangle((24, 18, 52, 46), radius=7, fill=(250, 204, 21))
    d.text((64, 19), "NileDesk", font=font("Bold", 22), fill="white")
    d.text((176, 23), "Self-Service Portal", font=font("Regular", 17), fill=(148, 163, 184))
    d.rounded_rectangle((W - 330, 15, W - 70, 49), radius=8, fill=(30, 41, 59))
    d.text((W - 314, 22), "Search services…", font=font("Regular", 16), fill=(148, 163, 184))
    d.ellipse((W - 52, 16, W - 20, 48), fill=(71, 85, 105))
    d.text((W - 44, 22), "MA", font=font("SemiBold", 14), fill="white")
    d.rectangle((0, 64, 248, H), fill="white")
    d.line((248, 64, 248, H), fill=LINE, width=1)
    for i, item in enumerate(["Home", "My requests", "VPN Access", "Devices", "Software", "Knowledge base"]):
        y = 96 + i * 52
        if item == active:
            d.rounded_rectangle((14, y - 10, 234, y + 30), radius=8, fill=(239, 246, 255))
            d.rectangle((14, y - 10, 18, y + 30), fill=BLUE)
        d.text(
            (36, y),
            item,
            font=font("Medium" if item == active else "Regular", 17),
            fill=BLUE if item == active else INK,
        )
    return img, d


def card(d: ImageDraw.ImageDraw, box, radius=14):
    d.rounded_rectangle(box, radius=radius, fill="white", outline=LINE, width=1)


def button(d, box, label, primary=True):
    d.rounded_rectangle(box, radius=9, fill=BLUE if primary else "white", outline=None if primary else (203, 213, 225))
    f = font("SemiBold", 17)
    tw = d.textlength(label, font=f)
    d.text(((box[0] + box[2] - tw) / 2, (box[1] + box[3]) / 2 - 11), label, font=f, fill="white" if primary else INK)


def home() -> Image.Image:
    img, d = shell("Home")
    d.text((290, 100), "Good morning, Mona", font=font("Bold", 32), fill=INK)
    d.text((290, 148), "What do you need help with today?", font=font("Regular", 18), fill=MUTED)
    tiles = [
        ("Reset Windows password", "Unlock or change your sign-in", (16, 185, 129)),
        ("VPN Access", "Connect, reset or request VPN", BLUE),
        ("New laptop request", "Order or replace a device", (245, 158, 11)),
        ("Email & Outlook", "Mailbox, signature, rules", (139, 92, 246)),
        ("Printers", "Add a printer or report an issue", (236, 72, 153)),
        ("Software requests", "Install approved applications", (14, 165, 233)),
    ]
    for i, (title, sub, colour) in enumerate(tiles):
        x = 290 + (i % 3) * 425
        y = 210 + (i // 3) * 190
        card(d, (x, y, x + 400, y + 165))
        d.rounded_rectangle((x + 24, y + 24, x + 72, y + 72), radius=12, fill=colour)
        d.text((x + 24, y + 92), title, font=font("SemiBold", 20), fill=INK)
        d.text((x + 24, y + 124), sub, font=font("Regular", 16), fill=MUTED)
    card(d, (290, 600, 1540, 850))
    d.text((318, 626), "Recent requests", font=font("SemiBold", 19), fill=INK)
    for i, (num, title, status) in enumerate(
        [
            ("REQ-20419", "Outlook keeps asking for password", "Resolved"),
            ("REQ-20377", "Monitor for desk 4B", "In progress"),
        ]
    ):
        y = 680 + i * 70
        d.line((318, y - 12, 1512, y - 12), fill=LINE)
        d.text((318, y + 4), num, font=font("Medium", 16), fill=MUTED)
        d.text((470, y + 4), title, font=font("Regular", 17), fill=INK)
        d.rounded_rectangle(
            (1380, y, 1500, y + 32), radius=16, fill=(236, 253, 243) if status == "Resolved" else (255, 247, 230)
        )
        d.text((1396, y + 6), status, font=font("Medium", 14), fill=GREEN if status == "Resolved" else (180, 83, 9))
    return img


def vpn(dialog: str | None = None) -> Image.Image:
    img, d = shell("VPN Access")
    d.text((290, 100), "VPN Access", font=font("Bold", 32), fill=INK)
    d.text((290, 148), "Your remote access account and connection settings", font=font("Regular", 18), fill=MUTED)
    card(d, (290, 200, 1060, 640))
    d.text((318, 226), "Account", font=font("SemiBold", 20), fill=INK)
    rows = [
        ("Username", "mona.adel"),
        ("Email", "mona.adel@niledesk-example.com"),
        ("Employee ID", "EG-104-58821"),
        ("Password last changed", "87 days ago"),
        ("Status", "Expires in 3 days"),
    ]
    for i, (k, v) in enumerate(rows):
        y = 280 + i * 62
        d.text((318, y), k, font=font("Regular", 17), fill=MUTED)
        d.text((560, y), v, font=font("Medium", 17), fill=(180, 83, 9) if k == "Status" else INK)
        d.line((318, y + 44, 1032, y + 44), fill=LINE)
    button(d, (318, 580, 588, 626), "Reset VPN password")
    button(d, (604, 580, 790, 626), "Download client", primary=False)
    card(d, (1090, 200, 1540, 640))
    d.text((1118, 226), "Connection", font=font("SemiBold", 20), fill=INK)
    d.ellipse((1118, 284, 1134, 300), fill=GREEN)
    d.text((1146, 278), "Connected · Cairo HQ", font=font("Medium", 17), fill=INK)
    for i, (k, v) in enumerate([("Gateway", "vpn-cai-02"), ("Protocol", "IKEv2"), ("Session", "2h 14m")]):
        d.text((1118, 340 + i * 50), k, font=font("Regular", 16), fill=MUTED)
        d.text((1290, 340 + i * 50), v, font=font("Medium", 16), fill=INK)
    if dialog is None:
        return img
    shade = Image.new("RGB", (W, H), (15, 23, 42))
    img = Image.blend(img, shade, 0.45)
    d = ImageDraw.Draw(img)
    box = (520, 170, 1180, 760)
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).rounded_rectangle(box, radius=18, fill=140)
    img.paste((0, 0, 0), (0, 0), shadow.filter(ImageFilter.GaussianBlur(24)))
    d.rounded_rectangle(box, radius=18, fill="white")
    d.text((556, 204), "Reset VPN password", font=font("Bold", 26), fill=INK)
    d.text((556, 248), "Use at least 12 characters with a number and a symbol.", font=font("Regular", 16), fill=MUTED)
    for i, label in enumerate(["New password", "Confirm new password"]):
        y = 300 + i * 110
        d.text((556, y), label, font=font("Medium", 16), fill=INK)
        d.rounded_rectangle((556, y + 30, 1144, y + 80), radius=9, fill="white", outline=(203, 213, 225), width=2)
        d.text((574, y + 42), "•" * 14, font=font("Bold", 20), fill=INK)
    d.rounded_rectangle((556, 530, 1144, 538), radius=4, fill=LINE)
    d.rounded_rectangle((556, 530, 1010, 538), radius=4, fill=GREEN)
    d.text((556, 548), "Strong password", font=font("Medium", 15), fill=GREEN)
    if dialog == "form":
        button(d, (556, 670, 760, 718), "Cancel", primary=False)
        button(d, (900, 670, 1144, 718), "Update password")
    else:
        d.rounded_rectangle((556, 610, 1144, 718), radius=12, fill=(236, 253, 243), outline=(134, 239, 172))
        d.ellipse((580, 640, 628, 688), fill=GREEN)
        d.line([(592, 664), (601, 674), (617, 655)], fill="white", width=5)
        d.text((646, 634), "Password updated", font=font("SemiBold", 19), fill=(20, 83, 45))
        d.text(
            (646, 668), "Reconnect the VPN client with your new password.", font=font("Regular", 15), fill=(21, 128, 61)
        )
    return img


def main() -> None:
    images = HERE / "images"
    images.mkdir(parents=True, exist_ok=True)
    for old in images.glob("*.png"):
        old.unlink()
    shots = {"home": home(), "vpn": vpn(), "form": vpn("form"), "done": vpn("done")}
    names = {}
    for key, img in shots.items():
        name = f"{uuid.uuid5(uuid.NAMESPACE_URL, 'warrini-example-' + key)}"
        name = name[:14] + "4" + name[15:19] + "8" + name[20:]  # make it a valid v4-shaped id
        img.save(images / f"{name}.png", optimize=True)
        names[key] = f"images/{name}.png"

    def sid(n: int) -> str:
        raw = str(uuid.uuid5(uuid.NAMESPACE_URL, f"warrini-example-step-{n}"))
        return raw[:14] + "4" + raw[15:19] + "8" + raw[20:]

    guide = {
        "schema": 1,
        "title": "إزاي تغيّر باسورد الـ VPN",
        "intro": "النهارده هنغيّر باسورد الـ VPN في أقل من دقيقة.",
        "outro": "لو قابلتك أي مشكلة، كلّم الـ Service Desk وهنساعدك.",
        "theme": "navy",
        "voice": {
            "engine": "auto",
            "speaker": "Tareq",
            "voice": "ar-EG-ShakirNeural",
            "rate": "+0%",
            "allow_online": False,
        },
        "steps": [
            {
                "id": sid(1),
                "text": "افتح الـ Self-Service Portal من المتصفح، ومن الصفحة الرئيسية دوس على VPN Access.",
                "image": names["home"],
                "focus": {"x": 0.4469, "y": 0.2289, "w": 0.2563, "h": 0.1878},
                "redact": [],
                "pause_after": 0.6,
            },
            {
                "id": sid(2),
                "text": "هتلاقي بيانات الحساب بتاعك. البيانات الشخصية متغطية. دوس على زرار Reset VPN password.",
                "image": names["vpn"],
                "focus": {"x": 0.1950, "y": 0.6378, "w": 0.1750, "h": 0.0622},
                "redact": [
                    {"x": 0.3460, "y": 0.3770, "w": 0.2200, "h": 0.0400},
                    {"x": 0.3460, "y": 0.4460, "w": 0.0950, "h": 0.0400},
                ],
                "pause_after": 0.6,
            },
            {
                "id": sid(3),
                "text": "اكتب الباسورد الجديدة مرتين، ولازم تكون اتناشر حرف على الأقل، وبعدين دوس Update password.",
                "image": names["form"],
                "focus": {"x": 0.5550, "y": 0.7356, "w": 0.1625, "h": 0.0622},
                "redact": [],
                "pause_after": 0.6,
            },
            {
                "id": sid(4),
                "text": "هتظهرلك رسالة إن الباسورد اتغيرت. اقفل الـ VPN وافتحه تاني بالباسورد الجديدة.",
                "image": names["done"],
                "focus": {"x": 0.3425, "y": 0.6700, "w": 0.3775, "h": 0.1300},
                "redact": [],
                "pause_after": 0.8,
            },
        ],
        "rev": 0,
        "updated_at": "2026-11-12T00:00:00.000000Z",
    }
    (HERE / "guide.json").write_text(json.dumps(guide, ensure_ascii=False, indent=2), encoding="utf-8")
    print(HERE)


if __name__ == "__main__":
    main()
