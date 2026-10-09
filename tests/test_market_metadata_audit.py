from scripts.audit_market_metadata import audit_rows


def row_with_price(**overrides):
    row = {
        "code": "005930",
        "market": "KOSPI",
        "price": 70500,
        "facts": {
            "price": {
                "value": 70500,
                "period": "2026-09-23T15:30:00+09:00",
                "unit": "원",
                "source_trace": {
                    "source": "public_quote",
                    "source_document_id": "a" * 64,
                    "source_url": "https://example.com/quote/005930",
                    "period": "2026-09-23T15:30:00+09:00",
                    "unit": "원",
                    "ingested_at": "2026-09-23T06:30:01+00:00",
                    "available_at": None,
                },
            }
        },
    }
    row.update(overrides)
    return row


def test_populated_values_require_matching_period_source_and_ingest_metadata():
    report = audit_rows([row_with_price()], {"price": {"label": "현재가", "unit": "원"}})

    assert report["passed"] is True
    assert report["metrics"]["price"]["populated"] == 1
    assert report["metrics"]["price"]["available_at_known"] == 0
    assert report["violations"] == {}


def test_audit_fails_when_fact_period_or_source_digest_does_not_match():
    row = row_with_price()
    row["facts"]["price"]["source_trace"]["period"] = "2026-09-22"
    row["facts"]["price"]["source_trace"]["source_document_id"] = "not-a-digest"

    report = audit_rows([row], {"price": {"label": "현재가", "unit": "원"}})

    assert report["passed"] is False
    assert report["violations"] == {
        "fact_trace_period_mismatch": 1,
        "missing_or_invalid_response_digest": 1,
    }
