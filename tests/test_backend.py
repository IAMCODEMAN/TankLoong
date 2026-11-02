import math
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
import backend
from core import AppError, MusicDLAdapter, SearchResult, SearchOutcome


def song(title='测试音频', downloadable=True):
    return SearchResult(title, 'TankLoong', '测试', '00:01', '-', 'QQ', downloadable, SimpleNamespace())


def test_search_requires_explicit_directory():
    with pytest.raises(AppError, match='先选择'):
        backend.Service(lambda _: None).search('test')


def test_parallel_sources_stream_results_and_isolate_errors(tmp_path, monkeypatch):
    events = []
    service = backend.Service(events.append)
    service.set_directory(str(tmp_path))
    def search(adapter, keyword):
        if adapter.sources[0] == 'QQMusicClient':
            raise RuntimeError('provider offline')
        return SearchOutcome([song(keyword)], [])
    monkeypatch.setattr(MusicDLAdapter, 'search_with_diagnostics', search)
    result = service.search('青花瓷')
    assert len(events) == 4
    assert len(service.results) == 4
    assert all(event['results'][0]['title'] == '青花瓷' for event in events)
    assert any(d['source'] == 'QQMusicClient' and d['status'] == 'error' for d in result['diagnostics'])
    assert 'song_info' not in events[0]['results'][0]


def test_full_download_pipeline_writes_real_mp3_without_overwrite(tmp_path, monkeypatch):
    """Only provider I/O is substituted; the actual FFmpeg encoder runs twice."""
    service = backend.Service(lambda _: None)
    if not service.converter.locate():
        pytest.skip('FFmpeg unavailable')
    destination = tmp_path / '用户选择的目录'
    service.set_directory(str(destination))
    service.results['one'] = song()
    def download(adapter, item):
        source = adapter.work_dir / '音频.wav'
        with wave.open(str(source), 'wb') as output:
            output.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            output.writeframes(b''.join(int(8000 * math.sin(i / 8000 * 440 * 2 * math.pi)).to_bytes(2, 'little', signed=True) for i in range(800)))
        item.song_info.save_path = str(source)
        return item
    monkeypatch.setattr(MusicDLAdapter, 'download_single', download)
    first = Path(service.download('one')['outputPath'])
    original = first.read_bytes()
    second = Path(service.download('one')['outputPath'])
    assert first != second
    assert first.read_bytes() == original
    assert first.parent == destination
    assert first.stat().st_size > 0 and second.stat().st_size > 0
    assert not service.active


def test_download_failure_emits_failure_and_clears_active(tmp_path, monkeypatch):
    events = []
    service = backend.Service(events.append)
    service.set_directory(str(tmp_path))
    service.results['one'] = song()
    service.encoder = Path('already-validated')
    def fail(*args):
        raise AppError('网络中断')
    monkeypatch.setattr(MusicDLAdapter, 'download_single', fail)
    with pytest.raises(AppError, match='网络中断'):
        service.download('one', downloadId='test-attempt')
    assert events[-1]['state'] == 'failed'
    assert events[-1]['downloadId'] == 'test-attempt'
    assert not service.active


def test_unavailable_or_unknown_song_cannot_download(tmp_path):
    service = backend.Service(lambda _: None)
    service.set_directory(str(tmp_path))
    service.results['locked'] = song(downloadable=False)
    for result_id in ['locked', 'unknown']:
        with pytest.raises(AppError, match='无法下载'):
            service.download(result_id)
