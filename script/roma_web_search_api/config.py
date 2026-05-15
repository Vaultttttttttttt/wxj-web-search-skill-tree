from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


def _load_env_file_without_override(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_path(name: str, default: Path, base: Path) -> Path:
    raw = os.getenv(name)
    path = Path(raw).expanduser() if raw and raw.strip() else default
    if not path.is_absolute():
        path = base / path
    return path


@dataclass(frozen=True)
class Settings:
    project_root: Path
    roma_src_root: Path
    skill_root: Path
    union_search_root: Path
    news_aggregator_root: Path
    academic_research_root: Path
    google_scholar_root: Path
    api_keys_file: Path
    canonical_prefix: str
    compatibility_prefix: str
    default_model: str
    default_top_n: int
    max_top_n: int
    stream_chunk_chars: int
    stream_chunk_delay_ms: int
    task_ttl_seconds: int
    web_backend: str
    artifact_dir: Path
    history_file: Path

    def skill_tree_config(self) -> Dict[str, Any]:
        return {
            "skill_root": str(self.skill_root),
            "union_search_root": str(self.union_search_root),
            "news_aggregator_root": str(self.news_aggregator_root),
        }


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[1]
    _load_env_file_without_override(project_root / ".env")
    roma_src_root = _env_path(
        "ROMA_SRC_ROOT",
        project_root / "vendor" / "ROMA_v2" / "src",
        project_root,
    )
    artifact_dir = _env_path("WEB_API_ARTIFACT_DIR", project_root / "outputs", project_root)

    return Settings(
        project_root=project_root,
        roma_src_root=roma_src_root,
        skill_root=_env_path(
            "WEB_SEARCH_SKILL_ROOT",
            project_root / "skills" / "web-search-innospark-tree",
            project_root,
        ),
        union_search_root=_env_path(
            "WEB_SEARCH_UNION_ROOT",
            project_root / "skills" / "union-search-skill",
            project_root,
        ),
        news_aggregator_root=_env_path(
            "WEB_SEARCH_NEWS_AGGREGATOR_ROOT",
            project_root / "skills" / "news-aggregator-skill",
            project_root,
        ),
        academic_research_root=_env_path(
            "ACADEMIC_RESEARCH_SKILLS_ROOT",
            project_root / "skills" / "academic-research-skills",
            project_root,
        ),
        google_scholar_root=_env_path(
            "GOOGLE_SCHOLAR_SKILLS_ROOT",
            project_root / "skills" / "gs-skills",
            project_root,
        ),
        api_keys_file=_env_path("WEB_API_KEYS_FILE", project_root / "api_keys.txt", project_root),
        canonical_prefix=os.getenv("WEB_API_PREFIX", "/web-search/v1"),
        compatibility_prefix=os.getenv("WEB_API_COMPAT_PREFIX", "/deepsearch/v1"),
        default_model=os.getenv("WEB_API_DEFAULT_MODEL", "roma-web-search"),
        default_top_n=max(1, _env_int("WEB_API_DEFAULT_TOP_N", 24)),
        max_top_n=max(1, _env_int("WEB_API_MAX_TOP_N", 200)),
        stream_chunk_chars=max(64, _env_int("WEB_API_STREAM_CHUNK_CHARS", 600)),
        stream_chunk_delay_ms=max(0, _env_int("WEB_API_STREAM_CHUNK_DELAY_MS", 45)),
        task_ttl_seconds=max(60, _env_int("WEB_API_TASK_TTL_SECONDS", 86400)),
        web_backend=os.getenv("WEB_API_WEB_BACKEND", "skill_tree").strip().lower(),
        artifact_dir=artifact_dir,
        history_file=_env_path(
            "WEB_API_HISTORY_FILE",
            artifact_dir / "search_history.json",
            project_root,
        ),
    )
