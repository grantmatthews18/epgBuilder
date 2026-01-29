import os
from urllib.parse import quote_plus

import pytest

from app.api import flask as stream_module


def test_missing_stream_url_returns_400():
    client = stream_module.app.test_client()
    resp = client.get('/api/stream')
    assert resp.status_code == 400


def test_stream_endpoint_serves_file_when_start_stream_is_monkeypatched(tmp_path, monkeypatch):
    client = stream_module.app.test_client()

    # Create a fake start_stream that writes a small mp4-like file and marks
    # the provided client_id as active. This avoids needing ffmpeg during tests.
    def fake_start_stream(stream_url, client_id):
        p = tmp_path / f'test_stream_{client_id}.mp4'
        p.write_bytes(b'\x00' * 2048)
        stream_module.STREAM_MANAGER.active_client_id = client_id
        stream_module.STREAM_MANAGER.current_stream_url = stream_url
        stream_module.STREAM_MANAGER.ffmpeg_process = None
        return str(p)

    monkeypatch.setattr(stream_module.STREAM_MANAGER, 'start_stream', fake_start_stream)

    url = quote_plus('http://example.com/some_stream')
    resp = client.get(f'/api/stream?stream_url={url}')
    assert resp.status_code == 200
    assert resp.headers.get('Content-Type') == 'video/mp4'
    data = resp.get_data()
    assert len(data) == 2048
