from __future__ import annotations

import os
from pathlib import Path

import uvicorn


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _select_env_file(bundle_root: Path) -> Path | None:
    bundle_env = bundle_root / ".env"
    if bundle_env.exists():
        return bundle_env

    repo_env = bundle_root.parent / ".env"
    if repo_env.exists():
        return repo_env

    return None


if __name__ == "__main__":
    bundle_root = Path(__file__).resolve().parent
    env_file = _select_env_file(bundle_root)
    if env_file is not None:
        _load_env(env_file)

    host = os.getenv("WEB_API_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_API_PORT", "8110"))
    reload = os.getenv("WEB_API_RELOAD", "").lower() in {"1", "true", "yes"}

    uvicorn.run(
        "roma_web_search_api.main:app",
        host=host,
        port=port,
        reload=reload,
    )
