from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ENV_LOADED = False
ENV_PREFIX = "PAPER_SEARCH_MCP_"


def _candidate_env_files() -> list[Path]:
    explicit_path = os.getenv(f"{ENV_PREFIX}ENV_FILE", "").strip()
    if explicit_path:
        return [Path(explicit_path).expanduser()]

    cwd_env = Path.cwd() / ".env"
    project_env = Path(__file__).resolve().parent.parent / ".env"

    if cwd_env == project_env:
        return [cwd_env]
    return [cwd_env, project_env]


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _load_env_from_file(env_file: Path) -> None:
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue

        value = _strip_quotes(value.strip())
        os.environ.setdefault(key, value)


def load_env_file(force: bool = False) -> None:
    global _ENV_LOADED

    if _ENV_LOADED and not force:
        return

    for env_file in _candidate_env_files():
        if not env_file.exists() or not env_file.is_file():
            continue

        try:
            _load_env_from_file(env_file)
            logger.debug("Loaded environment values from %s", env_file)
            break
        except Exception as exc:
            logger.warning("Failed to load environment file %s: %s", env_file, exc)

    _ENV_LOADED = True


def get_env(name: str, default: Optional[str] = "") -> str:
    load_env_file()

    normalized = name.strip()
    if not normalized:
        return "" if default is None else str(default)

    keys = [f"{ENV_PREFIX}{normalized}", normalized]
    for key in keys:
        if key in os.environ:
            return os.environ.get(key, "")

    return "" if default is None else str(default)


# --- grab tunables: network timeouts / retry budgets (centralized, env-overridable) ---
# Each is read from the env (via get_env, which also honors a PAPER_SEARCH_MCP_ prefix)
# with a fast default. Override in .env, e.g. `GRAB_SEMANTIC_TIMEOUT=20`.

def _env_int(name: str, default: int) -> int:
    raw = get_env(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = get_env(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


# Semantic Scholar: unauthenticated rate limits make it the slowest title-search
# source, so keep its retry/timeout budget small (a 429 storm must not stall a batch).
SEMANTIC_TIMEOUT = _env_int("GRAB_SEMANTIC_TIMEOUT", 10)
SEMANTIC_MAX_RETRIES = _env_int("GRAB_SEMANTIC_MAX_RETRIES", 1)
SEMANTIC_RETRY_DELAY = _env_float("GRAB_SEMANTIC_RETRY_DELAY", 2)

# Fatcat / IA Scholar (legal preserved PDFs): real downtime, so short timeouts plus a
# circuit breaker that disables it after this many consecutive connection failures.
FATCAT_CONNECT_TIMEOUT = _env_float("GRAB_FATCAT_CONNECT_TIMEOUT", 4)
FATCAT_READ_TIMEOUT = _env_float("GRAB_FATCAT_READ_TIMEOUT", 6)
FATCAT_FAILURE_LIMIT = _env_int("GRAB_FATCAT_FAILURE_LIMIT", 2)

# Shadow connectors (opt-in).
SCIDB_TIMEOUT = _env_int("GRAB_SCIDB_TIMEOUT", 8)
NEXUS_TIMEOUT = _env_int("GRAB_NEXUS_TIMEOUT", 10)
