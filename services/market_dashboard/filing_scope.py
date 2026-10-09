"""Restrict the public market feed to verified in-scope issuers, retaining raw DART data."""
from .service_scope import universe_view


def public_filings(store, snapshot):
    securities = universe_view(store.get('universe', {})).get('rows', [])
    codes = {row['code'] for row in securities}
    corps = {value for key, value in store.get('dart-corporations', {}).get('codes', {}).items() if key in codes}
    # Older snapshots lack identifiers. Require matching name AND DART listed
    # market class until the next collector refresh adds issuer identifiers.
    names = {row['name'] for row in securities if row.get('name')}
    rows = []
    for row in snapshot.get('rows', []):
        stock = row.get('code') or row.get('stock_code')
        corp = row.get('corp_code')
        match = stock in codes or corp in corps if stock or corp else (
            row.get('corp_cls') in ('Y', 'K') and row.get('corp_name') in names)
        if match:
            rows.append(row)
    return {**snapshot, 'rows': rows, 'displayed_count': len(rows),
            'scope': '코스피·코스닥 상장 기업 공시',
            'universe_filter_applied': True}
