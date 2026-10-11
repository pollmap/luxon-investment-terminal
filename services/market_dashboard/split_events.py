"""Reuse reviewed share-unit actions in the corporate-action ledger.

A verified split is not evidence of complete dividends or other actions.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import re

from .split_units import valid_history
from .store import now

VERSION = 'reviewed-split-events-v1'


def project_split_events(store, code):
    basis = store.get('share-basis:' + code, {})
    if not valid_history(basis) or basis.get('security_code') != code:
        return {'status': 'split_evidence_required', 'added': 0}
    # The reviewed company identity must still agree with the current official
    # issuer map. Do not use a common-share review for a preferred security.
    corp = store.get('dart-corporations', {}).get('codes', {}).get(code)
    if not corp or corp != basis.get('company_id'):
        return {'status': 'company_identity_mismatch', 'added': 0}
    snapshot = store.get('events:' + code, {})
    rows = deepcopy(snapshot.get('rows', []))
    added, conflicts = 0, []
    today = now()[:10]
    for split in basis['splits']:
        trace = split.get('source_trace', {})
        if not re.fullmatch(r'[0-9a-f]{64}', str(trace.get('sha256', ''))) or not trace.get('source'):
            return {'status': 'split_source_required', 'added': 0}
        day = date.fromisoformat(split['effective_date']).isoformat()
        if day > today:
            continue
        ratio = Decimal(str(split['new_shares'])) / Decimal(str(split['old_shares']))
        identity = hashlib.sha256(f'{code}:split:{day}:{ratio.normalize()}'.encode()).hexdigest()
        existing = [r for r in rows if not r.get('superseded_by')
                    and r.get('kind') == 'split' and r.get('effective_date') == day]
        if existing:
            try:
                different = any(r.get('security_code') != code or Decimal(str(r.get('ratio'))) != ratio
                                or r.get('verified') is not True for r in existing)
            except Exception:
                different = True
            if different:
                conflicts.append({'effective_date': day, 'reviewed_ratio': str(ratio),
                                  'existing_ids': [r.get('id') for r in existing]})
            continue
        rows.append({'id': identity, 'kind': 'split', 'security_code': code,
            'effective_date': day, 'date': day, 'date_kind': 'effective',
            'ratio': str(ratio), 'old_shares': str(split['old_shares']),
            'new_shares': str(split['new_shares']), 'verified': True, 'status': 'verified',
            'title': '주식분할' if ratio >= 1 else '주식병합',
            'source_trace': deepcopy(trace), 'source_url': trace['source_url'],
            'input_basis_id': basis['basis_id'], 'formula': 'new_shares / old_shares',
            'parser_version': VERSION})
        added += 1
    if conflicts:
        store.put('split-event-review:' + code, {'rows': conflicts,
            'basis_id': basis['basis_id'], 'checked_at': now(), 'status': 'conflicting_split_evidence'})
    if added or conflicts:
        coverage = snapshot.get('coverage') or {'verified': False,
            'reason': 'dividends_and_all_actions_required'}
        if conflicts:
            coverage = {**coverage, 'verified': False, 'reason': 'conflicting_split_evidence'}
        store.put('events:' + code, {**snapshot, 'rows': rows,
            'coverage': coverage, 'status': 'partial', 'received_at': now()})
    return {'status': 'conflicting_split_evidence' if conflicts else 'ready', 'added': added}
