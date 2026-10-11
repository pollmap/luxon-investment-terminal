"""Pure fixture_non_production cases. No PostgreSQL/SQLite/network/Store creation."""

import hashlib
import io
import json
import os
import tarfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import pytest

from ops.market import review_samsung_comparative as m

IFRS = "{https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full}"
# Deliberately authored parser fixture, not a captured third-party document.
NOTE = """<!-- fixture_non_production -->
<table><tr><td>전반기</td></tr></table><table>
<tr><th></th><th></th><th>보통주</th><th>우선주</th></tr>
<tr><th></th><th></th><th>3개월</th><th>누적</th><th>3개월</th><th>누적</th></tr>
<tr><td>가중평균유통보통주식수(단위: 주)</td>
<td>5,881,153,521</td><td>5,903,847,141</td><td></td><td></td></tr></table>""".encode()


def fixture_trace(**fields):
    return {
        "fixture_non_production": True,
        "source": "fixture_non_production",
        "source_url": "https://example.invalid/fixture_non_production",
        "sha256": "f" * 64,
        **fields,
    }


def fixture_fact(concept, value, **fields):
    shares = concept == "WeightedAverageShares"
    return {
        "fixture_non_production": True,
        "fact_id": m.FACT_ID if shares else "fixture_non_production:ocf",
        "company_id": m.COMPANY,
        "account_id": IFRS + concept,
        "basis": "CFS",
        "filing_id": m.FILING,
        "period_start": m.START,
        "period_end": m.END,
        "published_at": "20260814",
        "unit": m.SHARES if shares else "{http://www.xbrl.org/2003/iso4217}KRW",
        "currency": None if shares else "KRW",
        "dimensions": {
            IFRS + "ConsolidatedAndSeparateFinancialStatementsAxis": IFRS + "ConsolidatedMember",
            **(
                {IFRS + "ClassesOfShareCapitalAxis": IFRS + "OrdinarySharesMember"}
                if shares
                else {}
            ),
        },
        "value": value,
        "source_trace": fixture_trace(sha256=m.XBRL_HASH),
        **fields,
    }


@pytest.fixture
def inputs():
    """Small manufactured data; the production complete-review gate rejects it."""
    basis = {
        "fixture_non_production": True,
        "verified": True,
        "complete_split_history": True,
        "security_code": m.CODE,
        "company_id": m.COMPANY,
        "basis_id": "fixture_non_production:share-units",
        "start": "2016-01-01",
        "end": "2026-10-08",
        "basis_date": "2026-10-08",
        "source_trace": fixture_trace(),
        "splits": [
            {
                "verified": True,
                "kind": "split",
                "effective_date": "2018-05-04",
                "old_shares": "1",
                "new_shares": "50",
                "source_trace": fixture_trace(),
            }
        ],
        "unchanged_intervals": [
            {
                "verified": True,
                "no_split_interval": True,
                "security_code": m.CODE,
                "company_id": m.COMPANY,
                "start": "2019-01-01",
                "end": m.UNIT_DAY,
                "source_trace": fixture_trace(),
            }
        ],
        "fact_unit_reviews": [
            {
                "verified": True,
                "filing_id": m.FILING,
                "basis": "CFS",
                "period_start": "2026-01-01",
                "period_end": m.UNIT_DAY,
                "unit_as_of": m.UNIT_DAY,
                "source_trace": fixture_trace(),
            }
        ],
    }
    shares = fixture_fact("WeightedAverageShares", "5903847141")
    quarter = fixture_fact(
        "WeightedAverageShares",
        "5881153521",
        period_start="2025-04-01",
        fact_id="fixture_non_production:quarter",
    )
    official = {
        "fixture_non_production": True,
        "xbrl_facts": [
            shares,
            quarter,
            fixture_fact("CashFlowsFromUsedInOperatingActivities", "30000000000000"),
        ],
    }
    evidence = {
        "fixture_non_production": True,
        "shares": "5903847141",
        "source_url": "https://example.invalid/fixture_non_production",
        "document_hashes": m.PINNED.copy(),
    }
    return basis, official, evidence


@pytest.fixture
def mock_complete_plan(monkeypatch):
    """Exercise transactions with a fake approval; never unlabel fixture inputs.

    The real complete-review rejection is tested separately. Pure calculations
    still run with complete=False; this mock alone supplies approval to FakeDB.
    """
    original = m.plan

    def mocked(*args, **kwargs):
        kwargs["complete"] = False
        updated, report = original(*args, **kwargs)
        if not report["target_ocf_ttm_ready"]:
            raise ValueError("Fixture TTM calculation missing")
        return updated, {**report, "apply_ready": True, "fixture_non_production": True}

    monkeypatch.setattr(m, "plan", mocked)


@pytest.fixture
def fixture_archive(tmp_path, monkeypatch):
    """Synthetic archive exercises checks without requiring commercial captures."""
    raw = {
        "005930-6.html": b"fixture_non_production:6",
        "005930-7.html": b"fixture_non_production:7",
        "005930-48.html": NOTE,
    }
    pins = {name: hashlib.sha256(content).hexdigest() for name, content in raw.items()}
    monkeypatch.setattr(m, "PINNED", pins)
    manifest = [
        {
            "file": name,
            "code": m.CODE,
            "sha256": pins[name],
            "title": "23. 주당이익 (연결)"
            if name.endswith("48.html")
            else "fixture_non_production",
            "url": "https://dart.fss.or.kr/report/viewer.do?rcpNo=" + m.FILING,
        }
        for name in raw
    ]
    raw["sources.json"] = json.dumps(manifest, ensure_ascii=False).encode()
    path = tmp_path / "fixture_non_production.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, content in raw.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return path


def fixture_complete(basis, selected):
    """Entirely manufactured periods for algorithms, never operational proof."""
    share = next(f for f in selected["xbrl_facts"] if f["fact_id"] == m.FACT_ID)
    ocf = next(
        f
        for f in selected["xbrl_facts"]
        if m.local(f["account_id"]) == "CashFlowsFromUsedInOperatingActivities"
    )
    facts = []
    for start, end, filing, published, amount in (
        ("2025-01-01", "2025-03-31", "20250515000001", "2025-05-15", "20000000000000"),
        ("2025-01-01", "2025-06-30", "20250814003156", "2025-08-14", ocf["value"]),
        ("2025-01-01", "2025-09-30", "20251114000001", "2025-11-14", "50000000000000"),
        ("2025-01-01", "2025-12-31", "20260317000001", "2026-03-17", "70000000000000"),
        ("2026-01-01", "2026-03-31", "20260515000001", "2026-05-15", "25000000000000"),
        ("2026-01-01", "2026-06-30", m.FILING, "2026-08-14", "40000000000000"),
    ):
        for template, value in ((share, share["value"]), (ocf, amount)):
            facts.append(
                {
                    **deepcopy(template),
                    "period_start": start,
                    "period_end": end,
                    "filing_id": filing,
                    "published_at": published,
                    "value": value,
                    "fact_id": f"fixture_non_production:{template['account_id']}:{end}:{filing}",
                    "period_verified": True,
                    "fixture_non_production": True,
                    "source_trace": {"source": "fixture_non_production", "sha256": "f" * 64},
                }
            )
    facts.extend({**deepcopy(f), "period_verified": True} for f in (share, ocf))
    return {"xbrl_facts": facts, "fixture_non_production": True}


class FakeDatabase:
    def __init__(self, basis, official):
        self.rows = {m.BASIS_KEY: self.wrap(basis), m.OFFICIAL_KEY: self.wrap(official)}
        self.writes, self.commits = [], 0
        self.fail_cas = False

    @staticmethod
    def wrap(value, revision="2026-10-09T22:31:30.000000+00:00"):
        return {"payload": deepcopy(value), "raw": m.encode(value), "revision": revision}

    @contextmanager
    def transaction(self, write=False):
        before, count = deepcopy(self.rows), len(self.writes)
        self.write_allowed = write
        try:
            yield self
        except Exception:
            self.rows = before
            self.writes = self.writes[:count]
            raise
        else:
            self.commits += bool(write)

    def get(self, key, lock=None):
        return deepcopy(self.rows.get(key))

    def immutable(self, key, payload, stamp):
        assert self.write_allowed and key not in (m.BASIS_KEY, m.OFFICIAL_KEY)
        if key in self.rows and self.rows[key]["payload"] != payload:
            raise ValueError("Immutable review/history collision")
        self.rows.setdefault(key, self.wrap(payload, stamp))
        self.writes.append(key)

    def cas_basis(self, old, updated, stamp):
        assert self.write_allowed
        if self.fail_cas or self.rows[m.BASIS_KEY] != old:
            raise ValueError("Concurrent basis update")
        revision = m.next_snapshot_revision(old["revision"], stamp)
        self.rows[m.BASIS_KEY] = self.wrap(updated, revision)
        self.writes.append(m.BASIS_KEY)
        return revision


def apply(db, evidence, dry):
    return m.run(
        db,
        evidence,
        apply=True,
        reviewer="fixture_non_production",
        expected_basis=dry["basis_sha256"],
        expected_official=dry["official_sha256"],
        expected_review=dry["review_sha256"],
        basis_writer_paused=True,
    )


def test_synthetic_plan_only_changes_one_unit_context(inputs):
    basis, official, evidence = inputs
    original = deepcopy((basis, official))
    updated, result = m.plan(basis, official, evidence)
    assert evidence["shares"] == "5903847141"
    assert result["normalized_before"] is False and result["normalized_after"] is True
    assert result["factor_after"] == "1" and not result["apply_ready"]
    assert not result["complete_official_snapshot"] and not result["target_ocf_ttm_ready"]
    assert (basis, official) == original
    assert len(updated["fact_unit_reviews"]) == len(basis["fact_unit_reviews"]) + 1
    assert updated["basis_id"] == basis["basis_id"]
    assert {k: v for k, v in updated.items() if k != "fact_unit_reviews"} == {
        k: v for k, v in basis.items() if k != "fact_unit_reviews"
    }


def test_note_rejects_swapped_preferred_column_and_missing_cumulative_label():
    raw = NOTE
    for source in (
        raw.replace(b"5,903,847,141", b"813,433,928"),
        raw.replace("누적".encode(), "다른기간".encode()),
    ):
        with pytest.raises(ValueError):
            m.parse_note(source)


@pytest.mark.parametrize(
    "change",
    [
        {"company_id": "different"},
        {"basis": "OFS"},
        {"period_start": "2025-04-01"},
        {"unit": "천주"},
        {"value": "5903847.141"},
        {"published_at": "2026-06-30"},
        {"fact_id": "other"},
        {"source_trace": {"sha256": "0" * 64}},
        {"dimensions": {"ClassesOfShareCapitalAxis": "PreferredSharesMember"}},
    ],
)
def test_mismatched_source_cannot_receive_review(inputs, change):
    basis, official, evidence = inputs
    next(f for f in official["xbrl_facts"] if f["fact_id"] == m.FACT_ID).update(change)
    with pytest.raises(ValueError):
        m.plan(basis, official, evidence)


def test_no_review_expansion_to_preferred_or_other_quarter(inputs):
    basis, official, evidence = inputs
    updated, _ = m.plan(basis, official, evidence)
    fact = next(f for f in official["xbrl_facts"] if f["fact_id"] == m.FACT_ID)
    preferred = {**fact, "dimensions": {"ClassesOfShareCapitalAxis": "PreferredSharesMember"}}
    assert m.normalize_fact(preferred, updated) == preferred
    quarter = next(f for f in official["xbrl_facts"] if f["period_start"] == "2025-04-01")
    assert m.normalize_fact(quarter, updated) == quarter


def test_duplicate_or_conflicting_review_blocks(inputs):
    basis, official, evidence = inputs
    review = m.proposed_review(evidence)
    basis["fact_unit_reviews"].extend([review, deepcopy(review)])
    with pytest.raises(ValueError, match="Duplicate"):
        m.plan(basis, official, evidence)
    basis["fact_unit_reviews"].pop()
    basis["fact_unit_reviews"][-1]["unit_as_of"] = "2025-06-30"
    with pytest.raises(ValueError, match="differs"):
        m.plan(basis, official, evidence)


def test_fixture_full_dry_run_and_atomic_idempotent_apply(inputs, mock_complete_plan):
    basis, selected, evidence = inputs
    official = fixture_complete(basis, selected)
    db = FakeDatabase(basis, official)
    original = deepcopy(db.rows[m.OFFICIAL_KEY])
    dry = m.run(db, evidence)
    assert dry["apply_ready"] and dry["target_ocf_ttm_ready"] and not db.writes
    result = apply(db, evidence, dry)
    assert result["applied"] and not result["idempotent"]
    assert len(db.writes) == 3 and m.OFFICIAL_KEY not in db.writes
    assert db.rows[m.OFFICIAL_KEY] == original
    history = db.rows["share-basis-review-history:005930:" + dry["basis_sha256"]]["payload"]
    assert history["payload"] == basis
    assert db.rows[m.BASIS_KEY]["revision"] > history["source_revision"]
    result2 = apply(db, evidence, dry)
    assert result2["idempotent"] and len(db.writes) == 3
    assert sum(m.target(r) for r in db.rows[m.BASIS_KEY]["payload"]["fact_unit_reviews"]) == 1


def test_fake_apply_rollback_preserves_history_review_and_source(inputs, mock_complete_plan):
    basis, selected, evidence = inputs
    db = FakeDatabase(basis, fixture_complete(basis, selected))
    original = deepcopy(db.rows)
    dry = m.run(db, evidence)
    db.fail_cas = True
    with pytest.raises(ValueError, match="Concurrent"):
        apply(db, evidence, dry)
    assert db.rows == original and not db.writes


@pytest.mark.parametrize("field", ["basis_sha256", "official_sha256", "review_sha256"])
def test_cas_expected_digests_must_all_match(inputs, mock_complete_plan, field):
    basis, selected, evidence = inputs
    db = FakeDatabase(basis, fixture_complete(basis, selected))
    original = deepcopy(db.rows)
    dry = m.run(db, evidence)
    dry[field] = "a" * 64
    with pytest.raises(ValueError):
        apply(db, evidence, dry)
    assert db.rows == original and not db.writes


def test_partial_fixture_inputs_cannot_apply(inputs):
    basis, official, evidence = inputs
    db = FakeDatabase(basis, official)
    with pytest.raises(ValueError):
        m.run(db, evidence)
    assert not db.writes


def test_conflicting_history_is_not_overwritten(inputs, mock_complete_plan):
    basis, selected, evidence = inputs
    db = FakeDatabase(basis, fixture_complete(basis, selected))
    dry = m.run(db, evidence)
    key = "share-basis-review-history:005930:" + dry["basis_sha256"]
    db.rows[key] = db.wrap({"payload": {"wrong": True}})
    prior = deepcopy(db.rows)
    with pytest.raises(ValueError, match="Immutable"):
        apply(db, evidence, dry)
    assert db.rows == prior


def test_archive_fixed_hash_mismatch_is_blocked(fixture_archive, monkeypatch):
    monkeypatch.setitem(m.PINNED, "005930-7.html", "0" * 64)
    with pytest.raises(ValueError, match="hash"):
        m.archive_evidence(fixture_archive)


def test_archive_parser_with_manufactured_documents(fixture_archive):
    evidence = m.archive_evidence(fixture_archive)
    assert evidence["shares"] == "5903847141"
    assert evidence["document_hashes"] == m.PINNED


def test_full_fixture_calculation_does_not_authorize_apply(inputs):
    basis, selected, evidence = inputs
    official = fixture_complete(basis, selected)
    _, report = m.plan(basis, official, evidence, complete=False)
    assert report["target_ocf_ttm_ready"]
    assert report["prior_calculation_rows_preserved"]
    assert not report["apply_ready"] and not report["complete_official_snapshot"]
    with pytest.raises(ValueError, match="fixture_non_production"):
        m.plan(basis, official, evidence, complete=True)
    db = FakeDatabase(basis, official)
    original = deepcopy(db.rows)
    with pytest.raises(ValueError, match="fixture_non_production"):
        m.run(db, evidence)
    assert not db.writes and db.rows == original


@pytest.mark.parametrize(
    "marker",
    [
        {"fixture_non_production": True},
        {"xbrl_facts": [{"source_trace": {"fixture_non_production": True}}]},
        {"cashflow_reviews": [{"fixture_non_production": True}]},
    ],
)
def test_complete_review_rejects_nested_fixture_before_calculation(marker):
    with pytest.raises(ValueError, match="fixture_non_production"):
        m.validate_inputs({}, marker, {}, complete=True)


def test_repository_root_is_correct_for_operational_path():
    assert m.ROOT == Path(__file__).resolve().parents[1]


def test_optional_local_original_archive_and_selected_facts():
    """Opt in with two local paths. No captures/snapshots ship with CI fixtures."""
    archive = os.environ.get("SAMSUNG_COMPARATIVE_REVIEW_ARCHIVE")
    inputs = os.environ.get("SAMSUNG_COMPARATIVE_REVIEW_INPUTS")
    if not archive or not inputs:
        pytest.skip("Optional preserved local archive/input paths were not supplied")
    data = json.loads(Path(inputs).read_text(encoding="utf-8-sig"))
    official = {"xbrl_facts": data["facts"]}
    before = m.digest((data["basis"], official))
    evidence = m.archive_evidence(Path(archive))
    _, report = m.plan(data["basis"], official, evidence, complete=False)
    assert report["normalized_after"] and report["factor_after"] == "1"
    assert not report["apply_ready"]
    assert before == m.digest((data["basis"], official))


def test_manual_apply_requires_basis_writer_fence(inputs, mock_complete_plan):
    basis, selected, evidence = inputs
    db = FakeDatabase(basis, fixture_complete(basis, selected))
    dry = m.run(db, evidence)
    with pytest.raises(ValueError, match="writer"):
        m.run(
            db,
            evidence,
            apply=True,
            reviewer="fixture_non_production",
            expected_basis=dry["basis_sha256"],
            expected_official=dry["official_sha256"],
            expected_review=dry["review_sha256"],
        )
    assert not db.writes


def test_analysis_cache_rebuilds_on_basis_revision_without_official_rewrite(
    inputs, mock_complete_plan
):
    from services.market_dashboard.official_views import PROJECTION_VERSION, read_view

    basis, selected, evidence = inputs
    db = FakeDatabase(basis, fixture_complete(basis, selected))
    official_before = deepcopy(db.rows[m.OFFICIAL_KEY])
    cached_key = "official-view:005930:analysis"

    class ViewStore:
        def __init__(self):
            self.cache = {
                cached_key: {
                    "source_revision": db.rows[m.OFFICIAL_KEY]["revision"],
                    "share_basis_revision": db.rows[m.BASIS_KEY]["revision"],
                    "projection_version": PROJECTION_VERSION,
                    "payload": {"fixture_old_view": True},
                }
            }
            self.rebuilds = 0

        def revisions(self, keys):
            return {key: db.rows[key]["revision"] for key in keys if key in db.rows}

        def get(self, key, default=None, **_):
            return db.rows[key]["payload"] if key in db.rows else self.cache.get(key, default)

        def put(self, key, payload):
            assert key == cached_key
            self.cache[key] = payload
            self.rebuilds += 1

    view = ViewStore()
    assert read_view(view, "005930", "analysis") == {"fixture_old_view": True}
    apply(db, evidence, m.run(db, evidence))
    fresh = read_view(view, "005930", "analysis")
    assert view.rebuilds == 1 and "fixture_old_view" not in fresh
    assert any(
        r.get("metric") == "OCF"
        and r.get("frequency") == "ttm"
        and r.get("period_end") == "2026-06-30"
        for r in fresh["fundamental_metrics"]["rows"]
    )
    assert db.rows[m.OFFICIAL_KEY] == official_before
