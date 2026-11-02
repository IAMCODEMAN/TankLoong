import json
import math
import subprocess
import wave
import sys
from pathlib import Path

import pytest

from core import (
    AppError, DEFAULT_SOURCES, FFmpegConverter, MusicDLAdapter, SearchResult,
    build_keyword, create_task_directory, ensure_output_directory, safe_filename,
    search_result_from_record, search_result_to_record, task_manifest_path,
    unique_mp3_path,
)


def test_build_keyword():
    assert build_keyword("  Blue  ", " Artist ") == "Blue Artist"
    assert build_keyword("", "Artist") == "Artist"


def test_default_sources_match_musicdl_cli_defaults():
    from musicdl.musicdl import DEFAULT_MUSIC_SOURCES
    assert tuple(DEFAULT_MUSIC_SOURCES) == DEFAULT_SOURCES


def test_flatten_keeps_every_musicdl_search_result():
    class Song:
        song_name = "Unavailable but visible"
        singers = "Artist"
        album = None
        duration = "01:00"
        file_size = "-"
        root_source = None
        with_valid_download_url = False

    results = MusicDLAdapter._flatten({"QQMusicClient": [Song()]})
    assert len(results) == 1
    assert not results[0].downloadable


def test_safe_filename_handles_windows_characters_and_reserved_names():
    assert safe_filename('a/b:c*?"<>|') == "a_b_c_"
    assert safe_filename("CON") == "audio"


def test_unique_mp3_path_numbers_conflicts_and_keeps_within_limit(tmp_path):
    first = unique_mp3_path(tmp_path, "Song", "Artist")
    first.touch()
    second = unique_mp3_path(tmp_path, "Song", "Artist")
    assert second.name == "Song - Artist (1).mp3"
    long = unique_mp3_path(tmp_path, "x" * 500, "Artist")
    assert len(str(long.resolve())) <= 240


def test_ffmpeg_locate_returns_none_when_nothing_is_available(tmp_path, monkeypatch):
    monkeypatch.setattr("core.shutil.which", lambda name: None)
    monkeypatch.setattr("core.COMMON_FFMPEG_DIRS", ())
    assert FFmpegConverter(project_dir=tmp_path).locate() is None


def test_converter_is_unavailable_without_valid_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr("core.shutil.which", lambda name: None)
    monkeypatch.setattr("core.COMMON_FFMPEG_DIRS", ())
    assert not FFmpegConverter(project_dir=tmp_path).is_available()


def test_configured_path_wins_over_everything(tmp_path, monkeypatch):
    configured = tmp_path / "configured.exe"
    configured.touch()
    monkeypatch.setattr(FFmpegConverter, "validate", staticmethod(lambda path: True))
    assert FFmpegConverter(configured_path=str(configured), project_dir=tmp_path).locate() == configured


def test_system_ffmpeg_is_preferred_over_bundled(tmp_path, monkeypatch):
    """A user-installed FFmpeg on PATH must win over the bundled copy."""
    bundled = tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe"
    bundled.parent.mkdir(parents=True)
    bundled.touch()
    system = tmp_path / "system-ffmpeg.exe"
    system.touch()

    monkeypatch.setattr("core.shutil.which", lambda name: str(system) if name == "ffmpeg" else None)
    monkeypatch.setattr("core.COMMON_FFMPEG_DIRS", ())
    monkeypatch.setattr(FFmpegConverter, "validate", staticmethod(lambda path: Path(path) in {bundled, system}))

    assert FFmpegConverter(project_dir=tmp_path).locate() == system


def test_bundled_ffmpeg_used_when_no_system_copy(tmp_path, monkeypatch):
    bundled = tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe"
    bundled.parent.mkdir(parents=True)
    bundled.touch()

    monkeypatch.setattr("core.shutil.which", lambda name: None)
    monkeypatch.setattr("core.COMMON_FFMPEG_DIRS", ())
    monkeypatch.setattr(FFmpegConverter, "validate", staticmethod(lambda path: Path(path) == bundled))

    assert FFmpegConverter(project_dir=tmp_path).locate() == bundled


def test_system_ffmpeg_without_mp3_encoder_falls_back_to_bundled(tmp_path, monkeypatch):
    """A system FFmpeg lacking libmp3lame must not be selected."""
    bundled = tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe"
    bundled.parent.mkdir(parents=True)
    bundled.touch()
    system = tmp_path / "bad-system-ffmpeg.exe"
    system.touch()

    monkeypatch.setattr("core.shutil.which", lambda name: str(system) if name == "ffmpeg" else None)
    monkeypatch.setattr("core.COMMON_FFMPEG_DIRS", ())
    monkeypatch.setattr(FFmpegConverter, "validate", staticmethod(lambda path: Path(path) == bundled))

    assert FFmpegConverter(project_dir=tmp_path).locate() == bundled


def test_output_directory_failure_mentions_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr("core.shutil.disk_usage", lambda _: type("Usage", (), {"free": 0})())
    with pytest.raises(AppError, match="恢复转换"):
        ensure_output_directory(tmp_path)


def test_task_directory_has_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr("core.TASKS_DIR", tmp_path / "tasks")
    task_dir = create_task_directory()
    assert task_manifest_path(task_dir).is_file()


def test_convert_reports_missing_mp3_encoder_before_starting(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    source.touch()
    destination = tmp_path / "output.mp3"
    converter = FFmpegConverter(project_dir=tmp_path)
    monkeypatch.setattr(converter, "locate", lambda: None)

    with pytest.raises(AppError, match="缺少可用 MP3 编码器"):
        converter.convert(source, destination, lambda _: None, lambda: False)
    assert not destination.exists()


def test_bundled_ffmpeg_converts_wav_to_decodable_mp3(tmp_path):
    converter = FFmpegConverter()
    executable = converter.locate()
    if executable is None:
        pytest.skip("no usable FFmpeg is available")

    source = tmp_path / "tone.wav"
    destination = tmp_path / "tone.mp3"
    sample_rate = 8_000
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        samples = (int(12_000 * math.sin(2 * math.pi * 440 * index / sample_rate)) for index in range(sample_rate // 10))
        output.writeframes(b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples))

    converter.convert(source, destination, lambda _: None, lambda: False)
    assert destination.is_file()
    assert destination.stat().st_size > 0
    check = subprocess.run([str(executable), "-v", "error", "-i", str(destination), "-f", "null", "-"], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr


def test_conversion_drains_noisy_stderr_pipe(tmp_path, monkeypatch):
    """A child filling stderr must not deadlock before its exit can be seen."""
    import threading
    source = tmp_path / 'source.wav'
    source.touch()
    destination = tmp_path / 'output.mp3'
    converter = FFmpegConverter()
    monkeypatch.setattr(converter, 'locate', lambda: Path(sys.executable))
    real_popen = subprocess.Popen
    processes = []
    def noisy_child(command, **kwargs):
        process = real_popen([sys.executable, '-c',
            "import sys,pathlib;sys.stderr.write('diagnostic\\n'*20000);pathlib.Path(sys.argv[1]).write_bytes(b'mp3')",
            str(destination)], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(subprocess, 'Popen', noisy_child)
    errors = []
    def convert():
        try:
            converter.convert(source, destination, lambda _: None, lambda: False)
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=convert, daemon=True)
    worker.start()
    worker.join(timeout=5)
    try:
        assert not worker.is_alive(), 'stderr pipe deadlocked'
        assert not errors
        assert destination.read_bytes() == b'mp3'
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()


# --------------------------------------------------------------------------- #
# Persistence (queue + history)
# --------------------------------------------------------------------------- #

class _Song:
    """Minimal stand-in exposing the musicdl SongInfo serialisation contract."""

    song_name = "T"
    singers = "A"
    identifier = "id-1"
    ext = "m4a"
    source = "QQMusicClient"
    download_url = "http://example/x.m4a"
    download_url_status = {"ok": True}
    downloaded_contents = None
    work_dir = "./"
    _save_path = None

    def todict(self):
        return {
            "song_name": self.song_name, "singers": self.singers,
            "identifier": self.identifier, "ext": self.ext, "source": self.source,
            "download_url": self.download_url,
            "download_url_status": dict(self.download_url_status),
            "downloaded_contents": None, "_save_path": None,
            "work_dir": self.work_dir,
        }


def test_search_result_record_roundtrip():
    item = SearchResult("T", "A", "Alb", "03:00", "4 MB", "QQ", True, _Song())
    record = search_result_to_record(item)

    assert isinstance(record["song"], dict)
    assert record["song"]["downloaded_contents"] is None
    assert record["song"]["_save_path"] is None
    assert json.loads(json.dumps(record))  # JSON-serialisable

    rebuilt = search_result_from_record(record)
    assert rebuilt is not None
    assert rebuilt.title == "T"
    assert rebuilt.source == "QQ"
    assert rebuilt.song_info.identifier == "id-1"


def test_search_result_from_record_rejects_unusable_data():
    assert search_result_from_record({"song": None}) is None
    assert search_result_from_record({}) is None


def test_queue_and_history_roundtrip(monkeypatch, tmp_path):
    import core

    monkeypatch.setattr(core, "QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(core, "HISTORY_FILE", tmp_path / "history.json")

    core.save_queue([{"task_id": "q1", "output_dir": str(tmp_path)}])
    assert core.load_queue() == [{"task_id": "q1", "output_dir": str(tmp_path)}]

    core.append_history({"task_id": "h1", "state": "done"})
    core.append_history({"task_id": "h2", "state": "failed"})
    history = core.load_history()
    assert [entry["task_id"] for entry in history] == ["h2", "h1"], "newest first"

    core.clear_history()
    assert core.load_history() == []


def test_queue_load_tolerates_corrupt_file(monkeypatch, tmp_path):
    import core

    broken = tmp_path / "queue.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(core, "QUEUE_FILE", broken)
    assert core.load_queue() == []


def test_history_is_capped(monkeypatch, tmp_path):
    import core

    monkeypatch.setattr(core, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(core, "HISTORY_LIMIT", 3)
    for index in range(6):
        core.append_history({"task_id": f"h{index}"})
    assert len(core.load_history()) == 3


def test_download_single_rejects_missing_audio(tmp_path, monkeypatch):
    """download_single surfaces a clear error when the provider yields nothing."""
    adapter = MusicDLAdapter(["QQMusicClient"], tmp_path)
    item = SearchResult("T", "A", "Alb", "03:00", "4 MB", "QQ", True, _Song())

    class FakeClient:
        def download(self, items):
            return []

    class FakeMusicClient:
        def __init__(self, *a, **k):
            pass

        def download(self, items):
            return []

    monkeypatch.setattr(MusicDLAdapter, "_client", lambda self: FakeMusicClient())
    with pytest.raises(AppError, match="未返回可用音频"):
        adapter.download_single(item)


def test_diagnostics_never_write_to_the_real_app_data_dir():
    """conftest.py must repoint app data so tests cannot pollute user logs.

    Regression guard for stale "FFmpeg 转换失败：测试错误" records showing up in
    the real %LOCALAPPDATA%\\TankLoong\\logs directory (and therefore in the app's
    日志 / 诊断 panel).
    """
    import os

    import core

    real_app_data = Path(
        os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    ) / "TankLoong"
    assert core.APP_DATA_DIR != real_app_data
    assert real_app_data not in core.LOGS_DIR.parents
    assert real_app_data not in core.TASKS_DIR.parents


def test_append_diagnostic_survives_unwritable_log_dir(tmp_path, monkeypatch):
    """A failed diagnostic write must never mask the real error."""
    import core

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    # LOGS_DIR now lives under a *file*, so mkdir/open raise OSError.
    monkeypatch.setattr(core, "LOGS_DIR", blocker / "logs")
    assert core.append_diagnostic("probe", detail="x") is None
