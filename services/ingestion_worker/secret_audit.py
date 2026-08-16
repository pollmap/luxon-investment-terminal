from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

SECRET_QUERY_KEYS = {
    "api_key",
    "apikey",
    "api-key",
    "app_id",
    "appid",
    "apiid",
    "auth",
    "authorization",
    "client_secret",
    "crtfc_key",
    "password",
    "secret",
    "subscription-key",
    "subscription_key",
    "token",
    "access_token",
    "refresh_token",
}

SECRET_ENV_KEYS = {
    "AUTH_GITHUB_SECRET",
    "AUTH_SECRET",
    "BLOB_READ_WRITE_TOKEN",
    "DATABASE_URL",
    "DART_API_KEY",
    "ECOS_API_KEY",
    "EDINET_API_KEY",
    "ESTAT_APP_ID",
    "FRED_API_KEY",
    "JQUANTS_PASSWORD",
    "JQUANTS_REFRESH_TOKEN",
    "KOSIS_API_KEY",
    "OPENDART_API_KEY",
    "PF_COOKIE_SECRET",
}

DEFAULT_METADATA_GLOBS = (
    "storage/blob_queue/**/*.json",
    "storage/cache/**/*.json",
    "storage/parse_failures/**/*.json",
    "storage/rendered_charts/**/*.json",
)
RAW_METADATA_GLOBS = ("storage/raw/**/*.json",)
MAX_DEFAULT_FILE_BYTES = 2_000_000
QUERY_KEY_RE = re.compile(
    r"(?i)(?:[?&;]|\\u0026)([A-Za-z0-9_.-]*(?:api[_-]?key|apikey|crtfc[_-]?key|"
    r"subscription[_-]?key|app[_-]?id|appid|auth|token|secret|password)[A-Za-z0-9_.-]*)="
    r"([^&\s\"'<>\\]+)"
)


def source_metadata_secret_audit(
    *,
    root: Path | str | None = None,
    paths: list[Path | str] | None = None,
    include_raw: bool = False,
    max_file_bytes: int = MAX_DEFAULT_FILE_BYTES,
) -> dict[str, Any]:
    project_root = Path(root or Path.cwd()).resolve()
    scan_paths = _collect_scan_paths(project_root, paths=paths, include_raw=include_raw)
    findings: list[dict[str, Any]] = []
    skipped_files: list[dict[str, str]] = []
    checked_files = 0

    for path in scan_paths:
        try:
            size = path.stat().st_size
        except OSError as exc:
            skipped_files.append({"path": _display_path(project_root, path), "reason": str(exc)})
            continue
        if size > max_file_bytes:
            skipped_files.append(
                {
                    "path": _display_path(project_root, path),
                    "reason": f"file larger than {max_file_bytes} bytes",
                }
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped_files.append(
                {"path": _display_path(project_root, path), "reason": "not utf-8 text"}
            )
            continue
        checked_files += 1
        label = _display_path(project_root, path)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            findings.extend(audit_text_for_secrets(text, label=label))
        else:
            findings.extend(audit_source_metadata_payload(payload, label=label))

    findings = _dedupe_findings(findings)
    return {
        "status": "failed" if findings else "passed",
        "checked_files": checked_files,
        "skipped_files": skipped_files,
        "findings": findings,
        "include_raw": include_raw,
        "max_file_bytes": max_file_bytes,
    }


def audit_source_metadata_payload(payload: Any, *, label: str = "payload") -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    _walk_payload(payload, label=label, path="$", findings=findings)
    return _dedupe_findings(findings)


def audit_text_for_secrets(text: str, *, label: str = "text") -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    findings.extend(_query_param_findings(text, label=label, path="$"))
    findings.extend(_env_secret_value_findings(text, label=label, path="$"))
    return _dedupe_findings(findings)


def _collect_scan_paths(
    root: Path,
    *,
    paths: list[Path | str] | None,
    include_raw: bool,
) -> list[Path]:
    if paths:
        candidates = [Path(path) for path in paths]
    else:
        globs = [*DEFAULT_METADATA_GLOBS, *(RAW_METADATA_GLOBS if include_raw else ())]
        candidates = []
        for pattern in globs:
            candidates.extend(root.glob(pattern))

    resolved: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        path = candidate if candidate.is_absolute() else root / candidate
        path = path.resolve()
        if path.is_file() and path not in seen:
            resolved.append(path)
            seen.add(path)
    return sorted(resolved)


def _walk_payload(payload: Any, *, label: str, path: str, findings: list[dict[str, Any]]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            child_path = f"{path}.{key}"
            normalized_key = _normalize_key(str(key))
            if normalized_key in SECRET_QUERY_KEYS and _is_sensitive_value(value):
                findings.append(
                    {
                        "label": label,
                        "path": child_path,
                        "kind": "secret_field",
                        "key": str(key),
                        "evidence": f"{key}=<redacted:{len(str(value))} chars>",
                    }
                )
            _walk_payload(value, label=label, path=child_path, findings=findings)
        return
    if isinstance(payload, list):
        for index, item in enumerate(payload):
            _walk_payload(item, label=label, path=f"{path}[{index}]", findings=findings)
        return
    if isinstance(payload, str):
        findings.extend(_query_param_findings(payload, label=label, path=path))
        findings.extend(_env_secret_value_findings(payload, label=label, path=path))


def _query_param_findings(text: str, *, label: str, path: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    findings.extend(_url_query_findings(text, label=label, path=path))
    for match in QUERY_KEY_RE.finditer(text):
        key = match.group(1)
        value = match.group(2)
        if _normalize_key(key) in SECRET_QUERY_KEYS and _is_sensitive_value(value):
            findings.append(
                {
                    "label": label,
                    "path": path,
                    "kind": "secret_query_param",
                    "key": key,
                    "evidence": f"{key}=<redacted:{len(value)} chars>",
                }
            )
    return findings


def _url_query_findings(text: str, *, label: str, path: str) -> list[dict[str, Any]]:
    try:
        parsed = urlsplit(text)
    except ValueError:
        return []
    if not parsed.query:
        return []

    findings: list[dict[str, Any]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if _normalize_key(key) in SECRET_QUERY_KEYS and _is_sensitive_value(value):
            findings.append(
                {
                    "label": label,
                    "path": path,
                    "kind": "secret_query_param",
                    "key": key,
                    "evidence": f"{key}=<redacted:{len(value)} chars>",
                }
            )
    return findings


def _env_secret_value_findings(text: str, *, label: str, path: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for env_key in sorted(SECRET_ENV_KEYS):
        secret_value = os.getenv(env_key)
        if not _is_sensitive_value(secret_value):
            continue
        if str(secret_value) in text:
            findings.append(
                {
                    "label": label,
                    "path": path,
                    "kind": "secret_env_value",
                    "key": env_key,
                    "evidence": f"{env_key}=<redacted:{len(str(secret_value))} chars>",
                }
            )
    return findings


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


def _is_sensitive_value(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered in {"redacted", "<redacted>", "none", "null", "n/a", "na"}:
        return False
    if set(text) <= {"*", "x", "X"}:
        return False
    return len(text) >= 4


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for finding in findings:
        key = (
            str(finding.get("label")),
            str(finding.get("path")),
            str(finding.get("kind")),
            str(finding.get("key")),
            str(finding.get("evidence")),
        )
        if key not in seen:
            deduped.append(finding)
            seen.add(key)
    return deduped
