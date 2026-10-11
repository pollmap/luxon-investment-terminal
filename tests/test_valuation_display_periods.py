from services.market_dashboard.valuation_series import display_periods


def row(start, value, published, reason=None):
    return dict(period_start=start, period_end='2025-12-31', frequency='annual',
                value=value, available_at=published, reason=reason)


def test_verified_filing_replaces_legacy_summary_but_keeps_latest_correction():
    legacy = row(None, 6563.57, '2026-10-01', 'period_unverified')
    original = row('2025-01-01', 6600, '2026-03-01')
    corrected = row('2025-01-01', 6605, '2026-03-15')
    assert display_periods([legacy, original, corrected]) == [corrected]
    assert legacy['value'] == 6563.57


def test_changed_financial_years_and_unverified_only_observations_are_preserved():
    first = row('2025-01-01', 10, '2026-03-01')
    changed = row('2024-12-20', 11, '2026-03-01')
    assert len(display_periods([first, changed])) == 2
    unverified = row(None, 9, None, 'period_unverified')
    assert display_periods([unverified]) == [unverified]
