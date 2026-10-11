"""Bounded XBRL parser: retain namespaces, periods, dimensions and corrections."""

import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date

from .analysis import numeric

X = "{http://www.xbrl.org/2003/instance}"
D = "{http://xbrl.org/2006/xbrldi}"
MAX_TOTAL, MAX_FILE = 128 * 1024**2, 32 * 1024**2


def local(tag):
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def parse_instance(content, *, filing_id, source_trace, basis=None, publication=None):
    if (
        len(content) > MAX_FILE
        or b"\x00" in content[:512]
        or b"<!DOCTYPE" in content.upper()
        or b"<!ENTITY" in content.upper()
    ):
        raise ValueError("Unsafe or oversized XML")
    namespaces = {}
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(content), events=("start-ns",)):
        namespaces[prefix] = uri

    def qname(value):
        prefix, name = value.split(":", 1) if ":" in value else ("", value)
        return "{" + namespaces.get(prefix, prefix) + "}" + name

    root = ET.fromstring(content)
    if root.tag != X + "xbrl":
        return []
    contexts, units = {}, {}
    for c in root.findall(X + "context"):
        identifier, period = c.find(".//" + X + "identifier"), c.find(X + "period")
        if identifier is None or period is None:
            continue
        dimensions = {}
        for member in c.iter():
            if member.tag == D + "explicitMember":
                dimensions[qname(member.get("dimension", ""))] = qname((member.text or "").strip())
            elif member.tag == D + "typedMember":
                dimensions[qname(member.get("dimension", ""))] = ET.tostring(
                    member, encoding="unicode"
                )
        start, end = period.findtext(X + "startDate"), period.findtext(X + "endDate")
        instant = period.findtext(X + "instant")
        try:
            end = date.fromisoformat(end or instant).isoformat()
            start = date.fromisoformat(start).isoformat() if start else None
        except (ValueError, TypeError):
            continue
        if start and start > end:
            continue
        scopes = {local(v) for k, v in dimensions.items() if "ConsolidatedAndSeparate" in k}
        accounting = (
            "CFS"
            if scopes == {"ConsolidatedMember"}
            else "OFS"
            if scopes == {"SeparateMember"}
            else basis
        )
        contexts[c.get("id")] = {
            "entity_id": identifier.text,
            "entity_scheme": identifier.get("scheme"),
            "period_start": start,
            "period_end": end,
            "dimensions": dimensions,
            "basis": accounting,
            "kind": "duration" if start else "instant",
        }
    for unit in root.findall(X + "unit"):
        numerator = unit.findall(".//" + X + "unitNumerator/" + X + "measure")
        denominator = unit.findall(".//" + X + "unitDenominator/" + X + "measure")
        if not denominator:
            numerator = unit.findall(X + "measure")
        nums = sorted(qname(m.text or "") for m in numerator)
        dens = sorted(qname(m.text or "") for m in denominator)
        currencies = [local(n) for n in nums if "iso4217" in n]
        units[unit.get("id")] = {
            "unit": "*".join(nums) + ("/" + "*".join(dens) if dens else ""),
            "currency": currencies[0] if len(currencies) == 1 else None,
            "per_share": any(local(n) == "shares" for n in dens),
            "monetary": len(currencies) == 1 and not dens and len(nums) == 1,
        }
    facts = []
    for element in root:
        context, unit = contexts.get(element.get("contextRef")), units.get(element.get("unitRef"))
        if (
            context is None
            or unit is None
            or element.get("{http://www.w3.org/2001/XMLSchema-instance}nil") in ("true", "1")
        ):
            continue
        value = numeric((element.text or "").strip())
        if value is None:
            continue
        duration = (
            (
                date.fromisoformat(context["period_end"])
                - date.fromisoformat(context["period_start"])
            ).days
            + 1
            if context["period_start"]
            else 0
        )
        kind = (
            "per_share"
            if unit["per_share"]
            else context["kind"]
            if unit["monetary"]
            else "non_additive"
        )
        evidence = {
            **source_trace,
            "filing_id": filing_id,
            "available_at": publication,
            "context_id": element.get("contextRef"),
            "method": "xbrl-instance-v1",
            "formula": None,
        }
        facts.append(
            {
                **context,
                "account_id": element.tag,
                "name": local(element.tag),
                "value": str(value),
                "unit": unit["unit"],
                "currency": unit["currency"],
                "kind": kind,
                "period_verified": True,
                "cumulative": duration > 100,
                **({"fiscal_start": context["period_start"]} if duration > 100 else {}),
                "frequency": "annual"
                if 350 <= duration <= 380
                else "quarterly"
                if 70 <= duration <= 100
                else "cumulative"
                if duration
                else "instant",
                "published_at": publication,
                "filing_id": filing_id,
                "decimals": element.get("decimals"),
                "source_trace": evidence,
            }
        )
    return facts


def parse_xbrl_zip(content, **kwargs):
    if len(content) > MAX_TOTAL:
        raise ValueError("Archive too large")
    facts = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        files = archive.infolist()
        if len(files) > 2000 or sum(f.file_size for f in files) > MAX_TOTAL:
            raise ValueError("Expanded archive too large")
        for entry in files:
            if entry.is_dir() or not entry.filename.lower().endswith((".xml", ".xbrl")):
                continue
            if entry.file_size > MAX_FILE:
                raise ValueError("XBRL instance too large")
            facts.extend(parse_instance(archive.read(entry), **kwargs))
    return facts


async def collect_xbrl(
    source, code, filing_id, report_code, *, publication=None, replay_revision=None
):
    from datetime import UTC, datetime, timedelta

    from .fact_store import FactStore
    from .store import now

    if not re.fullmatch(r"[0-9]{14}", filing_id) or report_code not in (
        "11011",
        "11012",
        "11013",
        "11014",
    ):
        raise ValueError("Invalid filing identity")
    key = "xbrl-job:" + filing_id
    state = source.store.get(key, {})
    if (
        state.get("status") in ("ready", "unsupported_instance")
        and (not publication or state.get("publication") == publication)
        and (not replay_revision or state.get("bootstrap_replay_revision") == replay_revision)
    ) or (state.get("retry_after") or "") > now():
        return state
    if not source.dart and not replay_revision:
        return {"status": "credentials_missing"}
    endpoint = "https://opendart.fss.or.kr/api/fnlttXbrl.xml"
    source_url = endpoint + "?rcept_no=" + filing_id + "&reprt_code=" + report_code
    try:
        digest = state.get("raw_hash")
        if replay_revision:
            # Missing EPS inputs in a successful archive justify one local
            # reparse. They do not justify repeatedly downloading the same ZIP.
            from .raw_archive import read_original

            # A local replay must never refresh the issuer map through a provider.
            company_id = source.store.get("dart-corporations", {}).get("codes", {}).get(code)
            if code not in source.store.listed_codes() or not re.fullmatch(
                r"\d{8}", str(company_id or "")
            ):
                raise ValueError("Archived XBRL corporation identity is missing")
            content = read_original(source.store.root / "raw/dart-xbrl", str(digest))
            metadata = json.loads(
                (source.store.root / "raw/dart-xbrl" / (digest + ".json")).read_bytes()
            )
            if metadata.get("url") != source_url or metadata.get("sha256") != digest:
                raise ValueError("Archived XBRL request identity mismatch")
        archived = source.store.root / "raw" / "dart-xbrl" / (str(digest) + ".bin")
        if replay_revision:
            pass
        elif digest and re.fullmatch(r"[0-9a-f]{64}", digest) and archived.is_file():
            content = archived.read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("Archived XBRL hash mismatch")
        else:
            response = await source._dart_get(
                endpoint,
                params={"crtfc_key": source.dart, "rcept_no": filing_id, "reprt_code": report_code},
            )
            response.raise_for_status()
            content = response.content
            digest = source.store.raw("dart-xbrl", source_url, content)
        if not content.startswith(b"PK"):
            root = ET.fromstring(content)
            raise ValueError("dart_" + root.findtext("status", "invalid_response"))
        source_trace = {
            "source": "opendart_xbrl",
            "source_url": source_url,
            "sha256": digest,
        }
        # Replaying a stored ZIP is not a new receipt of the source document.
        # Keep processing time in the job state, outside immutable fact lineage.
        received_at = metadata.get("received_at") if replay_revision else now()
        if received_at:
            source_trace["received_at"] = received_at
        facts = parse_xbrl_zip(
            content,
            filing_id=filing_id,
            publication=publication,
            source_trace=source_trace,
        )
        if not replay_revision:
            company_id = (await source.corporations()).get(code)
        if not company_id:
            raise ValueError("Missing corporation identity")
        persisted = FactStore(source.store)
        count = persisted.append(company_id, facts)
        official = source.store.get("official:" + code, {})
        # All observations remain immutable in FactStore. The hot snapshot only
        # needs consolidated totals and reported EPS, not every note/segment.
        official["xbrl_facts"] = persisted.analysis_facts(company_id)
        official["xbrl_projection_version"] = 4
        source.store.put("official:" + code, official)
        state = {
            "status": "ready" if facts else "unsupported_instance",
            "facts": len(facts),
            "inserted": count,
            "raw_hash": digest,
            "publication": publication,
            "received_at": now(),
            **({"bootstrap_replay_revision": replay_revision} if replay_revision else {}),
        }
    except Exception as exc:
        state = {
            **state,
            "status": "error",
            "error": type(exc).__name__,
            "received_at": now(),
            "retry_after": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
    source.store.put(key, state)
    return state


async def xbrl_loop(source):
    import asyncio
    import logging

    from .storage_budget import collection_capacity
    from .store import now

    bootstrap_turn = 0
    while True:
        try:
            if collection_capacity(source.store, "xbrl")["limited"]:
                await asyncio.sleep(60)
                continue
            if source.dart:
                from .share_basis_bootstrap import service_xbrl_intent

                bootstrap_turn += 1
                if bootstrap_turn % 5 == 1 and await service_xbrl_intent(source):
                    # Keep the existing single-XBRL-per-cycle request budget.
                    await asyncio.sleep(30)
                    continue
                active = set(source.store.active())
                from .fact_store import FactStore

                for code in active:
                    official = source.store.get("official:" + code, {})
                    if (
                        official.get("xbrl_facts")
                        and official.get("xbrl_projection_version", 0) < 4
                    ):
                        corporation = (await source.corporations()).get(code)
                        if corporation:
                            official["xbrl_facts"] = FactStore(source.store).analysis_facts(
                                corporation
                            )
                            official["xbrl_projection_version"] = 4
                            source.store.put("official:" + code, official)

                # Older successful financial snapshots predate XBRL queuing.
                # Discover receipts from their immutable stored response, not by
                # refetching a report whose refresh TTL has not elapsed.
                discovery = source.store.get("xbrl-discovery", {})
                codes = sorted(source.store.listed_codes())
                if codes:
                    cursor = int(discovery.get("cursor", 0)) % len(codes)
                    for code in sorted(active | {codes[cursor]}):
                        queue_stored_reports(source.store, code)
                        from .filing_index import queue_index_reports

                        queue_index_reports(source.store, code)
                    source.store.put(
                        "xbrl-discovery", {"cursor": (cursor + 1) % len(codes), "updated_at": now()}
                    )
                # Recover real publication dates for historical filings. One
                # company per cycle uses the same DART request budget.
                from .filing_index import collect_periodic_index

                visits = int(discovery.get("index_visits", 0))
                universe_cursor = int(discovery.get("universe_index_cursor", 0))
                use_universe = bool(codes and (visits % 5 == 0 or not active))
                targets = [codes[universe_cursor % len(codes)]] if use_universe else sorted(active)
                if targets:
                    index = int(discovery.get("publication_cursor", 0)) % len(targets)
                    try:
                        await collect_periodic_index(source, targets[index])
                    except Exception as exc:
                        logging.getLogger("market-worker").warning(
                            "Filing index: %s", type(exc).__name__
                        )
                    state = source.store.get("xbrl-discovery", {})
                    source.store.put(
                        "xbrl-discovery",
                        {
                            **state,
                            "publication_cursor": index + 1,
                            "index_visits": visits + 1,
                            "universe_index_cursor": universe_cursor + int(use_universe),
                        },
                    )
                jobs = source.store.list_prefix("xbrl-request:")
                ordered = sorted(
                    jobs.values(),
                    # Active companies first, then newest filings. Old requests
                    # must not delay the current reporting period behind years of history.
                    key=lambda j: (
                        j.get("code") not in active,
                        -int(j["filing_id"]),
                        j.get("requested_at", ""),
                    ),
                )
                for job in ordered:
                    state = source.store.get("xbrl-job:" + job["filing_id"], {})
                    filings = source.store.get("filings:" + job["code"], {}).get(
                        "rows", []
                    ) + source.store.get("filing-index:" + job["code"], {}).get("rows", [])
                    published = next(
                        (
                            f.get("date")
                            for f in filings
                            if str(f.get("receipt") or f.get("rcept_no") or "") == job["filing_id"]
                        ),
                        None,
                    )
                    if (
                        state.get("status") in ("ready", "unsupported_instance")
                        and (not published or state.get("publication") == published)
                    ) or (state.get("retry_after") or "") > now():
                        continue
                    await collect_xbrl(
                        source,
                        job["code"],
                        job["filing_id"],
                        job["report_code"],
                        publication=published,
                    )
                    break
        except Exception as exc:
            logging.getLogger("market-worker").warning("XBRL collection: %s", type(exc).__name__)
        await asyncio.sleep(30)


def queue_stored_reports(store, code):
    """Idempotently recover missed work from source snapshots; preserve job status."""
    import hashlib
    import json

    from .store import now

    source_key = "official:" + code
    revision = store.revisions((source_key,)).get(source_key)
    key = "xbrl-discovered:" + code
    if store.get(key, {}).get("revision") == revision:
        return 0
    reports = store.get(source_key, {}).get("financial_reports", {})
    fingerprint = hashlib.sha256(json.dumps(reports, sort_keys=True).encode()).hexdigest()
    if store.get(key, {}).get("fingerprint") == fingerprint:
        store.put(key, {"revision": revision, "fingerprint": fingerprint})
        return 0
    added = 0
    for report in reports.values():
        if report.get("status") != "ready" or report.get("report_code") not in (
            "11011",
            "11012",
            "11013",
            "11014",
        ):
            continue
        snapshot = store.get(report["snapshot_key"], {}) if report.get("snapshot_key") else report
        for receipt in {str(row.get("receipt") or "") for row in snapshot.get("rows", [])}:
            if not re.fullmatch(r"[0-9]{14}", receipt) or store.get("xbrl-request:" + receipt):
                continue
            store.put(
                "xbrl-request:" + receipt,
                {
                    "code": code,
                    "filing_id": receipt,
                    "report_code": report["report_code"],
                    "requested_at": now(),
                    "discovered_from": report.get("snapshot_key"),
                },
            )
            added += 1
    store.put(key, {"revision": revision, "fingerprint": fingerprint})
    return added
