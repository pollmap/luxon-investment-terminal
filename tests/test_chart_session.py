"""fixture_non_production: newly fetched old source data is not today's chart."""
from services.market_dashboard.visuals import chart_context


def test_old_session_is_stale_even_when_fetched_today():
    result = chart_context({'status': 'ready', 'received_at': '2026-10-02T10:00:00+09:00', 'points': [
        {'time': '20260922153000', 'value': 90},
        {'time': '20260923195900', 'value': 100},
    ]}, 'day', '2026-10-01')
    assert result['status'] == 'stale'
    assert result['session_date'] == '2026-09-23'
    assert len(result['points']) == 1
    assert result['fetched_at'].startswith('2026-10-02')


def test_multi_day_chart_is_preserved():
    points = [{'time': '2026-09-22T10:00:00+09:00', 'value': 90}, {'time': '2026-09-23T10:00:00+09:00', 'value': 100}]
    result = chart_context({'points': points, 'status': 'ready'}, 'week', '2026-10-01')
    assert result['points'] == points
    assert result['session_date'] == '2026-09-23'
    assert not result['session_mismatch']
