import json
from pathlib import Path

from services.ingestion_worker.secret_audit import (
    audit_source_metadata_payload,
    source_metadata_secret_audit,
)


def test_source_metadata_secret_audit_flags_unredacted_query_key(tmp_path: Path):
    secret_value = "real-secret-value"
    manifest_dir = tmp_path / "storage" / "blob_queue"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "opendart.json").write_text(
        json.dumps(
            {
                "source_url": (
                    "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
                    f"?crtfc_key={secret_value}&corp_code=00126380"
                )
            }
        ),
        encoding="utf-8",
    )

    summary = source_metadata_secret_audit(root=tmp_path)

    assert summary["status"] == "failed"
    assert summary["checked_files"] == 1
    assert summary["findings"][0]["key"] == "crtfc_key"
    assert secret_value not in json.dumps(summary, ensure_ascii=False)


def test_source_metadata_secret_audit_allows_redacted_query_key(tmp_path: Path):
    manifest_dir = tmp_path / "storage" / "blob_queue"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "opendart.json").write_text(
        json.dumps(
            {
                "source_url": (
                    "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
                    "?crtfc_key=REDACTED&corp_code=00126380"
                )
            }
        ),
        encoding="utf-8",
    )

    summary = source_metadata_secret_audit(root=tmp_path)

    assert summary["status"] == "passed"
    assert summary["findings"] == []


def test_source_metadata_secret_audit_flags_env_secret_value(monkeypatch):
    secret_value = "env-secret-value-123"
    monkeypatch.setenv("OPENDART_API_KEY", secret_value)

    findings = audit_source_metadata_payload(
        {"metadata": {"cached_request": f"https://example.com/{secret_value}"}},
        label="payload",
    )

    assert findings[0]["kind"] == "secret_env_value"
    assert findings[0]["key"] == "OPENDART_API_KEY"
    assert secret_value not in json.dumps(findings, ensure_ascii=False)


def test_source_metadata_secret_audit_flags_dart_alias_env_secret_value(monkeypatch):
    secret_value = "dart-env-secret-value-123"
    monkeypatch.setenv("DART_API_KEY", secret_value)

    findings = audit_source_metadata_payload(
        {"metadata": {"cached_request": f"https://example.com/{secret_value}"}},
        label="payload",
    )

    assert findings[0]["kind"] == "secret_env_value"
    assert findings[0]["key"] == "DART_API_KEY"
    assert secret_value not in json.dumps(findings, ensure_ascii=False)
