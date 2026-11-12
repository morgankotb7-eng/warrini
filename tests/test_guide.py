import json

import pytest

from warrini import guide as g


def test_clean_fills_defaults_and_drops_unknown_keys():
    out = g.clean({"title": "  دليل   الـ VPN ", "evil": 1, "steps": [{"text": "افتح", "x": 2}]})
    assert out["title"] == "دليل الـ VPN"
    assert "evil" not in out
    assert out["theme"] == "navy"
    assert out["voice"]["engine"] == "auto" and out["voice"]["allow_online"] is False
    step = out["steps"][0]
    assert g.UUID_RE.match(step["id"])
    assert step == {**step, "image": None, "focus": None, "redact": [], "review": False, "pause_after": 0.6}
    assert out["intro"] is None and out["outro"] is None


@pytest.mark.parametrize(
    ("rect", "expected"),
    [
        ({"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}, {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}),
        ({"x": -1, "y": 0.5, "w": 0.5, "h": 0.9}, {"x": 0.0, "y": 0.5, "w": 0.5, "h": 0.5}),
        ({"x": 0.995, "y": 0, "w": 0.5, "h": 0.5}, None),  # clamps to 0.99 → width 0.01 is too small
        ({"x": 0.1, "y": 0.1, "w": 0.005, "h": 0.5}, None),
        ({"x": "a", "y": 0, "w": 1, "h": 1}, None),
        ({"x": float("nan"), "y": 0, "w": 1, "h": 1}, None),
        ("nope", None),
    ],
)
def test_clean_rect(rect, expected):
    assert g.clean_rect(rect) == expected


def test_image_path_must_be_server_generated():
    good = f"images/{g.new_id()}.png"
    out = g.clean(
        {
            "steps": [
                {"text": "a", "image": good, "focus": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}},
                {"text": "b", "image": "../../etc/passwd", "focus": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}},
            ]
        }
    )
    assert out["steps"][0]["image"] == good
    assert out["steps"][1]["image"] is None and out["steps"][1]["focus"] is None


def test_limits_and_bad_values():
    out = g.clean(
        {"steps": [{"text": "x" * 900, "pause_after": 99, "redact": [{"x": 0, "y": 0, "w": 0.1, "h": 0.1}] * 50}]}
    )
    step = out["steps"][0]
    assert len(step["text"]) == 600 and step["pause_after"] == 3.0 and step["redact"] == []  # no image → no redact
    with pytest.raises(g.GuideError):
        g.clean({"steps": [{"text": "x"}] * 61})
    with pytest.raises(g.GuideError):
        g.clean({"schema": 2})
    assert g.clean({"voice": {"engine": "bad", "rate": "fast", "voice": "x;rm"}})["voice"]["rate"] == "+0%"


def test_duplicate_step_ids_are_reissued():
    sid = g.new_id()
    out = g.clean({"steps": [{"id": sid, "text": "a"}, {"id": sid, "text": "b"}]})
    assert out["steps"][0]["id"] == sid and out["steps"][1]["id"] != sid


def test_check_renderable_names_the_step(tmp_path):
    with pytest.raises(g.GuideError, match="عنوان"):
        g.check_renderable(g.clean({"steps": [{"text": "a"}]}))
    with pytest.raises(g.GuideError, match="مفيهوش"):
        g.check_renderable(g.clean({"title": "t"}))
    with pytest.raises(g.GuideError, match="الخطوة 2"):
        g.check_renderable(g.clean({"title": "t", "steps": [{"text": "a"}, {"text": " "}]}))
    image = f"images/{g.new_id()}.png"
    guide = g.clean({"title": "t", "steps": [{"text": "a", "image": image}]})
    with pytest.raises(g.GuideError, match="صورة الخطوة 1"):
        g.check_renderable(guide, tmp_path)
    guide["steps"][0]["review"] = True
    with pytest.raises(g.GuideError, match="راجع"):
        g.check_renderable(guide)


@pytest.mark.parametrize(
    ("title", "taken", "slug"),
    [
        ("Reset VPN Password", set(), "reset-vpn-password"),
        ("إزاي تغيّر باسورد الـ VPN", set(), "vpn"),
        ("إزاي تغيّر الباسورد", {"guide-1"}, "guide-2"),
        ("Outlook", {"outlook", "outlook-2"}, "outlook-3"),
        ("CON", set(), "guide-1"),
        ("خطوة ٣ من 5", set(), "3-5"),
    ],
)
def test_slug_for(title, taken, slug):
    assert g.slug_for(title, taken) == slug


def test_store_roundtrip_conflict_and_delete(tmp_path):
    store = g.Store(tmp_path)
    slug, guide = store.create("Printer setup")
    assert slug == "printer-setup" and guide["rev"] == 0
    saved = store.save(slug, {**guide, "steps": [{"text": "شغّل الطابعة"}]})
    assert saved["rev"] == 1 and store.load(slug)["steps"][0]["text"] == "شغّل الطابعة"
    with pytest.raises(g.ConflictError) as err:
        store.save(slug, {**guide, "title": "stale"})  # still rev 0
    assert err.value.current["rev"] == 1
    assert store.save(slug, {**guide, "title": "forced"}, force=True)["rev"] == 2
    assert json.loads((tmp_path / slug / "guide.json").read_text("utf-8"))["title"] == "forced"
    assert store.slugs() == [slug]
    store.delete(slug)
    assert store.slugs() == [] and len(list((tmp_path / ".trash").iterdir())) == 1


@pytest.mark.parametrize("slug", ["../x", "a/b", "", ".trash", "con", "A", "x" * 61])
def test_store_rejects_bad_slugs(tmp_path, slug):
    with pytest.raises(g.GuideError):
        g.Store(tmp_path).folder(slug)


def test_parse_steps():
    raw = "1. افتح الـ portal\nودوس Login\n٢) اكتب الباسورد\n- اضغط Save\n• خلصت\n\n10- خطوة عشرة"
    assert g.parse_steps(raw) == [
        "افتح الـ portal ودوس Login",
        "اكتب الباسورد",
        "اضغط Save",
        "خلصت",
        "خطوة عشرة",
    ]
    assert g.parse_steps("نص من غير ترقيم") == ["نص من غير ترقيم"]
    with pytest.raises(g.GuideError):
        g.parse_steps("\n".join(f"{i}. x" for i in range(61)))


def test_natural_key_handles_arabic_digits():
    names = ["shot10.png", "shot2.png", "shot١.png"]
    assert sorted(names, key=g.natural_key) == ["shot١.png", "shot2.png", "shot10.png"]


def test_digit_helpers():
    assert g.to_arabic_digits(12) == "١٢"
    assert g.to_western_digits("٣۴") == "34"
