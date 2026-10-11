"""fixture_non_production: scheduling does not certify or append financial facts."""

from copy import deepcopy

import pytest

from services.market_dashboard import share_basis_bootstrap as queue
from services.market_dashboard import share_basis_bootstrap_notes as notes


class ReadOnlyStore:
    def __init__(self, root):
        self.root = root
        self.listed = {"000270", "000001", "000002", "000003", "000004"}
        self.values = {
            "dart-corporations": {"codes": {c: "00106641" if c == "000270" else c
                                            for c in self.listed}},
            "filing-index:000270": {"rows": [{"receipt": b["filing_id"],
                                               "date": b["published_at"]}
                                              for b in notes.BINDINGS]},
            "official:000270": {"xbrl_facts": ["existing_fixture_fact"]},
            "share-basis:000270": {"status": "ready", "revision": "fixture_original"},
        }

    def listed_codes(self):
        return self.listed

    def active(self):
        return []

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "now", lambda: "2026-10-10T00:00:00+00:00")
    return ReadOnlyStore(tmp_path)


def state(store, i, **value):
    store.values["bootstrap-eps-note:" + notes.BINDINGS[i]["filing_id"]] = value


def archive_metadata(store, binding):
    directory = store.root / "raw/dart-bootstrap-eps"
    directory.mkdir(parents=True, exist_ok=True)
    # This is only a scheduling hint, never a source validation receipt.
    for key in ("sha256", "period_sha256"):
        (directory / (binding[key] + ".json")).write_text("{}", encoding="utf-8")


def test_reviewed_issuer_pending_note_gets_one_priority_slot_without_writes(store):
    before = deepcopy(store.values)
    selected = [queue.bootstrap_target(store, {"000270"}, i) for i in range(10)]
    assert selected == ["000270", "000001", "000002", "000003", "000004"] * 2
    assert store.values == before  # no repeated fact registration or share-basis approval


def test_only_reviewed_issuer_advances_second_note_at_nonpriority_cursor(store):
    store.listed = {"000270"}
    assert queue.bootstrap_target(store, {"000270"}, 0) == "000270"
    state(store, 0, status="ready", version=notes.VERSION)
    assert queue.bootstrap_target(store, {"000270"}, 1) == "000270"
    state(store, 1, status="ready", version=notes.VERSION)
    assert queue.bootstrap_target(store, {"000270"}, 2) is None


def test_ready_version_excluded_but_changed_binding_version_is_pending(store):
    for i in range(2):
        state(store, i, status="ready", version=notes.VERSION)
    assert queue.bootstrap_target(store, {"000270"}, 0) != "000270"
    state(store, 0, status="ready", version="fixture_obsolete_version")
    assert queue.bootstrap_target(store, {"000270"}, 0) == "000270"


@pytest.mark.parametrize("retry", ["2026-10-10T09:01:00+09:00", "2099", "bad-date"])
def test_pending_first_note_retry_blocks_later_due_note_even_with_archives(store, retry):
    state(store, 0, status="error", retry_after=retry)
    archive_metadata(store, notes.BINDINGS[0])
    assert queue.bootstrap_target(store, {"000270"}, 0) != "000270"


def test_expired_retry_is_eligible(store):
    state(store, 0, status="error", retry_after="2026-10-10T08:59:00+09:00")
    assert queue.bootstrap_target(store, {"000270"}, 0) == "000270"


def test_network_backoff_blocks_missing_archive_not_pure_local_replay(store, monkeypatch):
    urls = []
    monkeypatch.setattr(queue, "provider_retry_for", lambda _, url: urls.append(url) or
                        {"next_at": "2099-01-01T00:00:00+00:00"})
    assert queue.bootstrap_target(store, {"000270"}, 0) != "000270"
    assert urls == [notes.BINDINGS[0]["url"]]
    urls.clear()
    archive_metadata(store, notes.BINDINGS[0])
    assert queue.bootstrap_target(store, {"000270"}, 0) == "000270"
    assert not urls


def test_missing_period_document_checks_exact_endpoint(store, monkeypatch):
    archive_metadata(store, notes.BINDINGS[0])
    (store.root / "raw/dart-bootstrap-eps" /
     (notes.BINDINGS[0]["period_sha256"] + ".json")).unlink()
    urls = []
    monkeypatch.setattr(queue, "provider_retry_for", lambda _, url: urls.append(url) or {})
    assert queue.bootstrap_target(store, {"000270"}, 0) == "000270"
    assert urls == [notes.BINDINGS[0]["period_url"]]


@pytest.mark.parametrize("change", ["company", "unlisted", "filing", "publication"])
def test_binding_identity_and_publication_scope_remain_required(store, change):
    if change == "company":
        store.values["dart-corporations"]["codes"]["000270"] = "fixture_other_company"
    elif change == "unlisted":
        store.listed.remove("000270")
    elif change == "filing":
        store.values["filing-index:000270"]["rows"][0]["receipt"] = "fixture_other_filing"
    else:
        store.values["filing-index:000270"]["rows"][0]["date"] = "2025-11-15"
    assert queue.bootstrap_target(store, {"000270"}, 0) != "000270"


def test_unreviewed_discovery_rotation_unchanged_when_notes_complete(store):
    for i in range(2):
        state(store, i, status="ready", version=notes.VERSION)
    assert [queue.bootstrap_target(store, {"000270"}, i) for i in range(8)] == [
        "000001", "000002", "000003", "000004"] * 2
