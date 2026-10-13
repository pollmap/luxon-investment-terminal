"""Promote explicit first-issuer reviews; discovery alone never approves units.

This first slice supports reviewed no-split ordinary-share windows. A window
containing a split/reorganization remains a manual split-unit review, using the
existing split ledger. No document content is guessed or fetched here.
"""

import hashlib
import json
import re
from copy import deepcopy
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

from bs4 import BeautifulSoup
from sqlalchemy import literal, select, text

from .raw_archive import read_original
from .share_basis import extend_reviewed_history, valid_evidence
from .share_basis_review_store import load_reviews
from .store import now

VERSION = "initial-no-split-review-v1"
PROOFS = ("issuer_share_history", "ordinary_share_identity", "filing_unit_alignment")


def _key(code):
    if not re.fullmatch(r"[0-9A-Z]{6}", str(code)):
        raise ValueError("Invalid review security")
    return "share-basis-promotion:" + code


def _archive(store, proof):
    if not isinstance(proof, dict):
        raise ValueError("Archived proof must be an object")
    source, digest, url = (proof.get(k, "") for k in ("raw_source", "sha256", "source_url"))
    parsed = urlsplit(url)
    private_parameters = {
        "crtfc_key",
        "api_key",
        "apikey",
        "access_token",
        "token",
        "client_secret",
        "secret",
        "authorization",
    }
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", source)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or any(name.lower() in private_parameters for name, _ in parse_qsl(parsed.query))
        or not isinstance(proof.get("excerpt"), str)
        or not proof["excerpt"].strip()
    ):
        raise ValueError("Explicit archived proof and relevant excerpt required")
    directory = store.root / "raw" / source
    metadata = json.loads((directory / (digest + ".json")).read_bytes())
    if (
        not isinstance(metadata, dict)
        or metadata.get("url") != url
        or metadata.get("sha256") != digest
    ):
        raise ValueError("Reviewed archive identity changed")
    if type(metadata.get("bytes")) is not int or not 0 <= metadata["bytes"] <= 64 * 1024**2:
        raise ValueError("Review archive requires a bounded original byte count")
    content = read_original(directory, digest)
    if len(content) != metadata["bytes"]:
        raise ValueError("Review archive byte count changed")
    if len(content) > 64 * 1024**2:
        raise ValueError("Review archive is too large")
    # A valid archive hash alone does not bind the review's quoted passage.
    # Require an exact passage after HTML/whitespace normalization; do not
    # guess encodings or use fuzzy matching to approve share units.
    encoding = proof.get("text_encoding", "utf-8")
    if encoding not in ("utf-8", "utf-8-sig", "euc-kr", "cp949"):
        raise ValueError("Review text encoding is unsupported")
    try:
        document = content.decode(encoding, errors="strict")
    except UnicodeError as exc:
        raise ValueError("Review archive requires an explicit text encoding") from exc
    soup = BeautifulSoup(document, "html.parser")
    for hidden in soup.find_all(["script", "style", "template"]):
        hidden.decompose()
    text_content = " ".join(soup.get_text(" ", strip=True).split())
    excerpt = " ".join(str(proof["excerpt"]).split())
    if excerpt not in text_content:
        raise ValueError("Reviewed excerpt is absent from the archived document")


def validate_review(store, code, review):
    """Validate the explicit decision and re-read its original source bytes."""
    _key(code)
    if not isinstance(review, dict) or not isinstance(review.get("evidence"), dict):
        raise ValueError("Explicit reviewer evidence required")
    company = store.get("dart-corporations", {}, use_redis=False).get("codes", {}).get(code)
    evidence = review.get("evidence", {})
    if (
        code not in store.listed_codes()
        or not company
        or review.get("company_id") != company
        or review.get("security_code") != code
        or review.get("status") != "reviewed"
        or review.get("share_class") != "ordinary"
        or not str(review.get("reviewer", "")).strip()
        or not str(review.get("reason", "")).strip()
    ):
        raise ValueError("Explicit ordinary-share reviewer decision required")
    try:
        reviewed_at = datetime.fromisoformat(review["reviewed_at"])
        if reviewed_at.tzinfo is None or reviewed_at > datetime.fromisoformat(now()):
            raise ValueError("Invalid review time")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid review time") from exc
    if (
        not valid_evidence(evidence, company)
        or evidence.get("security_code") != code
        or evidence.get("no_split_interval") is not True
        or evidence.get("splits")
        or evidence.get("complete_split_history")
    ):
        raise ValueError("Reviewed no-split evidence required; split windows need separate review")
    start, end = date.fromisoformat(evidence["start"]), date.fromisoformat(evidence["end"])
    if end > reviewed_at.date():
        raise ValueError("A future interval cannot be reviewed")
    bootstrap = store.get("share-basis-bootstrap:" + code, {}, use_redis=False)
    inputs = bootstrap.get("inputs", {})
    from .share_basis_bootstrap import describe_inputs, read_eps_facts

    actual_inputs = describe_inputs(read_eps_facts(store, company), company)
    window = inputs.get("candidate_window") or {}
    if (
        bootstrap.get("company_id") != company
        or not inputs.get("period_inputs_complete")
        or not actual_inputs.get("period_inputs_complete")
        or actual_inputs.get("candidate_window") != window
        or sorted(review.get("input_fact_ids", [])) != actual_inputs.get("input_fact_ids")
        or not window.get("start")
        or not window.get("end")
        or not evidence["start"] <= window["start"] <= window["end"] <= evidence["end"]
    ):
        raise ValueError("Continuous period inputs within reviewed interval required")
    proofs = review.get("proofs", {})
    if set(proofs) != set(PROOFS):
        raise ValueError("All initial unit review proofs required")
    for proof in proofs.values():
        _archive(store, proof)
    trace = evidence.get("source_trace", {})
    issuer = proofs["issuer_share_history"]
    if trace.get("sha256") != issuer["sha256"] or trace.get("source_url") != issuer["source_url"]:
        raise ValueError("Share basis must refer to reviewed issuer history")
    # Existing strict replay checks every page, total, hash, issuer and request.
    from .share_basis_worker import verified_window

    job = review.get("disclosure_window", {})
    if (
        job.get("company_id") != company
        or job.get("begin") != start.strftime("%Y%m%d")
        or job.get("end") != evidence["end"]
    ):
        raise ValueError("Initial disclosure window must cover the complete interval")
    pages, filings = verified_window(store, job)
    reviews = load_reviews(
        store, security_code=code, company_id=company, receipts=[r["receipt"] for r in filings]
    )
    # Reuse the established all-filings event checks across the full initial
    # interval. The day-before seed is local only and is never stored as proof.
    previous = (start - timedelta(days=1)).isoformat()
    seed = {**evidence, "start": previous, "end": previous}
    checked = extend_reviewed_history(
        seed,
        security_code=code,
        company_id=company,
        target_end=evidence["end"],
        disclosure_end=evidence["end"],
        disclosure_type="I",
        pages=pages,
        filings=filings,
        issuer_trace=trace,
        filing_reviews=reviews,
    )
    return {
        **deepcopy(evidence),
        "initial_review_version": VERSION,
        "initial_disclosure_review": checked["review_extension"],
    }


def queue_initial_review(store, code, review, *, replaces=None):
    """Queue an operator-reviewed envelope, not a provider tag or bootstrap guess."""
    validate_review(store, code, review)
    queue_key = _key(code)
    dependencies = [
        "dart-corporations",
        "universe",
        "share-basis-bootstrap:" + code,
        "official:" + code,
        "filing-index:" + code,
        "corporate-event-index:" + code,
        "share-unit-reviews:" + code,
    ]
    guards = store.revisions(dependencies)
    # Retain absent dependencies as guarded absence, rather than silently omit.
    guards = {key: guards.get(key) for key in dependencies}
    envelope = {"version": VERSION, "review": deepcopy(review), "input_revisions": guards}
    digest = hashlib.sha256(json.dumps(envelope, sort_keys=True).encode()).hexdigest()
    review_key = f"initial-share-review:{code}:{digest}"
    with store.lock, store.engine.begin() as db:
        if store.engine.dialect.name == "sqlite":
            db.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": queue_key})
        old = db.execute(
            select(store.snapshots.c.payload).where(store.snapshots.c.key == queue_key)
        ).scalar()
        old = json.loads(old) if old else {}
        if old.get("review_key") == review_key:
            return review_key
        if old.get("review_key") != replaces:
            raise ValueError("Replacement requires the exact previous review")
        store.put(review_key, envelope, _connection=db)
        store.put(
            queue_key,
            {
                "status": "queued",
                "review_key": review_key,
                "security_code": code,
                "queued_at": now(),
            },
            _connection=db,
        )
    return review_key


def promote_initial_review(store, code):
    """Bounded worker replay. Never replace an existing share basis."""
    queue_key = _key(code)
    pending = store.get(queue_key, {}, use_redis=False)
    if pending.get("status") != "queued":
        return {"status": "not_queued"}
    review_key = pending.get("review_key", "")
    envelope = store.get(review_key, {}, use_redis=False)
    digest = hashlib.sha256(json.dumps(envelope, sort_keys=True).encode()).hexdigest()
    if review_key != f"initial-share-review:{code}:{digest}" or envelope.get("version") != VERSION:
        raise ValueError("Immutable initial review changed")
    guards = dict(envelope["input_revisions"])
    current_revisions = store.revisions(guards)
    if any(current_revisions.get(key) != value for key, value in guards.items()):
        return {"status": "inputs_changed"}
    # Guard the queue/decision against replacement during archive replay too.
    guards.update(store.revisions([queue_key, review_key]))
    evidence = validate_review(store, code, envelope["review"])
    evidence["initial_review_id"] = review_key
    key = "share-basis:" + code
    if store.get(key, None, use_redis=False) is not None:
        return {"status": "existing_basis_preserved"}
    if store.engine.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    query = select(
        literal(key),
        literal(json.dumps(evidence, ensure_ascii=False, sort_keys=True)),
        literal(now()),
    )
    for name, revision in guards.items():
        actual = (
            select(store.snapshots.c.updated_at)
            .where(store.snapshots.c.key == name)
            .scalar_subquery()
        )
        query = query.where(actual.is_(None) if revision is None else actual == revision)
    statement = insert(store.snapshots).from_select(["key", "payload", "updated_at"], query)
    statement = statement.on_conflict_do_nothing(index_elements=[store.snapshots.c.key]).returning(
        store.snapshots.c.key
    )
    with store.lock, store.engine.begin() as db:
        saved = db.execute(statement).first()
        if not saved:
            return {"status": "inputs_changed_or_existing_basis"}
        store.put(
            queue_key, {**pending, "status": "promoted", "promoted_at": now()}, _connection=db
        )
    store._drop_cached(key)
    return {"status": "promoted", "review_key": review_key}


def process_one_review(store):
    """No automatic retry of rejected/stale decisions; retain the immutable review."""
    queued = sorted(
        (
            item
            for item in store.list_prefix("share-basis-promotion:").values()
            if item.get("status") == "queued"
        ),
        key=lambda item: item.get("queued_at", ""),
    )
    if not queued:
        return {"status": "idle"}
    pending = queued[0]
    code = pending["security_code"]
    try:
        result = promote_initial_review(store, code)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result = {"status": "review_required", "error": type(exc).__name__}
    if result["status"] != "promoted":
        # Do not overwrite an operator's newer queued review after the read.
        latest = store.get(_key(code), {}, use_redis=False)
        if latest == pending:
            store.compare_and_put(
                _key(code),
                pending,
                {
                    **pending,
                    "status": "review_required",
                    "last_result": result,
                    "checked_at": now(),
                },
                history_key="initial-review-attempt:"
                + code
                + ":"
                + hashlib.sha256(json.dumps(pending, sort_keys=True).encode()).hexdigest()[:32],
            )
    return result
