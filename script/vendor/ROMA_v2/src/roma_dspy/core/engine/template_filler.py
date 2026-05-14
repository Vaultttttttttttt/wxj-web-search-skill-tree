"""Runtime filling of dynamic_prompt template placeholders.

After the RETRIEVE phase completes, directive templates produced by MIPE
warm-up contain ``{placeholder}`` tokens that need concrete values drawn
from actual retrieval results.  ``TemplateFiller`` performs this mapping
with **zero LLM calls** — it is pure Python code.

Filling sources:
  - ``{evidence_focus}``  — high-frequency themes from RETRIEVE outputs
  - ``{evidence_type}``   — material type classification (papers/reports/news)
  - ``{skip_topic}``      — topics already well-covered in retrieved evidence

When the Sentinel provides explicit ``directive_fill_hints`` they take
priority over the auto-extracted values.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from roma_dspy.core.signatures.base_models.plan_blueprint import PlanBlueprint

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

_MATERIAL_PATTERNS: Dict[str, List[str]] = {
    "学术论文和研究报告": [
        "paper", "journal", "arxiv", "ieee", "acm", "doi",
        "论文", "研究", "学术", "期刊",
    ],
    "产业数据和市场报告": [
        "report", "market", "industry", "forecast", "gartner",
        "idc", "statista", "报告", "市场", "产业", "数据",
    ],
    "新闻与评论": [
        "news", "blog", "article", "opinion",
        "新闻", "评论", "报道",
    ],
    "官方文档和政策": [
        "doc", "documentation", "policy", "regulation", "law",
        "文档", "政策", "法规",
    ],
}


@dataclass
class FillResult:
    """Outcome of a template-filling pass."""
    filled_count: int = 0
    skipped_count: int = 0
    fill_map: Dict[str, str] = field(default_factory=dict)


class TemplateFiller:
    """Zero-LLM placeholder resolver for PlanBlueprint directive templates."""

    def fill_from_retrieve_results(
        self,
        retrieve_results: Dict[str, str],
        blueprint: Optional["PlanBlueprint"] = None,
        sentinel_hints: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Compute placeholder values from RETRIEVE outputs.

        Args:
            retrieve_results: Mapping of ``task_id -> result_text`` from
                completed RETRIEVE nodes.
            blueprint: PlanBlueprint for placeholder discovery
                (optional — used to validate which placeholders exist).
            sentinel_hints: Explicit ``directive_fill_hints`` from the
                Sentinel.  These override auto-extracted values.

        Returns:
            Mapping of ``placeholder_name -> concrete_value``.
        """
        all_text = "\n".join(retrieve_results.values())
        auto_values: Dict[str, str] = {}

        auto_values["evidence_focus"] = self._extract_focus(all_text)
        auto_values["evidence_type"] = self._classify_material_type(all_text)
        auto_values["skip_topic"] = self._extract_skip_topic(
            all_text, auto_values["evidence_focus"]
        )

        if sentinel_hints:
            auto_values.update(sentinel_hints)

        return auto_values

    def apply_to_dynamic_prompts(
        self,
        dynamic_prompts: Dict[str, Optional[str]],
        fill_values: Dict[str, str],
    ) -> FillResult:
        """Fill placeholders in a set of dynamic_prompt strings in place.

        Args:
            dynamic_prompts: Mutable mapping ``node_id -> dynamic_prompt``
                where values may contain ``{placeholder}`` tokens.
                Modified in place with filled strings.
            fill_values: Mapping from placeholder name to concrete value.

        Returns:
            ``FillResult`` with statistics.
        """
        result = FillResult(fill_map=dict(fill_values))
        for node_id, prompt in dynamic_prompts.items():
            if not prompt:
                result.skipped_count += 1
                continue

            placeholders = set(_PLACEHOLDER_RE.findall(prompt))
            if not placeholders:
                result.skipped_count += 1
                continue

            new_prompt = prompt
            for ph in placeholders:
                if ph in fill_values:
                    new_prompt = new_prompt.replace(f"{{{ph}}}", fill_values[ph])
                    result.filled_count += 1

            dynamic_prompts[node_id] = new_prompt

        return result

    # ------------------------------------------------------------------
    # Extraction heuristics (no LLM)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_focus(text: str) -> str:
        """Pick the most prominent topic phrase from RETRIEVE outputs."""
        words = re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z]{4,}", text)
        if not words:
            return "相关领域"

        _STOPWORDS = frozenset({
            "研究", "分析", "报告", "数据", "信息", "内容", "方面",
            "进行", "使用", "通过", "以及", "关于", "包括", "相关",
            "根据", "其中", "目前", "系统", "提供", "不同", "重要",
            "from", "with", "that", "this", "have", "will",
            "been", "they", "their", "about", "which",
        })
        filtered = [w for w in words if w.lower() not in _STOPWORDS]
        if not filtered:
            return "相关领域"

        counter = Counter(filtered)
        top = counter.most_common(3)
        return "、".join(w for w, _ in top)

    @staticmethod
    def _classify_material_type(text: str) -> str:
        """Classify the dominant material type from RETRIEVE text."""
        text_lower = text.lower()
        scores: Dict[str, int] = {}
        for label, keywords in _MATERIAL_PATTERNS.items():
            scores[label] = sum(
                1 for kw in keywords if kw in text_lower
            )

        if not scores or max(scores.values()) == 0:
            return "综合资料"

        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        return best

    @staticmethod
    def _extract_skip_topic(text: str, focus: str) -> str:
        """Identify well-covered topics that downstream writers can skip."""
        words = re.findall(r"[\u4e00-\u9fff]{2,6}", text)
        if not words:
            return "基础概念"

        counter = Counter(words)
        focus_words = set(re.findall(r"[\u4e00-\u9fff]{2,6}", focus))

        candidates = [
            w for w, c in counter.most_common(10)
            if w not in focus_words and c >= 3
        ]
        return "、".join(candidates[:2]) if candidates else "基础概念"
