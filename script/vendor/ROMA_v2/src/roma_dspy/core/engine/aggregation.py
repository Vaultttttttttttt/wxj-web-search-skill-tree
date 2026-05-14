"""WRITE aggregation: leaf-node concatenation and citation management.

This module handles non-LLM aggregation for report generation:
- Recursive collection of leaf WRITE nodes across the entire DAG subtree
- Topological-order concatenation of chapter results
- Inline citation replacement: ``[Source: URL]`` → ``[N]`` with deduplication

Design principle: WRITE content is NEVER re-synthesised by an LLM aggregator.
All leaf WRITE results are concatenated by code. LLM aggregation is only used
for non-WRITE tasks (RETRIEVE/THINK synthesis).
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, TYPE_CHECKING

from loguru import logger

from roma_dspy.core.engine.dag import TaskDAG
from roma_dspy.core.signatures import SubTask, TaskNode
from roma_dspy.types import TaskType

if TYPE_CHECKING:
    pass


class AggregationMixin:
    """Mixin providing WRITE aggregation and citation logic for ModuleRuntime.

    Expects the host class to have:
    - self.context_store: ContextStore
    - self._record_module_result(task, module_name, input_data, output_data, duration, ...)
    """

    # ------------------------------------------------------------------
    # Leaf WRITE collection (the single source of truth for report content)
    # ------------------------------------------------------------------

    def _collect_ordered_leaf_writes(
        self, subgraph: TaskDAG
    ) -> List[TaskNode]:
        """Recursively collect completed leaf WRITE nodes in topological order.

        A "leaf WRITE" is a WRITE node that has no subgraph (i.e. it was
        executed directly and produced chapter content, not decomposed further).

        The DFS follows topological order within each subgraph level, then
        recurses into child subgraphs. This naturally produces the correct
        chapter ordering because:
        - Within a group, chapters are sequentially chained (dependency order)
        - Across groups, the parent subgraph's topological order determines
          which group comes first
        """
        ordered: List[TaskNode] = []
        try:
            exec_order = subgraph.get_execution_order()
        except ValueError:
            exec_order = list(subgraph.graph.nodes())

        for tid in exec_order:
            node = subgraph.get_node(tid)
            child_sg = (
                subgraph.get_subgraph(node.subgraph_id)
                if node.subgraph_id
                else None
            )
            if child_sg:
                ordered.extend(self._collect_ordered_leaf_writes(child_sg))
            elif node.task_type == TaskType.WRITE and node.result:
                ordered.append(node)

        return ordered

    def _has_leaf_writes(self, subgraph: Optional[TaskDAG]) -> bool:
        """Quick check: does this subtree contain any completed leaf WRITE nodes?"""
        if not subgraph:
            return False
        return len(self._collect_ordered_leaf_writes(subgraph)) > 0

    # ------------------------------------------------------------------
    # Inline Citation → Numbered Reference Replacement
    # ------------------------------------------------------------------

    def _replace_inline_citations(
        self, text: str, metadata_sources: List[str]
    ) -> tuple[str, List[str]]:
        """Replace inline ``[Source: URL]`` citations with numbered ``[N]`` markers.

        Returns:
            (replaced_text, unique_sources)
        """
        _CITE_RE = re.compile(
            r"\[Source:\s*(https?://[^\]\s]+|ragflow://[^\]\s]+)\]"
        )

        seen: set = set()
        inline_ordered: List[str] = []
        for m in _CITE_RE.finditer(text):
            url = m.group(1)
            if url not in seen:
                seen.add(url)
                inline_ordered.append(url)

        url_to_idx: Dict[str, int] = {
            url: idx for idx, url in enumerate(inline_ordered, 1)
        }

        def _replacer(m: re.Match) -> str:
            url = m.group(1)
            idx = url_to_idx.get(url)
            return f"[{idx}]" if idx else m.group(0)

        replaced = _CITE_RE.sub(_replacer, text)
        return replaced, inline_ordered

    # ------------------------------------------------------------------
    # Leaf WRITE Concatenation
    # ------------------------------------------------------------------

    _SOURCES_TAIL_RE = re.compile(
        r"\n*-{0,5}\s*\n+###\s*(?:Sources|参考文献|数据来源|References)\s*\n([\s\S]*)$",
        re.IGNORECASE,
    )
    _NUMBERED_REF_RE = re.compile(
        r"\[(\d+)\]\s*(https?://[^\s\[\]<>]+|ragflow://[^\s\[\]<>]+)"
    )
    _URL_IN_SOURCES_RE = re.compile(
        r"(https?://[^\s\[\]<>]+|ragflow://[^\s\[\]<>]+)"
    )
    _INLINE_NUM_RE = re.compile(r"\[(\d+)\]")

    def _concatenate_leaf_writes(
        self,
        task: TaskNode,
        leaf_writes: List[TaskNode],
        dag: TaskDAG,
    ) -> TaskNode:
        """Concatenate leaf WRITE node results with unified citation numbering.

        Steps:
        1. Strip per-chapter ``### Sources`` sections
        2. Build a global URL list, renumber each chapter's ``[N]`` refs
        3. If chapters used ``[Source: URL]`` instead, do inline replacement
        4. Append a unified ``### Sources`` bibliography
        """
        start_time = time.time()
        all_sources: List[str] = []

        chapter_texts: List[str] = []
        chapter_ref_maps: List[Dict[int, str]] = []

        for node in leaf_writes:
            result = str(node.result)
            local_map: Dict[int, str] = {}
            sources_match = self._SOURCES_TAIL_RE.search(result)
            if sources_match:
                sources_block = sources_match.group(1)
                for m in self._NUMBERED_REF_RE.finditer(sources_block):
                    local_map[int(m.group(1))] = m.group(2)
                all_sources.extend(self._URL_IN_SOURCES_RE.findall(sources_block))

            cleaned = self._SOURCES_TAIL_RE.sub("", result).rstrip()
            chapter_texts.append(cleaned)
            chapter_ref_maps.append(local_map)

        global_url_list: List[str] = []
        global_url_idx: Dict[str, int] = {}

        def _get_global_idx(url: str) -> int:
            if url not in global_url_idx:
                global_url_idx[url] = len(global_url_list) + 1
                global_url_list.append(url)
            return global_url_idx[url]

        renumbered_chapters: List[str] = []
        for chapter_text, local_map in zip(chapter_texts, chapter_ref_maps):
            if not local_map:
                renumbered_chapters.append(chapter_text)
                continue

            local_to_global: Dict[int, int] = {}
            for local_num, url in local_map.items():
                local_to_global[local_num] = _get_global_idx(url)

            def _renumber(m: re.Match, ltg=local_to_global) -> str:
                n = int(m.group(1))
                gn = ltg.get(n)
                return f"[{gn}]" if gn is not None else m.group(0)

            renumbered_chapters.append(
                self._INLINE_NUM_RE.sub(_renumber, chapter_text)
            )

        concatenated = "\n\n".join(renumbered_chapters)

        if not global_url_list:
            concatenated, inline_sources = self._replace_inline_citations(
                concatenated, all_sources
            )
            global_url_list = inline_sources

        if global_url_list:
            sources_section = "\n\n---\n\n### Sources\n\n"
            for i, src in enumerate(global_url_list, 1):
                sources_section += f"[{i}] {src}\n"
            concatenated += sources_section

        duration = time.time() - start_time

        logger.info(
            f"[WRITE-CONCAT] Concatenated {len(leaf_writes)} leaf WRITE chapters, "
            f"{len(global_url_list)} unique sources, "
            f"{len(concatenated)} chars total, "
            f"took {duration:.2f}s (no LLM call)"
        )

        task = self._record_module_result(
            task,
            "aggregator",
            {
                "original_goal": task.goal,
                "mode": "leaf_write_concatenation",
                "chapter_count": len(leaf_writes),
                "source_count": len(global_url_list),
                "total_chars": len(concatenated),
            },
            concatenated,
            duration,
        )
        task = task.with_result(concatenated)
        dag.update_node(task)
        return task

    # ------------------------------------------------------------------
    # Subtask result collection (for LLM aggregator path only)
    # ------------------------------------------------------------------

    _AGG_DEFAULT_MAX_RESULT_CHARS = 6000

    def _collect_subtask_results(
        self,
        subgraph: Optional[TaskDAG],
        max_result_chars: Optional[int] = None,
    ) -> List[SubTask]:
        """Collect subtask results from a subgraph for LLM aggregation.

        Args:
            subgraph: The subgraph containing subtask nodes.
            max_result_chars: Per-subtask result truncation limit (chars).
                Detailed content is already persisted in artifact files
                accessible to downstream executors; the aggregator only
                needs a condensed view. ``None`` uses the class default.
        """
        limit = max_result_chars if max_result_chars is not None else self._AGG_DEFAULT_MAX_RESULT_CHARS
        collected: List[SubTask] = []
        if subgraph:
            for node in subgraph.get_all_tasks(include_subgraphs=False):
                if (
                    node.metadata
                    and node.metadata.get("skipped_due_to_content_filter", False)
                ):
                    logger.warning(
                        f"[AGG-SKIP] Skipping subtask {node.task_id[:8]} in aggregation "
                        f"due to content-filter skip marker"
                    )
                    continue

                context_input = getattr(node, "context_input", None)
                if node.dependencies:
                    dep_ids = list(node.dependencies)
                    dependency_context = self.context_store.get_context_for_dependencies(
                        dep_ids
                    )
                    if context_input and dependency_context:
                        context_input = (
                            f"{context_input}\n\n<dependency_context>\n"
                            f"{dependency_context}\n</dependency_context>"
                        )
                    elif dependency_context:
                        context_input = dependency_context

                sources = None

                if node.metadata and "sources" in node.metadata:
                    sources = node.metadata["sources"]
                if not sources and node.execution_history:
                    executor_result = node.execution_history.get("executor")
                    if (
                        executor_result
                        and hasattr(executor_result, "metadata")
                        and executor_result.metadata
                        and "sources" in executor_result.metadata
                    ):
                        sources = executor_result.metadata["sources"]

                result_str = str(node.result) if node.result else ""

                if not sources and result_str:
                    inline_urls = re.findall(
                        r"\[Source:\s*(https?://[^\]\s]+|ragflow://[^\]\s]+)\]",
                        result_str,
                    )
                    if inline_urls:
                        seen = set()
                        sources = []
                        for url in inline_urls:
                            if url not in seen:
                                seen.add(url)
                                sources.append(url)

                if limit and len(result_str) > limit:
                    truncated = result_str[:limit]
                    last_newline = truncated.rfind("\n")
                    if last_newline > limit * 0.6:
                        truncated = truncated[:last_newline]
                    result_str = truncated + f"\n\n... [truncated, full content in artifact file ({len(str(node.result))} chars total)]"
                    logger.debug(
                        f"[AGG-TRUNCATE] Truncated subtask {node.task_id[:8]} "
                        f"result from {len(str(node.result))} to {limit} chars"
                    )

                collected.append(
                    SubTask(
                        goal=node.goal,
                        task_type=node.task_type,
                        dependencies=[],
                        result=result_str,
                        context_input=context_input,
                        sources=sources,
                        agg_task_id=node.task_id,
                    )
                )
        return collected
