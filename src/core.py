"""Core services for the MusicDL MP3 desktop application."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional


# Frozen builds unpack to sys._MEIPASS; in a source checkout the bundled assets
# and ffmpeg/ live one level above this src/ package.
PROJECT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
APP_DATA_DIR = Path(os.environ["TANKLOONG_DATA_DIR"]) if os.environ.get("TANKLOONG_DATA_DIR") else Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "TankLoong"
TASKS_DIR = APP_DATA_DIR / "tasks"
LOGS_DIR = APP_DATA_DIR / "logs"
# Keep this in lockstep with musicdl.musicdl.DEFAULT_MUSIC_SOURCES (v2.13.6).
# The GUI must search the same default services as the upstream CLI.
DEFAULT_SOURCES = (
    "MiguMusicClient",
    "NeteaseMusicClient",
    "QQMusicClient",
    "KuwoMusicClient",
    "QianqianMusicClient",
)
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))
}
# Common locations for a user-installed FFmpeg that is NOT on PATH. Checked after
# shutil.which("ffmpeg") and before the bundled copy, so an existing system FFmpeg
# (with libmp3lame) wins over the bundled one without ever "installing" anything.
COMMON_FFMPEG_DIRS = (
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    r"C:\Program Files (x86)\ffmpeg\bin",
    str(Path.home() / "scoop" / "apps" / "ffmpeg" / "current" / "bin"),
    str(Path.home() / "scoop" / "shims"),
)


class AppError(RuntimeError):
    """An error that can be shown directly to the user."""


class CancelledError(AppError):
    pass


@dataclass
class SearchResult:
    title: str
    artist: str
    album: str
    duration: str
    size: str
    source: str
    downloadable: bool
    song_info: object


@dataclass
class ConversionResult:
    output_path: Path
    source_path: Path


@dataclass
class SourceDiagnostic:
    source: str
    status: str
    detail: str
    result_count: int = 0


@dataclass
class SearchOutcome:
    results: list[SearchResult]
    diagnostics: list[SourceDiagnostic]


@dataclass
class DownloadOutcome:
    results: list[SearchResult]
    diagnostics: list[SourceDiagnostic]


def build_keyword(title: str, artist: str) -> str:
    return " ".join(part.strip() for part in (title, artist) if part and part.strip())


def safe_filename(value: str, fallback: str = "audio", max_length: int = 140) -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", (value or "").strip())
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value or value.upper() in WINDOWS_RESERVED:
        value = fallback
    return value[:max_length].rstrip(" .") or fallback


def unique_mp3_path(output_dir: Path, title: str, artist: str) -> Path:
    ensure_output_directory(output_dir)
    stem = safe_filename(f"{title} - {artist}" if artist else title)
    suffix = ".mp3"
    max_stem_length = max(1, 240 - len(str(output_dir.resolve())) - len(suffix) - 1)
    stem = stem[:max_stem_length].rstrip(" .") or "audio"
    candidate = output_dir / f"{stem}{suffix}"
    index = 1
    while candidate.exists():
        numbered = f"{stem} ({index})"
        candidate = output_dir / f"{numbered[:max_stem_length].rstrip(' .')}{suffix}"
        index += 1
    return candidate


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def task_manifest_path(task_dir: Path) -> Path:
    return Path(task_dir) / "task.json"


def write_task_manifest(task_dir: Path, **updates: object) -> None:
    path = task_manifest_path(task_dir)
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        current = {}
    current.update(updates)
    current["updated_at"] = _utc_now()
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def append_diagnostic(event: str, **fields: object) -> Path | None:
    """Append one JSONL diagnostic record.

    Diagnostics are best-effort: a read-only or missing log directory must never
    mask the real error a caller is reporting, so failures are swallowed and
    None is returned.
    """
    path = LOGS_DIR / f"diagnostic-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    record = {"timestamp": _utc_now(), "event": event, **fields}
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return path


def sanitize_detail(error: object) -> str:
    """Keep diagnostics useful without persisting request tokens or cookies."""
    detail = str(error)
    detail = re.sub(r"([?&](?:token|key|cookie|authorization|signature)=[^&\s]+)", r"\1=<redacted>", detail, flags=re.IGNORECASE)
    detail = re.sub(r"(?i)(cookie|authorization)\s*[:=]\s*[^,\n]+", r"\1=<redacted>", detail)
    return detail[:1_000]


def ensure_output_directory(output_dir: Path, minimum_free_bytes: int = 5 * 1024 * 1024) -> None:
    """Validate the user-selected target without ever falling back to another drive."""
    output_dir = Path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        if not output_dir.is_dir():
            raise OSError("目标不是目录")
        probe = output_dir / f".tankloong-write-test-{uuid.uuid4().hex}"
        probe.touch(exist_ok=False)
        probe.unlink(missing_ok=True)
        if shutil.disk_usage(output_dir).free < minimum_free_bytes:
            raise OSError("可用空间不足 5 MB")
    except OSError as error:
        raise AppError(f"保存目录不可用：{output_dir}（{error}）。请重新插入 U 盘后使用“恢复转换”。") from error


class MusicDLAdapter:
    """Small boundary around musicdl's non-interactive Python API."""

    def __init__(self, sources: Iterable[str], work_dir: Path):
        self.sources = list(sources) or list(DEFAULT_SOURCES)
        self.work_dir = Path(work_dir)

    def _client(self):
        try:
            from musicdl.musicdl import MusicClient
        except ImportError as error:
            raise AppError("未安装 musicdl，请先执行 pip install -r requirements.txt。") from error
        configs = {source: {"work_dir": str(self.work_dir), "max_retries": 1} for source in self.sources}
        return MusicClient(music_sources=self.sources, init_music_clients_cfg=configs,
                           requests_overrides={source: {"timeout": (5, 12)} for source in self.sources})

    @staticmethod
    def _flatten(results: dict) -> list[SearchResult]:
        flattened: list[SearchResult] = []
        for source, items in results.items():
            for item in items or []:
                flattened.append(SearchResult(
                    title=str(getattr(item, "song_name", "") or "未知歌曲"),
                    artist=str(getattr(item, "singers", "") or "未知歌手"),
                    album=str(getattr(item, "album", "") or "-"),
                    duration=str(getattr(item, "duration", "") or "-"),
                    size=str(getattr(item, "file_size", "") or "-"),
                    source=str(getattr(item, "root_source", None) or source).replace("MusicClient", ""),
                    downloadable=bool(getattr(item, "with_valid_download_url", False)),
                    song_info=item,
                ))
        return flattened

    def search_with_diagnostics(self, keyword: str) -> SearchOutcome:
        if not keyword:
            raise AppError("请至少输入歌曲名称或歌手/关键词。")
        results: list[SearchResult] = []
        diagnostics: list[SourceDiagnostic] = []
        # Isolate sources so a transient provider failure never contaminates the
        # next source or the next user task.
        for source in self.sources:
            try:
                source_results = self._flatten(MusicDLAdapter([source], self.work_dir)._client().search(keyword))
                results.extend(source_results)
                diagnostics.append(SourceDiagnostic(source, "ok" if source_results else "empty", "返回 0 条结果" if not source_results else "搜索成功", len(source_results)))
            except Exception as error:
                diagnostics.append(SourceDiagnostic(source, "error", sanitize_detail(error)))
        append_diagnostic("search", sources=self.sources, diagnostics=[diagnostic.__dict__ for diagnostic in diagnostics])
        return SearchOutcome(results, diagnostics)

    def search(self, keyword: str) -> list[SearchResult]:
        return self.search_with_diagnostics(keyword).results

    def parse_playlist(self, url: str) -> list[SearchResult]:
        if not url.lower().startswith(("http://", "https://")):
            raise AppError("歌单 URL 必须以 http:// 或 https:// 开头。")
        try:
            return self._flatten({"playlist": self._client().parseplaylist(url)})
        except Exception as error:
            raise AppError(f"歌单解析失败：{error}") from error

    def download_with_diagnostics(self, selected: list[SearchResult]) -> DownloadOutcome:
        if not selected:
            raise AppError("请先在搜索结果中至少选择一首歌曲。")
        successful: list[SearchResult] = []
        diagnostics: list[SourceDiagnostic] = []
        grouped: dict[str, list[SearchResult]] = {}
        for item in selected:
            grouped.setdefault(str(getattr(item.song_info, "source", "") or item.source), []).append(item)
        for source, items in grouped.items():
            try:
                for item in items:
                    item.song_info.work_dir = str(self.work_dir)
                    item.song_info._save_path = None
                downloaded = MusicDLAdapter([source], self.work_dir)._client().download([item.song_info for item in items])
                downloaded_by_id = {str(item.identifier): item for item in downloaded if getattr(item, "identifier", None) and getattr(item, "save_path", None) and Path(item.save_path).is_file()}
                source_successes = [SearchResult(item.title, item.artist, item.album, item.duration, item.size, item.source, item.downloadable, downloaded_by_id[str(getattr(item.song_info, "identifier", None))]) for item in items if str(getattr(item.song_info, "identifier", None)) in downloaded_by_id]
                successful.extend(source_successes)
                diagnostics.append(SourceDiagnostic(source, "ok" if source_successes else "empty", "下载器未返回可用音频" if not source_successes else "下载成功", len(source_successes)))
            except Exception as error:
                diagnostics.append(SourceDiagnostic(source, "error", sanitize_detail(error)))
        append_diagnostic("download", sources=list(grouped), diagnostics=[diagnostic.__dict__ for diagnostic in diagnostics])
        return DownloadOutcome(successful, diagnostics)

    def download(self, selected: list[SearchResult]) -> list[SearchResult]:
        return self.download_with_diagnostics(selected).results

    def download_single(self, item: SearchResult) -> SearchResult:
        """Download exactly one song and return it with a populated save_path.

        The background queue schedules one song per task so that progress,
        retries and cancellation stay isolated per track.
        """
        song_info = item.song_info
        source = str(getattr(song_info, "source", "") or item.source)
        song_info.work_dir = str(self.work_dir)
        song_info._save_path = None
        try:
            downloaded = MusicDLAdapter([source], self.work_dir)._client().download([song_info])
        except Exception as error:
            raise AppError(f"下载失败：{sanitize_detail(error)}") from error
        identifier = str(getattr(song_info, "identifier", None))
        for candidate in downloaded or []:
            if str(getattr(candidate, "identifier", None)) != identifier:
                continue
            save_path = getattr(candidate, "save_path", None)
            if save_path and Path(save_path).is_file():
                return SearchResult(item.title, item.artist, item.album, item.duration, item.size, item.source, item.downloadable, candidate)
        raise AppError("下载器未返回可用音频。")


class FFmpegConverter:
    def __init__(self, configured_path: str = "", project_dir: Path = PROJECT_DIR):
        self.configured_path = configured_path.strip()
        self.project_dir = Path(project_dir)
        self.process: Optional[subprocess.Popen] = None

    def locate(self) -> Optional[Path]:
        # Order: explicit configured path -> system PATH -> common install dirs ->
        # bundled copy. Every candidate must validate (i.e. expose libmp3lame), so a
        # system FFmpeg without MP3 encoding is skipped in favour of the bundled one.
        candidates: list[Path] = []
        if self.configured_path:
            candidates.append(Path(self.configured_path))
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            candidates.append(Path(system_ffmpeg))
        candidates.extend(Path(common_dir) / "ffmpeg.exe" for common_dir in COMMON_FFMPEG_DIRS)
        candidates.extend([
            self.project_dir / "ffmpeg" / "bin" / "ffmpeg.exe",
            self.project_dir / "ffmpeg" / "ffmpeg.exe",
        ])
        for candidate in candidates:
            if candidate.is_file() and self.validate(candidate):
                return candidate
        return None

    def is_available(self) -> bool:
        """Return whether a usable FFmpeg with MP3 encoding is available."""
        return self.locate() is not None

    @staticmethod
    def validate(path: Path) -> bool:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            version = subprocess.run([str(path), "-version"], capture_output=True, text=True, timeout=10, creationflags=flags)
            if version.returncode != 0:
                return False
            encoders = subprocess.run([str(path), "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10, creationflags=flags)
            return encoders.returncode == 0 and "libmp3lame" in f"{encoders.stdout}\n{encoders.stderr}"
        except (OSError, subprocess.SubprocessError):
            return False

    def verify(self) -> bool:
        """Exercise the exact bundled process and encoder before a user task."""
        executable = self.locate()
        if not executable:
            return False
        probe_dir = APP_DATA_DIR / "ffmpeg-check"
        probe_dir.mkdir(parents=True, exist_ok=True)
        output = probe_dir / f"{uuid.uuid4().hex}.mp3"
        try:
            run = subprocess.run([str(executable), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.05", "-codec:a", "libmp3lame", str(output)], capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ok = run.returncode == 0 and output.is_file() and output.stat().st_size > 0
            append_diagnostic("ffmpeg_check", executable=str(executable), ok=ok, detail=run.stderr.strip()[-500:])
            return ok
        except (OSError, subprocess.SubprocessError) as error:
            append_diagnostic("ffmpeg_check", executable=str(executable), ok=False, detail=str(error))
            return False
        finally:
            output.unlink(missing_ok=True)

    def convert(self, source: Path, destination: Path, status: Callable[[str], None], is_cancelled: Callable[[], bool]) -> ConversionResult:
        executable = self.locate()
        if not source.is_file():
            raise AppError(f"找不到下载的临时音频：{source}")
        if not executable:
            raise AppError("软件包缺少可用 MP3 编码器，请重新安装完整的 TankLoong 软件包。")
        ensure_output_directory(destination.parent)
        command = [str(executable), "-nostdin", "-v", "error", "-y", "-i", str(source), "-vn", "-codec:a", "libmp3lame", "-q:a", "2", str(destination)]
        status(f"正在转换：{source.name}")
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            # communicate drains stderr while waiting. poll()+read() deadlocks
            # when a noisy source fills the Windows pipe before FFmpeg exits.
            while True:
                try:
                    _, stderr = self.process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if is_cancelled():
                        self.process.terminate()
                        self.process.communicate(timeout=5)
                        destination.unlink(missing_ok=True)
                        raise CancelledError("任务已取消。")
            if self.process.returncode != 0 or not destination.is_file():
                destination.unlink(missing_ok=True)
                detail = stderr.strip().splitlines()[-1] if stderr.strip() else "FFmpeg 未生成 MP3 文件。"
                raise AppError(f"FFmpeg 转换失败：{detail}")
            return ConversionResult(destination, source)
        except OSError as error:
            raise AppError(f"无法启动 FFmpeg：{error}") from error
        finally:
            self.process = None

    def cancel(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()


def create_task_directory() -> Path:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    task_dir = TASKS_DIR / f"task-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    task_dir.mkdir()
    write_task_manifest(task_dir, task_id=task_dir.name, created_at=_utc_now(), stage="created", output_paths=[], source_paths=[])
    return task_dir


def remove_task_directory(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def recoverable_tasks() -> list[Path]:
    if not TASKS_DIR.is_dir():
        return []
    tasks: list[Path] = []
    for task_dir in TASKS_DIR.iterdir():
        manifest = task_manifest_path(task_dir)
        if task_dir.is_dir() and manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if data.get("stage") in {"downloaded", "converting", "failed"} and any(Path(path).is_file() for path in data.get("source_paths", [])):
                    tasks.append(task_dir)
            except (OSError, json.JSONDecodeError):
                continue
    return sorted(tasks, reverse=True)


# --------------------------------------------------------------------------- #
# Persistent download queue and history
#
# queue.json   -> tasks that have not finished yet, restored on the next launch
# history.json -> finished tasks (done / failed / cancelled), newest first
#
# SongInfo exposes todict()/fromdict(), so a queued task can be rebuilt after a
# restart. A provider download URL may have expired by then; that task simply
# fails and the user can retry from the download list.
# --------------------------------------------------------------------------- #

QUEUE_FILE = APP_DATA_DIR / "queue.json"
HISTORY_FILE = APP_DATA_DIR / "history.json"
HISTORY_LIMIT = 500


def _load_record_list(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save_record_list(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def search_result_to_record(item: SearchResult) -> dict:
    """Serialise a search result so it can be rebuilt after a restart."""
    song_info = getattr(item, "song_info", None)
    song: dict | None = None
    if song_info is not None and hasattr(song_info, "todict"):
        try:
            song = song_info.todict()
            # Never persist downloaded payloads or transient local paths.
            song["downloaded_contents"] = None
            song["_save_path"] = None
        except Exception:
            song = None
    return {
        "title": item.title,
        "artist": item.artist,
        "album": item.album,
        "duration": item.duration,
        "size": item.size,
        "source": item.source,
        "downloadable": item.downloadable,
        "song": song,
    }


def search_result_from_record(record: dict) -> SearchResult | None:
    """Rebuild a SearchResult from a persisted record; None when unusable."""
    song_data = record.get("song")
    if not isinstance(song_data, dict):
        return None
    try:
        from musicdl.modules.utils.data import SongInfo

        song_info = SongInfo.fromdict(song_data)
    except Exception:
        return None
    return SearchResult(
        str(record.get("title") or "未知歌曲"),
        str(record.get("artist") or "未知歌手"),
        str(record.get("album") or "-"),
        str(record.get("duration") or "-"),
        str(record.get("size") or "-"),
        str(record.get("source") or ""),
        bool(record.get("downloadable", True)),
        song_info,
    )


def load_queue() -> list[dict]:
    return _load_record_list(QUEUE_FILE)


def save_queue(records: list[dict]) -> None:
    try:
        _save_record_list(QUEUE_FILE, records)
    except OSError:
        # Persistence must never break an active download.
        pass


def load_history() -> list[dict]:
    return _load_record_list(HISTORY_FILE)


def append_history(record: dict) -> None:
    try:
        records = load_history()
        records.insert(0, record)
        _save_record_list(HISTORY_FILE, records[:HISTORY_LIMIT])
    except OSError:
        pass


def clear_history() -> None:
    try:
        _save_record_list(HISTORY_FILE, [])
    except OSError:
        pass
