"""Electron's private JSON Lines service. stdout is reserved for protocol frames."""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
import core

from core import (
    APP_DATA_DIR, DEFAULT_SOURCES, AppError, FFmpegConverter, MusicDLAdapter,
    SearchResult, create_task_directory, ensure_output_directory,
    remove_task_directory, sanitize_detail, unique_mp3_path, write_task_manifest,
)


class Service:
    def __init__(self, emit):
        self.emit = emit
        self.directory: Path | None = None
        self.results: dict[str, SearchResult] = {}
        self.lock = threading.RLock()
        self.download_lock = threading.Lock()
        self.search_lock = threading.Lock()
        self.active: set[str] = set()
        self.converter = FFmpegConverter()
        self.encoder: Path | None = None
        self.music_error = ''

    def health(self):
        # Import here, once, before starting any search threads. The platformdirs
        # override is configured by Electron, not by patching upstream files.
        try:
            from musicdl.musicdl import MusicClient  # noqa: F401
        except Exception as error:
            self.music_error = sanitize_detail(error)
        self.encoder = self.converter.locate()
        return {'musicdl': not self.music_error, 'ffmpeg': bool(self.encoder),
                'message': self.music_error or ('' if self.encoder else '未找到可用 FFmpeg MP3 编码器。')}

    def set_directory(self, directory):
        if not isinstance(directory, str) or not Path(directory).is_absolute():
            raise AppError('请选择有效的绝对目录。')
        destination = Path(directory).resolve()
        ensure_output_directory(destination)
        with self.lock:
            if self.active or self.search_lock.locked():
                raise AppError('请等待当前任务结束后再更换目录。')
            self.directory = destination
        return {'directory': str(destination)}

    def search(self, keyword, searchId=None):
        if not self.directory:
            raise AppError('请先选择下载目录。')
        if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 200:
            raise AppError('请输入 1–200 个字符的歌曲名称。')
        if self.music_error:
            raise AppError(f'musicdl 不可用：{self.music_error}')
        if not self.search_lock.acquire(blocking=False):
            raise AppError('搜索正在进行。')
        search_id = searchId if isinstance(searchId, str) and len(searchId) <= 100 else uuid.uuid4().hex
        # Keep only the current view and active downloads. Limit cached objects
        # even after many searches; renderer holds opaque IDs, never SongInfo.
        with self.lock:
            self.results = {key: value for key, value in self.results.items() if key in self.active}
        work = core.APP_DATA_DIR / 'search'
        work.mkdir(parents=True, exist_ok=True)
        pool = ThreadPoolExecutor(max_workers=len(DEFAULT_SOURCES))
        accepted = threading.Event()
        accepted.set()
        gate = threading.Lock()
        diagnostics = []
        all_records = []

        def source_search(source):
            try:
                outcome = MusicDLAdapter([source], work).search_with_diagnostics(keyword.strip())
                records = []
                # The gate ensures no late source can append after completion.
                with gate:
                    if not accepted.is_set():
                        return
                    with self.lock:
                        for item in outcome.results:
                            result_id = uuid.uuid4().hex
                            self.results[result_id] = item
                            records.append({'id': result_id, 'title': item.title, 'artist': item.artist,
                                            'album': item.album, 'duration': item.duration,
                                            'source': item.source, 'downloadable': item.downloadable})
                    diagnostics.extend(d.__dict__ for d in outcome.diagnostics)
                    all_records.extend(records)
                    self.emit({'event': 'search-source', 'searchId': search_id, 'source': source, 'results': records})
            except Exception as error:
                with gate:
                    if accepted.is_set():
                        diagnostics.append({'source': source, 'status': 'error', 'detail': sanitize_detail(error)})

        try:
            futures = {pool.submit(source_search, source): source for source in DEFAULT_SOURCES}
            _, pending = wait(futures, timeout=35)
            with gate:
                accepted.clear()
                for future in pending:
                    diagnostics.append({'source': futures[future], 'status': 'timeout', 'detail': '来源搜索超过 35 秒，已跳过。'})
            return {'searchId': search_id, 'diagnostics': diagnostics, 'results': all_records}
        finally:
            accepted.clear()
            pool.shutdown(wait=False, cancel_futures=True)
            self.search_lock.release()

    def download(self, resultId, downloadId=None):
        with self.lock:
            if not self.directory:
                raise AppError('请先选择下载目录。')
            item = self.results.get(resultId)
            if not item or not item.downloadable:
                raise AppError('该歌曲无法下载，请重新搜索。')
            if resultId in self.active:
                raise AppError('该歌曲已在下载中。')
            destination_dir = self.directory
            self.active.add(resultId)

        def status(state, **extra):
            self.emit({'event': 'download', 'resultId': resultId, 'downloadId': downloadId, 'state': state, **extra})

        task_dir = None
        try:
            status('queued')
            # First version deliberately serializes downloads and filename
            # allocation. Concurrent requests cannot overwrite the same MP3.
            with self.download_lock:
                ensure_output_directory(destination_dir)
                if not self.encoder:
                    self.encoder = self.converter.locate()
                if not self.encoder:
                    raise AppError('缺少 FFmpeg MP3 编码器，请重新安装完整软件包。')
                task_dir = create_task_directory()
                write_task_manifest(task_dir, stage='downloading', title=item.title)
                status('downloading')
                downloaded = MusicDLAdapter(DEFAULT_SOURCES, task_dir).download_single(item)
                source = Path(downloaded.song_info.save_path)
                destination = unique_mp3_path(destination_dir, item.title, item.artist)
                write_task_manifest(task_dir, stage='converting', source_paths=[str(source)], output_paths=[str(destination)])
                status('converting')
                self.converter.convert(source, destination, lambda _: None, lambda: False)
                remove_task_directory(task_dir)
                status('done', outputPath=str(destination))
                return {'outputPath': str(destination)}
        except Exception as error:
            if task_dir:
                write_task_manifest(task_dir, stage='failed', error=sanitize_detail(error))
            status('failed', message=sanitize_detail(error))
            raise
        finally:
            with self.lock:
                self.active.discard(resultId)


def main():
    # Frozen Python may ignore PYTHONIOENCODING. Explicit UTF-8 is mandatory
    # on Windows or JSON keywords such as Chinese song names become mojibake.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    protocol = sys.stdout
    sys.stdout = sys.stderr  # musicdl/Rich/print must never corrupt JSON Lines.
    lock = threading.Lock()

    def send(message):
        with lock:
            protocol.write(json.dumps(message, ensure_ascii=False) + '\n')
            protocol.flush()

    service = Service(send)
    methods = {'health': service.health, 'set_directory': service.set_directory,
               'search': service.search, 'download': service.download}
    workers = ThreadPoolExecutor(max_workers=8)

    def dispatch(request):
        request_id = request.get('id')
        try:
            method = methods.get(request.get('method'))
            if not method:
                raise AppError('未知的服务请求。')
            result = method(**request.get('params', {}))
            send({'id': request_id, 'result': result})
        except Exception as error:
            send({'id': request_id, 'error': sanitize_detail(error)})

    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError('request must be an object')
                workers.submit(dispatch, request)
            except (ValueError, TypeError):
                send({'id': None, 'error': '请求格式无效。'})
    finally:
        # On parent disconnect, kill running source threads/downloads too.
        service.converter.cancel()
        workers.shutdown(wait=False, cancel_futures=True)
        os._exit(0)


if __name__ == '__main__':
    main()
