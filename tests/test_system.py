import json
import os
import sys

from warrini import system


def test_state_dir_and_running_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    system.write_running("http://127.0.0.1:1/launch?code=x", 1)
    data = json.loads(system.running_file().read_text("utf-8"))
    assert data["pid"] == os.getpid() and data["port"] == 1
    assert system.existing_instance() is None  # nothing listens on port 1
    system.clear_running()
    assert not system.running_file().exists()


def test_existing_instance_rejects_foreign_hosts(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    system.running_file().write_text(json.dumps({"url": "http://evil.example:80/launch?code=x"}), "utf-8")
    assert system.existing_instance() is None
    system.running_file().write_text("not json", "utf-8")
    assert system.existing_instance() is None


def test_bundled_ffmpeg_goes_first_on_path(tmp_path, monkeypatch):
    app = tmp_path / "app"
    (app / "python").mkdir(parents=True)
    (app / "ffmpeg").mkdir()
    (app / "ffmpeg" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")).write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(app / "python" / "python"))
    monkeypatch.setenv("PATH", "/usr/bin")
    system.use_bundled_ffmpeg()
    assert os.environ["PATH"].split(os.pathsep)[0] == str((app / "ffmpeg").resolve())


def test_has_libass_handles_missing_binary():
    assert system.has_libass("/nonexistent/ffmpeg") is False
