"""Select daily turnover facts without borrowing a different trading day."""
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlsplit


def volume_field_trace(fact, code):
    """Adapt legacy candle-envelope metadata without rewriting stored evidence."""
    trace = fact.get('source_trace') or {}
    if trace.get('source') != 'toss' or fact.get('unit') != '주' or fact.get('venue') != '통합':
        return fact
    try:
        url = urlsplit(trace.get('source_url', ''))
        query = parse_qs(url.query)
    except (TypeError, ValueError):
        return fact
    if (url.hostname != 'openapi.tossinvest.com' or url.path != '/api/v1/candles'
            or query.get('symbol') != [code] or query.get('interval') != ['1d']):
        return fact
    return {**fact, 'source_trace': {**trace, 'unit': '주',
            'period': fact['period'], 'venue': '통합', 'field': 'volume'}}


def trading_day(value):
    if not isinstance(value, str):
        return None
    try:
        if 'T' in value:
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return stamp.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat() if stamp.tzinfo else None
        if re.fullmatch(r'\d{8}', value):
            return datetime.strptime(value, '%Y%m%d').date().isoformat()
        return date.fromisoformat(value.strip().rstrip('.').replace('.', '-')).isoformat()
    except ValueError:
        return None


def select_daily_measures(combined, candidates):
    """Keep stored evidence; only same-day observations are current measures.

    Candidates are newest preferred first. A stale enrichment must not hide
    a valid current listing observation. Collection time is never a period.
    """
    day = trading_day(combined.get('as_of'))
    facts = dict(combined.get('facts', {}))
    for key in ('volume', 'value'):
        available = [group[key] for group in candidates if group.get(key) and group[key].get('value') is not None]
        selected = next((fact for fact in available if day and trading_day(fact.get('period')) == day), None)
        if selected is not None:
            if key == 'volume':
                selected = volume_field_trace(selected, combined.get('code'))
            facts[key] = selected
            combined[key] = selected['value']
        else:
            last = available[0] if available else facts.get(key, {})
            combined[key] = None
            facts[key] = {**last, 'value': None, 'availability_reason':
                          'reference_day_missing' if not day else 'different_or_unknown_trading_day'}
    combined['facts'] = facts
    return combined
