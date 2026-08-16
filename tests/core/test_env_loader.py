from __future__ import annotations

import os

from packages.core import env as env_loader


def test_parse_env_line_handles_quotes_comments_and_export() -> None:
    assert env_loader.parse_env_line('SEC_USER_AGENT="NEXUS/0.1 contact@example.com"') == (
        "SEC_USER_AGENT",
        "NEXUS/0.1 contact@example.com",
    )
    assert env_loader.parse_env_line("export DATA_BACKEND=postgres") == (
        "DATA_BACKEND",
        "postgres",
    )
    assert env_loader.parse_env_line("DATABASE_URL=postgresql://localhost/db # local") == (
        "DATABASE_URL",
        "postgresql://localhost/db",
    )
    assert env_loader.parse_env_line("# ignored") is None
    assert env_loader.parse_env_line("not-an-env-line") is None


def test_load_local_env_uses_env_local_before_env_without_overriding(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("DATA_BACKEND", "process")
    monkeypatch.setattr(env_loader, "_LOADED", False)

    (tmp_path / "pnpm-workspace.yaml").write_text("packages: []\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        'SEC_USER_AGENT="from-env"\nDATA_BACKEND=env\n',
        encoding="utf-8",
    )
    (tmp_path / ".env.local").write_text(
        'SEC_USER_AGENT="from-local"\n',
        encoding="utf-8",
    )

    loaded = env_loader.load_local_env(force=True, start=tmp_path)

    assert "SEC_USER_AGENT" in loaded
    assert os.environ["SEC_USER_AGENT"] == "from-local"
    assert os.environ["DATA_BACKEND"] == "process"
