import random

import pytest

from warrini import geometry as geo
from warrini import timeline as tl
from warrini.consts import FPS


def test_fit_image_centres_and_limits_upscale():
    card = geo.fit_image(1920, 1080)
    assert geo.CARD_BOX.contains(card)
    assert card.cx == pytest.approx(geo.CARD_BOX.cx) and card.cy == pytest.approx(geo.CARD_BOX.cy)
    small = geo.fit_image(200, 100)
    assert small.w == pytest.approx(400)  # 2x at most


def test_ease_endpoints_and_monotonic():
    assert geo.ease(0) == 0 and geo.ease(1) == 1 and geo.ease(0.5) == pytest.approx(0.5)
    values = [geo.ease(i / 100) for i in range(101)]
    assert values == sorted(values)


def test_target_view_frames_focus_for_random_boxes():
    rnd = random.Random(7)
    card = geo.fit_image(1920, 1080)
    for _ in range(200):
        w, h = rnd.uniform(0.02, 0.9), rnd.uniform(0.02, 0.9)
        x, y = rnd.uniform(0, 1 - w), rnd.uniform(0, 1 - h)
        focus = geo.to_frame({"x": x, "y": y, "w": w, "h": h}, card)
        view = geo.target_view(focus, card)
        assert view.w / view.h == pytest.approx(16 / 9)
        assert geo.FULL.contains(view)
        zoom = geo.zoom_of(view)
        assert 1.0 - 1e-9 <= zoom <= 2.4 + 1e-9
        if zoom < 2.4 - 1e-9:
            assert view.contains(focus)
        else:
            assert view.x <= focus.cx <= view.x1 and view.y <= focus.cy <= view.y1


def test_target_view_none_is_full_frame():
    assert geo.target_view(None, geo.fit_image(800, 600)) == geo.FULL


def test_lerp_view_keeps_aspect_and_endpoints():
    a = geo.FULL
    b = geo.Rect(400, 200, 800, 450)
    assert geo.lerp_view(a, b, 0) == a and geo.lerp_view(a, b, 1) == b
    mid = geo.lerp_view(a, b, 0.3)
    assert mid.w / mid.h == pytest.approx(16 / 9)


def test_ring_follows_view():
    focus = geo.Rect(900, 500, 120, 80)
    view = geo.Rect(800, 450, 960, 540)  # 2x
    ring = geo.ring_rect(focus, view)
    assert (ring.x, ring.y, ring.w, ring.h) == pytest.approx((190, 90, 260, 180))


def test_pixel_box_rounds_outward():
    assert geo.pixel_box({"x": 0.101, "y": 0.2, "w": 0.3, "h": 0.1}, 1000, 500) == (99, 98, 403, 152)
    assert geo.pixel_box({"x": 0, "y": 0, "w": 1, "h": 1}, 10, 10) == (0, 0, 10, 10)


def _guide(*steps):
    return {"steps": [{"id": f"s{i}", "pause_after": 0.6, "redact": [], **s} for i, s in enumerate(steps)]}


FOCUS = {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}


def test_timeline_whole_frames_and_order():
    guide = _guide({"image": "images/a.png", "focus": FOCUS}, {"image": None})
    segs = tl.build(guide, {"title": 2.234, "s0": 3.01, "s1": 0.2})
    assert [s.kind for s in segs] == ["title", "step", "step", "end"]
    for prev, cur in zip(segs, segs[1:], strict=False):
        assert cur.start == prev.end
    title, s0, s1, end = segs
    assert title.frames == tl.to_frames(0.4 + 2.234 + 0.8)
    assert s0.zoom_out and s0.fade_out
    assert s0.frames == tl.to_frames(0.5 + 3.01 + 0.95)
    assert s1.frames == tl.to_frames(0.5 + 0.7 + 0.6)  # short speech still allows the camera move
    assert end.frames == 2 * FPS and not end.fade_out
    assert tl.total_frames(segs) == end.end


def test_timeline_same_screenshot_continues_without_fade():
    a = {"image": "images/a.png", "focus": FOCUS}
    b = {"image": "images/a.png", "focus": {"x": 0.5, "y": 0.5, "w": 0.2, "h": 0.2}}
    segs = tl.build(_guide(a, b), {})
    assert not segs[1].zoom_out and not segs[1].fade_out
    assert segs[2].continues and segs[2].zoom_out
    c = {**b, "redact": [FOCUS]}  # different covered areas → treated as a new picture
    segs = tl.build(_guide(a, c), {})
    assert segs[1].fade_out and not segs[2].continues


def test_end_card_with_outro_voice():
    segs = tl.build(_guide({"image": None}), {"end": 2.0})
    assert segs[-1].frames == tl.to_frames(0.4 + 2.0 + 0.8) and segs[-1].voice_len == 2.0


def test_estimate_seconds():
    assert tl.estimate_seconds("") == 0 and tl.estimate_seconds("x" * 26) == 2.0
