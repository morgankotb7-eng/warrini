import http.client
import io
import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest
from PIL import Image

from warrini import narrate, server


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("WARRINI_VOICE", "fake")
    port = server.free_port(0)
    app = server.App(tmp_path / "root", port, narrate.Narrator(cache_dir=tmp_path / "cache"))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), server.make_handler(app))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield app, port
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, headers=None, raw=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    hdrs = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=hdrs)
    res = conn.getresponse()
    payload = res.read()
    conn.close()
    return res.status, dict(res.getheaders()), payload


def auth(app):
    return {"Cookie": f"{server.COOKIE}={app.secret}", "X-Warrini-Token": app.secret}


def png_bytes(size=(320, 180), colour=(200, 220, 240)):
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


def test_launch_sets_cookie_and_page_needs_it(live):
    app, port = live
    status, _, body = call(port, "GET", "/")
    assert status == 403 and "افتح ورّيني".encode() in body
    status, _, _ = call(port, "GET", "/launch?code=wrong")
    assert status == 403
    status, headers, _ = call(port, "GET", f"/launch?code={app.launch_code}")
    assert status == 303 and headers["Location"] == "/"
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and app.secret in cookie
    status, headers, body = call(port, "GET", "/", headers={"Cookie": cookie.split(";")[0]})
    assert status == 200 and app.secret.encode() in body
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_host_header_blocks_dns_rebinding(live):
    app, port = live
    status, _, _ = call(port, "GET", "/api/status", headers=auth(app), host="evil.example:80")
    assert status == 403


def test_reads_need_cookie_writes_need_token_and_origin(live):
    app, port = live
    assert call(port, "GET", "/api/guides")[0] == 403
    cookie_only = {"Cookie": f"{server.COOKIE}={app.secret}"}
    assert call(port, "GET", "/api/guides", headers=cookie_only)[0] == 200
    assert call(port, "POST", "/api/guides", {"title": "x"}, headers=cookie_only)[0] == 403
    evil = {**auth(app), "Origin": "http://evil.example"}
    assert call(port, "POST", "/api/guides", {"title": "x"}, headers=evil)[0] == 403
    good = {**auth(app), "Origin": f"http://127.0.0.1:{port}"}
    assert call(port, "POST", "/api/guides", {"title": "x"}, headers=good)[0] == 201


def test_guide_crud_and_conflict(live):
    app, port = live
    status, _, body = call(port, "POST", "/api/guides", {"title": "Outlook signature"}, headers=auth(app))
    created = json.loads(body)
    slug, guide = created["slug"], created["guide"]
    assert status == 201 and slug == "outlook-signature"
    guide["steps"] = [{"text": "افتح Outlook"}]
    status, _, body = call(port, "PUT", f"/api/guides/{slug}", {"guide": guide}, headers=auth(app))
    saved = json.loads(body)["guide"]
    assert status == 200 and saved["rev"] == 1
    status, _, body = call(port, "PUT", f"/api/guides/{slug}", {"guide": guide}, headers=auth(app))
    assert status == 409 and json.loads(body)["current"]["rev"] == 1
    status, _, body = call(port, "GET", "/api/guides", headers=auth(app))
    listed = json.loads(body)["guides"]
    assert [g["slug"] for g in listed] == [slug] and listed[0]["steps"] == 1
    assert call(port, "DELETE", f"/api/guides/{slug}", headers=auth(app))[0] == 200
    assert call(port, "GET", f"/api/guides/{slug}", headers=auth(app))[0] == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/guides/..%2f..%2fetc",
        "/files/x/images/../../guide.json",
        "/files/x/images/.secret",
        "/files/x/other/a.png",
        "/audio/../../x.wav",
        "/static/../server.py",
        "/static/fonts/../../cli.py",
    ],
)
def test_traversal_is_not_found(live, path):
    app, port = live
    call(port, "POST", "/api/guides", {"title": "x"}, headers=auth(app))
    assert call(port, "GET", path, headers=auth(app))[0] in (403, 404, 422)


def test_image_upload_validation(live):
    app, port = live
    slug = json.loads(call(port, "POST", "/api/guides", {"title": "img"}, headers=auth(app))[2])["slug"]
    url = f"/api/guides/{slug}/images"
    status, _, body = call(port, "PUT", url, headers=auth(app), raw=png_bytes())
    info = json.loads(body)
    assert status == 201 and info["width"] == 320 and info["image"].startswith("images/")
    status, headers, data = call(port, "GET", f"/files/{slug}/{info['image']}", headers=auth(app))
    assert status == 200 and headers["Content-Type"] == "image/png" and data[:4] == b"\x89PNG"
    assert call(port, "PUT", url, headers=auth(app), raw=b"<svg onload=alert(1)>")[0] == 415
    assert call(port, "PUT", url, headers=auth(app), raw=b"")[0] == 422
    status, _, _ = call(port, "PUT", url, headers={**auth(app), "Content-Length": str(30 * 1024 * 1024)}, raw=b"x")
    assert status == 413


def test_bad_length_and_json(live):
    app, port = live
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest("POST", "/api/guides", skip_host=True)
    conn.putheader("Host", f"127.0.0.1:{port}")
    for k, v in auth(app).items():
        conn.putheader(k, v)
    conn.putheader("Content-Length", "abc")
    conn.endheaders()
    assert conn.getresponse().status == 400
    conn.close()
    assert call(port, "POST", "/api/guides", headers=auth(app), raw=b"{nope")[0] == 400
    assert call(port, "POST", "/api/guides", headers=auth(app), raw=b"[1,2]")[0] == 400


def test_range_requests(live, tmp_path):
    app, port = live
    slug = json.loads(call(port, "POST", "/api/guides", {"title": "vid"}, headers=auth(app))[2])["slug"]
    out = app.store.folder(slug) / "out" / "vid-r1.mp4"
    out.write_bytes(bytes(range(256)) * 4)
    path = f"/files/{slug}/out/vid-r1.mp4"
    status, headers, body = call(port, "GET", path, headers={**auth(app), "Range": "bytes=10-19"})
    assert status == 206 and body == bytes(range(10, 20)) and headers["Content-Range"] == "bytes 10-19/1024"
    status, _, body = call(port, "GET", path, headers={**auth(app), "Range": "bytes=-4"})
    assert status == 206 and body == bytes([252, 253, 254, 255])
    status, headers, _ = call(port, "GET", path, headers={**auth(app), "Range": "bytes=5000-"})
    assert status == 416 and headers["Content-Range"] == "bytes */1024"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("bytes=0-0", (0, 0)),
        ("bytes=5-", (5, 99)),
        ("bytes=-10", (90, 99)),
        ("bytes=90-500", (90, 99)),
        ("bytes=100-", None),
        ("bytes=5-2", None),
        ("bytes=-0", None),
        ("bytes=1-2,4-5", None),
        ("items=1-2", None),
    ],
)
def test_parse_range(header, expected):
    assert server.parse_range(header, 100) == expected


def test_voice_preview_and_render_job(live):
    app, port = live
    slug = json.loads(call(port, "POST", "/api/guides", {"title": "Render me"}, headers=auth(app))[2])["slug"]
    image = json.loads(call(port, "PUT", f"/api/guides/{slug}/images", headers=auth(app), raw=png_bytes((640, 360)))[2])
    guide = json.loads(call(port, "GET", f"/api/guides/{slug}", headers=auth(app))[2])["guide"]
    guide["steps"] = [
        {"text": "دوس هنا", "image": image["image"], "focus": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}},
        {"text": "خلصنا"},
    ]
    call(port, "PUT", f"/api/guides/{slug}", {"guide": guide}, headers=auth(app))
    status, _, body = call(port, "POST", f"/api/guides/{slug}/voice", {"text": "اهلا بيك"}, headers=auth(app))
    preview = json.loads(body)
    assert status == 200 and preview["engine"] == "fake"
    status, headers, data = call(port, "GET", preview["url"], headers=auth(app))
    assert status == 200 and data[:4] == b"RIFF"
    status, _, body = call(port, "POST", f"/api/guides/{slug}/render", {"quality": "fast"}, headers=auth(app))
    assert status == 202
    assert call(port, "POST", f"/api/guides/{slug}/render", {}, headers=auth(app))[0] == 409  # one at a time
    for _ in range(600):
        job = json.loads(call(port, "GET", "/api/job", headers=auth(app))[2])
        if job["state"] != "running":
            break
        time.sleep(0.1)
    if not app.status()["libass"]:
        assert job["state"] == "error"
        return
    assert job["state"] == "done", job
    status, headers, data = call(port, "GET", job["video"], headers={**auth(app), "Range": "bytes=0-11"})
    assert status == 206 and data[4:8] == b"ftyp"


def test_render_refuses_incomplete_guide(live):
    app, port = live
    slug = json.loads(call(port, "POST", "/api/guides", {"title": "Empty"}, headers=auth(app))[2])["slug"]
    status, _, body = call(port, "POST", f"/api/guides/{slug}/render", {}, headers=auth(app))
    assert status == 422 and "خطوات" in json.loads(body)["error"]


def test_import_text(live):
    app, port = live
    status, _, body = call(port, "POST", "/api/import/text", {"text": "1. افتح\n2. اقفل"}, headers=auth(app))
    assert status == 200 and json.loads(body)["steps"] == ["افتح", "اقفل"]
