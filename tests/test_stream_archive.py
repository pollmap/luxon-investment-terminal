"""fixture_non_production: exact stream bytes with existing raw-v1 readers."""
import hashlib

import pytest

from services.market_dashboard.raw_archive import read_original, read_original_verified
from services.market_dashboard.store import Store


def test_stream_original_is_one_durable_envelope_and_both_readers_agree(tmp_path):
    store = Store('sqlite://', tmp_path)
    body = b'{"topic":"trade:kr:005930","data":{"price":"100"}}\n'
    digest = store.raw('toss-stream', 'trade:kr:005930', body)
    directory = tmp_path / 'raw' / 'toss-stream'
    assert digest == hashlib.sha256(body).hexdigest()
    assert sorted(p.name for p in directory.iterdir()) == [digest + '.json']
    assert read_original(directory, digest) == read_original_verified(directory, digest) == body
    before = (directory / (digest + '.json')).read_bytes()
    store.raw('toss-stream', 'trade:kr:005930', body)
    assert (directory / (digest + '.json')).read_bytes() == before


def test_existing_stream_binary_is_preserved_with_legacy_receipt(tmp_path):
    store = Store('sqlite://', tmp_path)
    body = b'{"topic":"orderbook:kr:005930"}'
    directory = tmp_path / 'raw' / 'toss-stream'
    directory.mkdir(parents=True)
    digest = hashlib.sha256(body).hexdigest()
    original = directory / (digest + '.bin')
    original.write_bytes(body)
    store.raw('toss-stream', 'orderbook:kr:005930', body)
    assert original.read_bytes() == read_original_verified(directory, digest) == body


def test_failed_durable_publication_does_not_leave_a_successful_frame(tmp_path, monkeypatch):
    store = Store('sqlite://', tmp_path)
    body = b'{"topic":"trade:kr:005930"}'
    def unavailable(*_):
        raise OSError('fixture disk full')
    monkeypatch.setattr('services.market_dashboard.raw_archive.atomic_create', unavailable)
    with pytest.raises(OSError):
        store.raw('toss-stream', 'trade:kr:005930', body)
    assert not list((tmp_path / 'raw' / 'toss-stream').iterdir())
