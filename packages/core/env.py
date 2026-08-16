from __future__ import annotations

import os
from pathlib import Path


_LOADED = False


def load_local_env(*, force: bool = False, start: Path | None = None) -> list[str]:
    """Load local development secrets without overriding real environment vars.

    Precedence is:
    1. Existing process environment
    2. .env.local
    3. .env

    The function returns only loaded key names, never values.
    """

    global _LOADED
    if _LOADED and not force:
        return []

    root = find_project_root(start or Path.cwd())
    loaded: list[str] = []
    for env_file in (root / ".env.local", root / ".env"):
        loaded.extend(load_env_file(env_file))

    _LOADED = True
    return loaded


def find_project_root(start: Path) -> Path:
    current = start.resolve()
    candidates = [current, *current.parents]
    for candidate in candidates:
        if (candidate / "pnpm-workspace.yaml").exists() and (candidate / "pyproject.toml").exists():
            return candidate

    package_root = Path(__file__).resolve()
    for candidate in [package_root, *package_root.parents]:
        if (candidate / "pnpm-workspace.yaml").exists() and (candidate / "pyproject.toml").exists():
            return candidate
    return current


def load_env_file(path: Path) -> list[str]:
    if not path.exists():
        return []

    loaded: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if key in os.environ:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


def parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return None

    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
        return None

    return key, _clean_env_value(value.strip())


def _clean_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]

    if "#" not in value:
        return value

    chars: list[str] = []
    escaped = False
    for char in value:
        if char == "\\" and not escaped:
            escaped = True
            chars.append(char)
            continue
        if char == "#" and not escaped:
            break
        escaped = False
        chars.append(char)
    return "".join(chars).strip()
