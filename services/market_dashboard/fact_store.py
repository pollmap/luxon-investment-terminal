"""Append-only, versioned financial facts alongside the legacy snapshot read model."""

from __future__ import annotations

import hashlib
import json
import os

from sqlalchemy import Column, MetaData, String, Table, Text, or_, select

from .source_trace_store import FINANCIAL_FORMAT, SourceTraceStore, compact_payloads


def storage_capabilities():
    """Separate rollout gate: old time-series readers cannot read financial refs."""
    return {'inline_financial_facts': True, 'read_formats': [FINANCIAL_FORMAT],
            'reference_write_env': 'MARKET_SHARED_FINANCIAL_TRACES',
            'reference_writes_default': False}


def fact_identity(payload):
    """Collection clocks are not new disclosure facts or correction versions."""
    identity_payload = {
        **payload,
        'source_trace': {
            key: value for key, value in (payload.get('source_trace') or {}).items()
            if key not in ('received_at', 'collected_at', 'ingested_at')
        },
    }
    return hashlib.sha256(json.dumps(
        identity_payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')
    ).encode()).hexdigest()


class FactStore:
    def __init__(self, store):
        self.store = store
        metadata = MetaData()
        self.facts = Table(
            "market_financial_facts",
            metadata,
            Column("id", String(64), primary_key=True),
            Column("company_id", String(32), nullable=False, index=True),
            Column("account_id", Text, nullable=False),
            Column("period_end", String(10), nullable=False, index=True),
            Column("available_at", String(40)),
            Column("filing_id", String(64), nullable=False),
            Column("payload", Text, nullable=False),
        )
        metadata.create_all(store.engine)
        self.source_traces = SourceTraceStore(store)
        # Financial refs need their own reader/rollback gate. The existing
        # time-series flag must never enable a new financial storage format.
        self.shared_source_traces = os.getenv('MARKET_SHARED_FINANCIAL_TRACES', '0') == '1'

    def append(self, company_id, facts):
        from .analysis import numeric
        from .financial_dates import publication_date
        if self.store.engine.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        elif self.store.engine.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            raise ValueError("Financial facts require SQLite or PostgreSQL")

        count = 0
        with self.store.lock, self.store.engine.begin() as db:
            batch = []
            clocked = set()
            def flush():
                if not batch:
                    return 0
                pending = list(batch)
                if clocked:
                    # Pre-fix IDs included ingested_at. Keep those immutable IDs
                    # and input_fact_ids valid: recognize equivalent legacy facts
                    # within the same issuer/filing/accounts, never rewrite them.
                    candidates = [record for record in batch if record['id'] in clocked]
                    previous = db.execute(select(self.facts.c.payload).where(
                        self.facts.c.company_id == company_id,
                        self.facts.c.filing_id.in_({r['filing_id'] for r in candidates}),
                        self.facts.c.account_id.in_({r['account_id'] for r in candidates}),
                        self.facts.c.period_end.in_({r['period_end'] for r in candidates}),
                    )).scalars().all()
                    existing = {fact_identity(payload) for payload in
                                self.source_traces.restore_payloads(previous, connection=db)}
                    pending = [record for record in batch if record['id'] not in existing]
                batch.clear()
                clocked.clear()
                if not pending:
                    return 0
                if self.shared_source_traces:
                    bodies, evidence = compact_payloads(
                        [record['payload'] for record in pending], format=FINANCIAL_FORMAT,
                    )
                    self.source_traces.persist(evidence, db)
                    pending = [{**record, 'payload': body}
                               for record, body in zip(pending, bodies, strict=True)]
                statement = insert(self.facts).values(pending).on_conflict_do_nothing(
                    index_elements=[self.facts.c.id]
                ).returning(self.facts.c.id)
                inserted = len(db.execute(statement).all())
                return inserted
            for fact in facts:
                evidence = fact.get("source_trace") or {}
                value = numeric(fact.get("exact_value", fact.get("value")))
                if (
                    not fact.get("account_id")
                    or not fact.get("period_end")
                    or value is None
                    or not evidence
                ):
                    continue
                payload = {**fact, "company_id": company_id, "value": str(value)}
                body = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                # Keep the original evidence and clock in the stored payload.
                identity = fact_identity(payload)
                if 'ingested_at' in evidence:
                    clocked.add(identity)
                batch.append(dict(
                        id=identity,
                        company_id=company_id,
                        account_id=fact["account_id"],
                        period_end=fact["period_end"],
                        available_at=publication_date(fact)[0],
                        filing_id=str(fact.get("filing_id") or evidence.get("filing_id") or ""),
                        payload=body,
                    ))
                # Bound SQLite parameters and memory while removing one SELECT
                # and INSERT round trip per fact. Identical filings remain immutable.
                if len(batch) >= 100:
                    count += flush()
            count += flush()
        return count

    def read(self, company_id, *, account_names=None):
        selection = select(self.facts.c.id, self.facts.c.payload).where(
            self.facts.c.company_id == company_id)
        if account_names is not None:
            # QName local names are curated, never supplied by a public request.
            selection = selection.where(or_(*(
                self.facts.c.account_id.endswith('}'+name) for name in account_names)))
        with self.store.read_connection() as db:
            rows = db.execute(
                selection.order_by(self.facts.c.period_end)
            ).all()
        restored = self.source_traces.restore_payloads([body for _, body in rows])
        return [{**payload, "fact_id": identity}
                for (identity, _), payload in zip(rows, restored, strict=True)]

    def analysis_facts(self, company_id):
        from .eps_calculation import EPS_ACCOUNTS
        from .financial_summary import ACCOUNTS
        from .fundamental_metrics import ACCOUNTS as METRIC_ACCOUNTS
        from .fundamental_metrics import _valid as metric_fact_valid
        from .reviewed_notes import verified_note
        eps_names = EPS_ACCOUNTS | {'DilutedEarningsLossPerShare'}
        names = set(ACCOUNTS) | eps_names | set(METRIC_ACCOUNTS)
        return [f for f in self.read(company_id, account_names=names)
                if (f.get('source_trace',{}).get('source') == 'opendart_xbrl' or verified_note(f))
                and (f.get('account_id','').rsplit('}',1)[-1] in eps_names
                     or metric_fact_valid(f)
                     or all('ConsolidatedAndSeparate' in key
                            for key in (f.get('dimensions') or {})))]
