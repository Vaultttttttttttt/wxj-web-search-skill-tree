from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import Settings
from .schemas import SearchExecution, WebSearchRequest


class NullRagFlowToolkit:
    """Web-only API shim so AdaptiveRetrieveToolkit can initialize cleanly."""

    def retrieve(self, query: str, top_n: int = 10, top_k: int = 1024) -> list[dict[str, Any]]:
        return []

    def format_ragflow_contexts(self, chunks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return []

    async def cleanup(self) -> None:
        return None


def _append_roma_src_to_sys_path(roma_src_root: Path) -> None:
    roma_src_root = roma_src_root.expanduser().resolve()
    if not roma_src_root.exists():
        raise RuntimeError(f"ROMA source root not found: {roma_src_root}")
    roma_src = str(roma_src_root)
    if roma_src not in sys.path:
        sys.path.insert(0, roma_src)


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines without overriding real process env."""

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


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = str(item.get("text") or item.get("content") or "").strip()
            else:
                text = str(item).strip()
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return str(content).strip()


def extract_query(payload: WebSearchRequest) -> str:
    if payload.query and payload.query.strip():
        return payload.query.strip()

    for message in reversed(payload.messages):
        if message.role == "user":
            text = _content_to_text(message.content)
            if text:
                return text

    for message in reversed(payload.messages):
        text = _content_to_text(message.content)
        if text:
            return text

    raise ValueError("A non-empty `query` or user message is required.")


_TITLE_SOURCE_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")


def _public_roma_result(roma_result: dict[str, Any]) -> dict[str, Any]:
    """Make ROMA's internal source labels clearer for API consumers.

    Internally ROMA still uses ``source=exa`` as a coarse "external web"
    bucket for historical reasons. The public API should expose the routed
    source/skill or website label instead, while keeping the discovery backend.
    """

    public = copy.deepcopy(roma_result)
    contexts = public.get("contexts")
    if not isinstance(contexts, list):
        return public

    for context in contexts:
        if not isinstance(context, dict):
            continue
        internal_source = str(context.get("source") or "").strip()
        if internal_source.lower() != "exa":
            continue

        title = str(context.get("title") or "")
        match = _TITLE_SOURCE_RE.match(title)
        display_source = match.group(1).strip() if match else ""
        if not display_source:
            continue

        context["source"] = display_source
        context["source_type"] = "web"
        context["discovery_backend"] = internal_source

    return public


class WebSearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._toolkit: Any = None
        self._toolkit_lock = asyncio.Lock()

    async def _ensure_toolkit(self) -> Any:
        if self._toolkit is not None:
            return self._toolkit

        async with self._toolkit_lock:
            if self._toolkit is not None:
                return self._toolkit

            self._prepare_runtime_environment()
            _append_roma_src_to_sys_path(self.settings.roma_src_root)
            from roma_dspy.tools.adaptive_retrieve_toolkit import AdaptiveRetrieveToolkit

            self._toolkit = AdaptiveRetrieveToolkit(
                enabled=True,
                default_mode="web",
                evaluator_mode="heuristic",
                top_n_default=self.settings.default_top_n,
                web_backend=self.settings.web_backend,
                mlflow_logging=False,
                ragflow_toolkit_instance=NullRagFlowToolkit(),
                skill_tree_config=self.settings.skill_tree_config(),
            )
            return self._toolkit

    def _prepare_runtime_environment(self) -> None:
        # A single .env in web_api/script is enough for server deployment.
        _load_env_file(self.settings.project_root / ".env")

        runtime_paths = {
            "ROMA_SRC_ROOT": self.settings.roma_src_root,
            "WEB_SEARCH_SKILL_ROOT": self.settings.skill_root,
            "WEB_SEARCH_UNION_ROOT": self.settings.union_search_root,
            "WEB_SEARCH_NEWS_AGGREGATOR_ROOT": self.settings.news_aggregator_root,
            "ACADEMIC_RESEARCH_SKILLS_ROOT": self.settings.academic_research_root,
            "GOOGLE_SCHOLAR_SKILLS_ROOT": self.settings.google_scholar_root,
        }
        for key, path in runtime_paths.items():
            os.environ[key] = str(path)

    def _resolve_top_n(self, requested: Optional[int]) -> int:
        top_n = requested if requested is not None else self.settings.default_top_n
        return max(1, min(int(top_n), self.settings.max_top_n))

    @staticmethod
    def _slugify_query(query: str, max_length: int = 80) -> str:
        tokens = re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", query)
        slug = "_".join(tokens[:12]).strip("_").lower()
        slug = re.sub(r"_+", "_", slug)
        return slug[:max_length].rstrip("_") or "query"

    def _artifact_base_path(self, query: str, artifact_id: Optional[str]) -> Path:
        self.settings.artifact_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = artifact_id or uuid.uuid4().hex
        slug = self._slugify_query(query)
        return self.settings.artifact_dir / f"{stamp}_{suffix}_{slug}"

    @staticmethod
    def _markdown_payload(execution: SearchExecution) -> str:
        return "\n".join(
            [
                f"# ROMA Web Search Result: {execution.query}",
                "",
                f"- **Model**: {execution.model}",
                f"- **Decision**: {execution.roma_result.get('decision', '')}",
                f"- **Confidence**: {execution.roma_result.get('confidence', '')}",
                "",
                execution.content,
                "",
            ]
        )

    async def _persist_execution(
        self,
        execution: SearchExecution,
        artifact_id: Optional[str],
    ) -> SearchExecution:
        base = self._artifact_base_path(execution.query, artifact_id)
        json_path = base.with_suffix(".json").resolve()
        markdown_path = base.with_suffix(".md").resolve()

        execution.artifact_json_path = str(json_path)
        execution.artifact_markdown_path = str(markdown_path)

        await asyncio.to_thread(
            json_path.write_text,
            json.dumps(execution.model_dump(), ensure_ascii=False, indent=2),
            "utf-8",
        )
        await asyncio.to_thread(
            markdown_path.write_text,
            self._markdown_payload(execution),
            "utf-8",
        )
        return execution

    async def execute(
        self,
        payload: WebSearchRequest,
        artifact_id: Optional[str] = None,
    ) -> SearchExecution:
        toolkit = await self._ensure_toolkit()
        query = extract_query(payload)
        top_n = self._resolve_top_n(payload.top_n)
        result = await toolkit.adaptive_retrieve_async(
            query=query,
            mode="web",
            top_n=top_n,
        )
        content = toolkit._format_contexts_for_llm(result)
        roma_result = _public_roma_result(result.to_dict())
        execution = SearchExecution(
            query=query,
            model=payload.model or self.settings.default_model,
            content=content,
            roma_result=roma_result,
        )
        return await self._persist_execution(execution, artifact_id=artifact_id)

    async def cleanup(self) -> None:
        toolkit = self._toolkit
        if toolkit is None:
            return
        cleanup = getattr(toolkit, "cleanup", None)
        if cleanup is None:
            return
        maybe = cleanup()
        if asyncio.iscoroutine(maybe):
            await maybe
