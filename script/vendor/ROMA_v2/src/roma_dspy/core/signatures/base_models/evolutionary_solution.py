"""EvolutionarySolution — an individual in the MIPE evolution search.

Each solution represents a complete planning proposal that can be mutated,
evaluated, and selected through the MIPE (Micro-Island Plan Evolution) process.

Key lifecycle (edit-script architecture):
  1. Created from unified Planner output via ``from_planner_output()``
  2. Mutated via ``apply_prompt_edits()`` / ``apply_topo_edits()``
     — LLM outputs targeted edits, Python applies them deterministically
  3. Compared via Judge using edit-log (``_edit_history``)
  4. Winners merged via programmatic orthogonal assembly
  5. Final solution packaged into a PlanBlueprint by the Assembler
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Union

from loguru import logger
from pydantic import BaseModel, Field

from roma_dspy.core.signatures.base_models.subtask import SubTask


class EvolutionarySolution(BaseModel):
    """A single individual in the MIPE evolutionary search."""

    solution_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:12],
        description="Unique identifier for this solution",
    )

    # === Core Plan (produced by unified Planner) ===
    subtasks: List[SubTask] = Field(
        ..., description="Complete subtask list with goal, task_type, dependencies, dynamic_prompt"
    )
    dependencies_graph: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="DAG topology: node index -> list of dependency indices",
    )
    report_policy: Optional[str] = Field(
        default=None,
        description="Global report strategy (audience, tone, depth, evidence, etc.)",
    )

    # === Evolution Metadata ===
    fitness_score: float = Field(
        default=0.0,
        description="Head-to-head tournament cumulative score",
    )
    island_id: Optional[str] = Field(
        default=None,
        description="Which island produced this solution: 'topology' | 'directive' | 'budget' | None (seed)",
    )
    generation: int = Field(
        default=0,
        description="Evolution generation (0 = initial seed from Planner)",
    )
    parent_id: Optional[str] = Field(
        default=None,
        description="Parent solution ID (None for the seed P0)",
    )
    mutation_log: Optional[str] = Field(
        default=None,
        description="What mutations were applied to produce this solution",
    )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_planner_output(
        cls,
        subtasks: List[SubTask],
        dependencies_graph: Optional[Dict[str, List[str]]] = None,
        report_policy: Optional[str] = None,
    ) -> "EvolutionarySolution":
        """Create the initial seed solution (P0) from unified Planner output."""
        if dependencies_graph is None:
            dependencies_graph = {}

        return cls(
            subtasks=subtasks,
            dependencies_graph=dependencies_graph,
            report_policy=report_policy,
            generation=0,
            island_id=None,
            parent_id=None,
            mutation_log=None,
        )

    # ------------------------------------------------------------------
    # Clone / Mutation
    # ------------------------------------------------------------------

    def clone_with_mutation(
        self,
        *,
        island_id: str,
        mutation_log: str,
        subtasks: Optional[List[SubTask]] = None,
        dependencies_graph: Optional[Dict[str, List[str]]] = None,
        report_policy: Optional[str] = None,
    ) -> "EvolutionarySolution":
        """Create a mutated copy preserving lineage."""
        return EvolutionarySolution(
            subtasks=subtasks if subtasks is not None else [st.model_copy() for st in self.subtasks],
            dependencies_graph=(
                dependencies_graph if dependencies_graph is not None
                else dict(self.dependencies_graph)
            ),
            report_policy=report_policy if report_policy is not None else self.report_policy,
            fitness_score=0.0,
            island_id=island_id,
            generation=self.generation + 1,
            parent_id=self.solution_id,
            mutation_log=mutation_log,
        )

    # ------------------------------------------------------------------
    # Multi-level helpers
    # ------------------------------------------------------------------

    def get_depth0_subtasks(self) -> List[SubTask]:
        """Return only depth-0 subtasks (direct children of root)."""
        return [st for st in self.subtasks if st.depth == 0]

    def get_children(self, parent_index: str) -> List[SubTask]:
        """Return subtasks whose parent_node_id matches *parent_index*."""
        return [st for st in self.subtasks if st.parent_node_id == parent_index]

    @property
    def max_depth(self) -> int:
        if not self.subtasks:
            return 0
        return max(st.depth for st in self.subtasks)

    @classmethod
    def from_flat_multilevel(
        cls,
        flat_subtasks: List[SubTask],
        dependencies_graph: Optional[Dict[str, List[str]]] = None,
        report_policy: Optional[str] = None,
    ) -> "EvolutionarySolution":
        """Create a seed solution from a recursively-generated flat subtask list."""
        if dependencies_graph is None:
            dependencies_graph = {}
            for i, st in enumerate(flat_subtasks):
                if st.dependencies:
                    dependencies_graph[str(i)] = list(st.dependencies)

        return cls(
            subtasks=flat_subtasks,
            dependencies_graph=dependencies_graph,
            report_policy=report_policy,
            generation=0,
            island_id=None,
            parent_id=None,
            mutation_log=None,
        )

    # ------------------------------------------------------------------
    # Cross-island merge helpers
    # ------------------------------------------------------------------

    def diff_prompts_from(self, base: "EvolutionarySolution") -> str:
        """Generate an annotated prompt diff: what *self* changed vs *base*.

        Used by the Assembler to describe DP-island improvements to the
        LLM merge agent.  Each changed node is annotated with its P0
        context (goal, task_type) so the merge agent can find the
        semantically-matching node in T_best's topology.

        Returns a human-readable multi-entry string, one block per
        changed node.  Unchanged nodes are omitted to save tokens.
        """
        if not base or not base.subtasks:
            return "(no base plan available for diff)"

        entries: List[str] = []
        n = min(len(self.subtasks), len(base.subtasks))
        for i in range(n):
            dp_st = self.subtasks[i]
            p0_st = base.subtasks[i]
            if dp_st.dynamic_prompt == p0_st.dynamic_prompt:
                continue

            old_preview = (p0_st.dynamic_prompt or "")[:200]
            if len(p0_st.dynamic_prompt or "") > 200:
                old_preview += "..."

            new_prompt = dp_st.dynamic_prompt or ""

            entries.append(
                f"[P0 Node {i}] task_type={p0_st.task_type.value} | "
                f"goal: {p0_st.goal[:120]}\n"
                f"  BEFORE: {old_preview}\n"
                f"  AFTER:  {new_prompt}"
            )

        if not entries:
            return "(DP island made no prompt changes vs P0)"
        return f"{len(entries)} prompt improvement(s):\n\n" + "\n\n".join(entries)

    # ------------------------------------------------------------------
    # Serialization — Skeleton (compact, for LLM input)
    # ------------------------------------------------------------------

    def to_skeleton_json(
        self,
        include_prompts_for: Optional[List[str]] = None,
    ) -> str:
        """Compact plan skeleton for LLM input — no full dynamic_prompt by default.

        Each node contains only: index, goal, task_type, dependencies.
        Multi-level plans also include: depth, parent_node_id, children_ids, is_leaf.

        Args:
            include_prompts_for: Node indices whose dynamic_prompt should be
                included in full.  Pass ``["all"]`` to include all prompts
                (used by ConsistencyChecker).  ``None`` omits all prompts.

        Token budget: ~500 tokens for a 10-node plan (vs ~3000 for to_json).
        """
        include_all = (
            include_prompts_for is not None and "all" in include_prompts_for
        )
        include_set = set(include_prompts_for or [])
        has_multilevel = any(st.depth > 0 for st in self.subtasks)

        subtask_entries: List[Dict[str, Any]] = []
        for i, st in enumerate(self.subtasks):
            idx_str = str(i)
            entry: Dict[str, Any] = {
                "index": idx_str,
                "goal": st.goal,
                "task_type": st.task_type.value,
                "dependencies": st.dependencies,
            }
            if include_all or idx_str in include_set:
                entry["dynamic_prompt"] = st.dynamic_prompt
            if has_multilevel:
                entry["depth"] = st.depth
                entry["parent_node_id"] = st.parent_node_id
                entry["children_ids"] = st.children_ids
                entry["is_leaf"] = st.is_leaf
            subtask_entries.append(entry)

        payload: Dict[str, Any] = {
            "subtasks": subtask_entries,
            "report_policy": self.report_policy,
        }
        return json.dumps(payload, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Island-specific skeleton views for SAE (Serial Alternating Evolution)
    # ------------------------------------------------------------------

    def to_skeleton_overview_topo(self) -> str:
        """L1-Topo: structural overview for the T-island.

        Shows all depth-0 nodes with full goals, dependencies and a
        per-child summary (index, task_type, truncated goal).  Omits
        ``dynamic_prompt`` since the T-island only edits topology.
        """
        from collections import Counter

        children_by_parent: Dict[str, List[Dict[str, Any]]] = {}
        for j, child in enumerate(self.subtasks):
            pid = child.parent_node_id
            if pid is not None:
                children_by_parent.setdefault(pid, []).append({
                    "index": str(j),
                    "task_type": child.task_type.value,
                    "goal": child.goal[:80] + ("..." if len(child.goal) > 80 else ""),
                    "is_leaf": child.is_leaf,
                })

        entries: List[Dict[str, Any]] = []
        for i, st in enumerate(self.subtasks):
            if st.depth > 0:
                continue
            entry: Dict[str, Any] = {
                "index": str(i),
                "goal": st.goal,
                "task_type": st.task_type.value,
                "dependencies": st.dependencies,
                "is_leaf": st.is_leaf,
                "children": children_by_parent.get(str(i), []),
            }
            entries.append(entry)

        return json.dumps(
            {"nodes": entries, "total_nodes": len(self.subtasks),
             "_view": "topo_overview"},
            ensure_ascii=False,
        )

    def to_skeleton_overview_dp(self) -> str:
        """L1-DP: content overview for the DP-island.

        Shows every node with goal text (full for depth-0 / THINK / WRITE,
        truncated for RETRIEVE), prompt presence indicator, prompt first-line
        preview, and sibling-WRITE markers for scope-overlap detection.
        """
        write_depth0 = [
            j for j, s in enumerate(self.subtasks)
            if s.depth == 0 and s.task_type.value == "WRITE"
        ]

        entries: List[Dict[str, Any]] = []
        for i, st in enumerate(self.subtasks):
            needs_full_goal = (
                st.depth == 0
                or st.task_type.value in ("THINK", "WRITE")
            )
            entry: Dict[str, Any] = {
                "index": str(i),
                "goal": st.goal if needs_full_goal
                        else st.goal[:60] + ("..." if len(st.goal) > 60 else ""),
                "task_type": st.task_type.value,
                "depth": st.depth,
                "parent_node_id": st.parent_node_id,
                "has_prompt": bool(st.dynamic_prompt),
            }
            if st.dynamic_prompt:
                first_line = st.dynamic_prompt.split("\n")[0][:80]
                entry["prompt_preview"] = first_line
            if st.task_type.value == "WRITE" and st.depth == 0:
                siblings = [str(j) for j in write_depth0 if j != i]
                if siblings:
                    entry["sibling_write_indices"] = siblings
            entries.append(entry)

        return json.dumps(
            {"nodes": entries, "total_nodes": len(self.subtasks),
             "_view": "dp_overview"},
            ensure_ascii=False,
        )

    def to_skeleton_window(
        self,
        focus_indices: List[int],
        hops: int = 1,
        include_prompts: bool = False,
    ) -> str:
        """L2: focused window around *focus_indices* with N-hop expansion.

        Expands the set of visible nodes by following parent, sibling,
        dependency, and dependent edges for *hops* iterations.  Nodes
        outside the window are listed in ``hidden_indices`` so the LLM
        knows they exist but are not shown in detail.
        """
        relevant: set = set()
        for idx in focus_indices:
            if 0 <= idx < len(self.subtasks):
                relevant.add(idx)

        for _ in range(hops):
            expanded: set = set()
            for idx in relevant:
                st = self.subtasks[idx]
                if st.parent_node_id is not None:
                    try:
                        p = int(st.parent_node_id)
                        expanded.add(p)
                        for j, other in enumerate(self.subtasks):
                            if other.parent_node_id == st.parent_node_id:
                                expanded.add(j)
                    except (ValueError, TypeError):
                        pass
                for dep in st.dependencies:
                    if dep.isdigit():
                        expanded.add(int(dep))
                for j, other in enumerate(self.subtasks):
                    if str(idx) in other.dependencies:
                        expanded.add(j)
            relevant |= {x for x in expanded if 0 <= x < len(self.subtasks)}

        entries: List[Dict[str, Any]] = []
        for i, st in enumerate(self.subtasks):
            if i not in relevant:
                continue
            entry: Dict[str, Any] = {
                "index": str(i),
                "goal": st.goal,
                "task_type": st.task_type.value,
                "dependencies": st.dependencies,
                "depth": st.depth,
                "parent_node_id": st.parent_node_id,
                "is_leaf": st.is_leaf,
            }
            if include_prompts and st.dynamic_prompt:
                entry["dynamic_prompt"] = st.dynamic_prompt
            entries.append(entry)

        hidden = [i for i in range(len(self.subtasks)) if i not in relevant]
        return json.dumps(
            {"window_nodes": entries, "hidden_indices": hidden,
             "_note": "Nodes not in window are hidden. "
                      "You may reference them by index."},
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    # Edit-Script Application — DP island (PromptEdit)
    # ------------------------------------------------------------------

    def apply_prompt_edits(
        self,
        edits: List[Any],
        island_id: str = "dynamic_prompt",
        mutation_log: str = "",
    ) -> "EvolutionarySolution":
        """Deterministically apply DP-island anchor-section prompt edits.

        Guarantees:
          - Only the ``dynamic_prompt`` field of targeted nodes is changed.
          - All other fields (goal, task_type, deps, hierarchy) are untouched.
          - Invalid node indices are skipped with a warning.
          - Each edit targets exactly one anchor section (or does a full
            replacement if the anchor is not found in the current prompt).

        Args:
            edits: List of PromptEdit objects with fields:
                ``node_index``, ``op`` (PromptEditOp), ``section_content``,
                ``source_node`` (optional, for transfer_anchor).
            island_id: Island tag for lineage tracking.
            mutation_log: Human-readable description of what changed.

        Returns:
            A new EvolutionarySolution with edits applied.
        """
        new_subtasks = [st.model_copy() for st in self.subtasks]
        applied = 0

        for edit in edits:
            try:
                idx = int(edit.node_index)
            except (ValueError, TypeError, AttributeError):
                logger.warning(
                    f"[EDIT-APPLY] Invalid node_index '{getattr(edit, 'node_index', '?')}', skipping"
                )
                continue
            if not (0 <= idx < len(new_subtasks)):
                logger.warning(
                    f"[EDIT-APPLY] node_index {idx} out of range [0, {len(new_subtasks)}), skipping"
                )
                continue

            op = getattr(edit, "op", None)
            section_content = getattr(edit, "section_content", None)

            if op is None or section_content is None:
                logger.warning(
                    f"[EDIT-APPLY] PromptEdit for node {idx} missing op/section_content, skipping"
                )
                continue

            op_str = op.value if hasattr(op, "value") else str(op)

            if op_str == "transfer_anchor":
                source_node = getattr(edit, "source_node", None)
                if source_node is not None:
                    try:
                        src_idx = int(source_node)
                    except (ValueError, TypeError):
                        src_idx = -1
                    if 0 <= src_idx < len(new_subtasks):
                        src_prompt = new_subtasks[src_idx].dynamic_prompt or ""
                        new_subtasks[idx].dynamic_prompt = _apply_anchor_replace(
                            new_subtasks[idx].dynamic_prompt or "",
                            section_content,
                            source_text=src_prompt,
                        )
                    else:
                        logger.warning(
                            f"[EDIT-APPLY] transfer_anchor: invalid source_node '{source_node}', skipping"
                        )
                        continue
                else:
                    new_subtasks[idx].dynamic_prompt = _apply_anchor_replace(
                        new_subtasks[idx].dynamic_prompt or "",
                        section_content,
                    )
            else:
                new_subtasks[idx].dynamic_prompt = _apply_anchor_replace(
                    new_subtasks[idx].dynamic_prompt or "",
                    section_content,
                )
            applied += 1

        logger.info(f"[EDIT-APPLY] Applied {applied}/{len(edits)} prompt edits")

        variant = self.clone_with_mutation(
            subtasks=new_subtasks,
            island_id=island_id,
            mutation_log=mutation_log,
        )
        _set_edit_history(variant, self, edits)
        return variant

    # ------------------------------------------------------------------
    # Edit-Script Application — T island (TopoEdit)
    # ------------------------------------------------------------------

    def apply_topo_edits(
        self,
        edits: List[Any],
        island_id: str = "topology",
        mutation_log: str = "",
    ) -> "EvolutionarySolution":
        """Deterministically apply T-island topology edits.

        Supported atomic operations (via ``edit.op``):
          - ``split``:        replace target node with 2+ focused nodes
          - ``add``:          insert a new node after a specified position
          - ``delete``:       remove a node, rerouting downstream deps
          - ``reorder_deps``: change dependency list for a node

        After all ops are applied, ``_rebuild_hierarchy`` and
        ``_rebuild_dependency_graph`` ensure consistency.

        Insertion-order correction
        --------------------------
        ``_topo_add`` always inserts at ``after_node + 1``, so consecutive
        ``add`` ops sharing the same ``after_node`` end up in reversed order
        in the list.  ``_normalize_add_order`` reverses these groups before
        processing so the first op in the edit list gets the lowest final
        index.

        Symbolic dep resolution
        -----------------------
        An ``add`` op may set ``params.symbolic_id = "some_name"`` on the
        new node.  Sibling nodes can then declare ``deps: ["#some_name"]``
        instead of a numeric index.  After all ops are applied the
        ``"#some_name"`` strings are resolved to the node's actual final
        index by object-identity lookup.
        """
        new_subtasks = [st.model_copy() for st in self.subtasks]
        applied = 0

        # Preprocess: reverse consecutive add groups with the same after_node
        # so that the edit-list order matches the final index order.
        edits = _normalize_add_order(edits)

        # Object-identity snapshot taken before any mutations.  reorder_deps
        # uses this to locate a node by its original index even after preceding
        # delete ops have shifted the live list.
        orig_snapshot = list(new_subtasks)

        # Track (symbolic_id, SubTask object) for post-processing resolution.
        symbolic_nodes: List[tuple] = []

        for edit in edits:
            op = getattr(edit, "op", None)
            if op is None:
                logger.warning("[TOPO-EDIT] Edit missing 'op', skipping")
                continue
            op_str = op.value if hasattr(op, "value") else str(op)
            params = getattr(edit, "params", None)
            target = getattr(edit, "target_node", "0")

            try:
                if op_str == "delete":
                    new_subtasks = _topo_delete(new_subtasks, target)
                elif op_str == "add":
                    new_subtasks, new_st = _topo_add(new_subtasks, params)
                    if new_st is not None:
                        sym = getattr(params, "symbolic_id", None)
                        if sym:
                            symbolic_nodes.append((sym, new_st))
                elif op_str == "split":
                    new_subtasks = _topo_split(new_subtasks, target, params)
                elif op_str == "reorder_deps":
                    new_subtasks = _topo_reorder_deps(
                        new_subtasks, target, params, orig_snapshot
                    )
                else:
                    logger.warning(f"[TOPO-EDIT] Unknown op '{op_str}', skipping")
                    continue
                applied += 1
            except Exception as e:
                logger.warning(f"[TOPO-EDIT] Failed to apply {op_str} on node {target}: {e}")

        # Resolve "#symbol" dep references to actual numeric indices.
        # We use object identity to find each symbolic node's final position
        # so the lookup is immune to all index remappings done during the loop.
        if symbolic_nodes:
            symbol_to_index: Dict[str, str] = {}
            for sym_id, st_ref in symbolic_nodes:
                for i, st in enumerate(new_subtasks):
                    if st is st_ref:
                        symbol_to_index[sym_id] = str(i)
                        break
            if symbol_to_index:
                logger.info(f"[TOPO-EDIT] Resolved symbolic deps: {symbol_to_index}")
                for st in new_subtasks:
                    st.dependencies = [
                        symbol_to_index.get(d[1:], d) if d.startswith("#") else d
                        for d in st.dependencies
                    ]

        _rebuild_hierarchy(new_subtasks)
        deps_graph = _rebuild_dependency_graph(new_subtasks)
        new_subtasks, deps_graph = _topo_sort_subtasks(new_subtasks, deps_graph)

        logger.info(f"[TOPO-EDIT] Applied {applied}/{len(edits)} topo edits, {len(new_subtasks)} nodes")

        variant = self.clone_with_mutation(
            subtasks=new_subtasks,
            dependencies_graph=deps_graph,
            island_id=island_id,
            mutation_log=mutation_log,
        )
        _set_edit_history(variant, self, edits)
        return variant


# =====================================================================
# Edit-history tracking (memory-only, not serialized)
# =====================================================================

def _set_edit_history(
    variant: EvolutionarySolution,
    parent: EvolutionarySolution,
    latest_edits: List[Any],
) -> None:
    """Attach edit-history metadata to *variant* for downstream consumers.

    ``_edit_history``  — cumulative edits from the original seed (P0)
    ``_latest_edits``  — only the edits applied in this step

    These are used by:
      - Judge: format edit-log for tournament comparison
      - Assembler: programmatic orthogonal merge
      - Logger: incremental evolution trace
    """
    parent_history: list = getattr(parent, "_edit_history", [])
    variant._edit_history = parent_history + list(latest_edits)  # type: ignore[attr-defined]
    variant._latest_edits = list(latest_edits)  # type: ignore[attr-defined]


# Judge receives the full dynamic_prompt — no truncation.
# The original 120-char hard truncation was incorrect: the Judge evaluates
# anchor completeness (D3b/D3g) and cannot do so without seeing the full text.
# Set to 0 to disable truncation entirely (current default).
JUDGE_PROMPT_PREVIEW_CHARS: int = 0
# Evolution logs can use short previews to avoid huge markdown files.
EVOLUTION_LOG_PROMPT_PREVIEW_CHARS: int = 240

# Anchor section headers used to find natural cut points within a prompt.
_ANCHOR_HEADERS = (
    "WHO & HOW", "MUST COVER", "EVIDENCE SPEC", "DEPTH BUDGET",
    "STRICTLY AVOID", "WHAT TO FIND", "SOURCE PRIORITY", "EXTRACTION SCHEMA",
    "ANALYTICAL FRAME", "REASONING STEPS", "DEPTH ALLOCATION",
    "ROLE", "TASK", "OUTPUT FORMAT", "CONSTRAINTS",
)


def _find_anchor_span(prompt: str, header_names: List[str]) -> Optional[tuple]:
    """Find the start/end character positions of the first matching anchor section.

    Handles both ``**HEADER:**`` (bold) and plain ``HEADER:`` formatting.
    The section extends from the start of its header to just before the next
    known anchor header (or the end of the prompt).

    Returns ``(start, end)`` or ``None`` if no matching header is found.
    """
    lines = prompt.split("\n")
    section_starts: List[tuple] = []

    char_pos = 0
    for line in lines:
        stripped = line.strip()
        for header in _ANCHOR_HEADERS:
            if (
                stripped.startswith(f"**{header}") or
                stripped.startswith(f"{header}:")
            ):
                section_starts.append((char_pos, header))
                break
        char_pos += len(line) + 1  # +1 for newline

    # Find the first matching header and the position of the next section
    for i, (pos, hdr) in enumerate(section_starts):
        if hdr in header_names:
            end = section_starts[i + 1][0] if i + 1 < len(section_starts) else len(prompt)
            # Trim trailing whitespace from section end
            while end > pos and prompt[end - 1] in (" ", "\n", "\r"):
                end -= 1
            return pos, end
    return None


def _apply_anchor_replace(
    prompt: str,
    section_content: str,
    source_text: Optional[str] = None,
) -> str:
    """Replace or append an anchor section in *prompt* with *section_content*.

    The target anchor header is inferred from the first non-empty line of
    *section_content*.  If the header already exists in *prompt*, that section
    (from its header to the next anchor header) is replaced.  If it is absent,
    *section_content* is appended to the end of *prompt*.

    For ``transfer_anchor`` ops, *source_text* is provided but the
    replacement logic is identical — the caller has already extracted the
    relevant section into *section_content*.
    """
    if not section_content:
        return prompt

    # Infer target anchor from the first line of section_content
    first_line = section_content.strip().split("\n")[0].strip()
    matched_header: Optional[str] = None
    for header in _ANCHOR_HEADERS:
        if (
            first_line.startswith(f"**{header}") or
            first_line.startswith(f"{header}:")
        ):
            matched_header = header
            break

    if matched_header is None:
        # Cannot determine anchor — append as a new section
        logger.warning(
            "[ANCHOR-REPLACE] Could not identify anchor header in section_content; appending."
        )
        return prompt.rstrip() + "\n\n" + section_content.strip()

    span = _find_anchor_span(prompt, [matched_header])
    if span is None:
        # Header not present — append new section
        return prompt.rstrip() + "\n\n" + section_content.strip()

    start, end = span
    before = prompt[:start].rstrip()
    after = prompt[end:].lstrip("\n")
    if after:
        return before + "\n" + section_content.strip() + "\n\n" + after
    return before + "\n" + section_content.strip()


def _extract_anchor_summary(prompt: str, max_chars: int) -> tuple:
    """Return (preview_text, input_mode) for judge consumption.

    Tries to cut at a natural anchor-section boundary rather than mid-sentence,
    so the judge receives complete sections even in truncated output.
    """
    if max_chars <= 0 or len(prompt) <= max_chars:
        return prompt, "full"

    cut = prompt[:max_chars]
    # Scan for the last anchor header that starts within the cut window and is
    # at least 60% into it (so we don't cut too early).
    last_anchor_pos = 0
    for header in _ANCHOR_HEADERS:
        pos = cut.rfind(header + ":")
        if pos == -1:
            pos = cut.rfind("**" + header)
        if pos > last_anchor_pos:
            last_anchor_pos = pos

    if last_anchor_pos > int(max_chars * 0.6):
        cut = prompt[:last_anchor_pos].rstrip()
        return cut + "\n[...truncated at anchor boundary...]", "truncated_anchor_cut"

    return cut + "...", "truncated"


def format_edit_log(
    edits: List[Any],
    prompt_preview_chars: int = JUDGE_PROMPT_PREVIEW_CHARS,
    include_input_mode: bool = True,
) -> str:
    """Render a list of edits as a human-readable log block.

    Works with both PromptEdit and TopoEdit objects.

    Args:
        prompt_preview_chars: Max chars to show for new_dynamic_prompt.
            Defaults to ``JUDGE_PROMPT_PREVIEW_CHARS`` (0 = full prompt).
            Larger values give the Judge more anchor
            signal (D3b/D3g evaluation), at the cost of extra tokens.
        include_input_mode: Whether to include the ``judge_input_mode=...``
            tag in output. Keep True for judge-facing payloads.
    """
    if not edits:
        return "(no edits)"

    lines: List[str] = []
    for edit in edits:
        if hasattr(edit, "section_content"):
            # New PromptEdit (anchor-section op)
            op_str = (
                edit.op.value if hasattr(edit.op, "value") else str(edit.op)
                if hasattr(edit, "op") else "unknown"
            )
            content_preview, input_mode = _extract_anchor_summary(
                edit.section_content, prompt_preview_chars
            )
            mode_prefix = (
                f"  [judge_input_mode={input_mode}] Section content:\n"
                if include_input_mode
                else "  Section content:\n"
            )
            lines.append(
                f"- Node {edit.node_index}: {op_str} — {edit.rationale}\n"
                f"{mode_prefix}{content_preview}"
            )
        elif hasattr(edit, "new_dynamic_prompt"):
            # Legacy PromptEdit (full prompt replacement)
            prompt_preview, input_mode = _extract_anchor_summary(
                edit.new_dynamic_prompt, prompt_preview_chars
            )
            mode_prefix = (
                f"  [judge_input_mode={input_mode}] New prompt:\n"
                if include_input_mode
                else "  New prompt:\n"
            )
            lines.append(
                f"- Node {edit.node_index}: prompt rewrite — {edit.rationale}\n"
                f"{mode_prefix}{prompt_preview}"
            )
        elif hasattr(edit, "op") and hasattr(edit, "target_node"):
            # TopoEdit
            op_str = edit.op.value if hasattr(edit.op, "value") else str(edit.op)
            lines.append(
                f"- Node {edit.target_node}: {op_str} — {edit.rationale}"
            )
        else:
            lines.append(f"- {edit}")
    return "\n".join(lines)


# =====================================================================
# Topology edit operation helpers
# =====================================================================


def _remap_deps(subtasks: List[SubTask], old_to_new: Dict[str, str]) -> None:
    """Remap all dependency references according to old_to_new mapping."""
    for st in subtasks:
        st.dependencies = [
            old_to_new.get(d, d) for d in st.dependencies
            if old_to_new.get(d, d) is not None
        ]


def _remap_hierarchy(subtasks: List[SubTask], old_to_new: Dict[str, str]) -> None:
    """Remap children_ids and parent_node_id according to old_to_new mapping.

    Must be called after every topology mutation (alongside _remap_deps) to keep
    the parent-child hierarchy consistent with the updated index layout.  Without
    this call, _build_prebuilt_subtasks() reads stale children_ids and packs the
    wrong subtasks under each parent key, silently corrupting the blueprint.
    """
    for st in subtasks:
        # Remove references to deleted nodes; update shifted indices.
        st.children_ids = [
            old_to_new[c] for c in st.children_ids
            if c in old_to_new and old_to_new[c] is not None
        ]
        # Remap parent pointer — if the parent was deleted, orphan the node.
        if st.parent_node_id is not None:
            mapped = old_to_new.get(st.parent_node_id)
            if mapped is None:
                st.parent_node_id = None
            else:
                st.parent_node_id = mapped


def _collect_descendants(subtasks: List[SubTask], root_idx: int) -> List[int]:
    """Return indices of all descendants of *root_idx* in BFS order.

    A descendant is any node whose ``parent_node_id`` chain leads back to
    *root_idx*.  We use the string-index convention throughout.
    """
    root_str = str(root_idx)
    # Build a parent→children map for fast lookup.
    children_of: Dict[str, List[int]] = {}
    for i, st in enumerate(subtasks):
        if st.parent_node_id is not None:
            children_of.setdefault(st.parent_node_id, []).append(i)

    result: List[int] = []
    queue = list(children_of.get(root_str, []))
    while queue:
        child = queue.pop()
        result.append(child)
        queue.extend(children_of.get(str(child), []))
    return result


def _topo_delete(subtasks: List[SubTask], target: str) -> List[SubTask]:
    """Remove node at *target* index and remap all references.

    Only the target node itself is removed.  Descendants (if any) are NOT
    cascade-deleted — their stale ``parent_node_id`` will be detected and
    reset by ``_rebuild_hierarchy`` after all edits are applied, promoting
    them to depth-0 roots.  This keeps delete behaviour predictable: the
    number of nodes removed equals exactly the number of explicit delete
    ops in the edit list, with no hidden side-effects.
    """
    idx = int(target)
    if not (0 <= idx < len(subtasks)):
        logger.warning(f"[TOPO-DELETE] Index {idx} out of range, skipping")
        return subtasks

    current = list(subtasks)
    current = current[:idx] + current[idx + 1:]

    original_len = len(current) + 1
    old_to_new: Dict[str, str] = {}
    for old_i in range(original_len):
        if old_i < idx:
            old_to_new[str(old_i)] = str(old_i)
        elif old_i == idx:
            old_to_new[str(old_i)] = None  # type: ignore[assignment]
        else:
            old_to_new[str(old_i)] = str(old_i - 1)

    _remap_deps(current, old_to_new)
    _remap_hierarchy(current, old_to_new)

    return current


def _normalize_add_order(edits: List[Any]) -> List[Any]:
    """Reverse consecutive ``add`` groups sharing the same ``after_node``.

    ``_topo_add`` inserts at ``after_node + 1`` on every call.  When the
    model emits several ``add`` ops with the same anchor they all land at
    the same slot, pushing every previously-inserted node one position
    further back — producing a fully-reversed order relative to the edit
    list.  Reversing each same-anchor group before processing ensures the
    op that appears *first* in the edit list ends up with the *lowest*
    final index, which is the intuitive and intended behaviour.

    Example (after_node="5", edit list [THINK, W1, W2, W3]):
      Without fix  → [W3(6), W2(7), W1(8), THINK(9)]
      With fix     → [THINK(6), W1(7), W2(8), W3(9)]  ✓
    """
    result: List[Any] = []
    i = 0
    while i < len(edits):
        edit = edits[i]
        op = getattr(edit, "op", None)
        op_str = op.value if hasattr(op, "value") else str(op) if op else ""
        if op_str == "add":
            anchor = getattr(getattr(edit, "params", None), "after_node", None)
            group: List[Any] = []
            j = i
            while j < len(edits):
                e = edits[j]
                e_op = getattr(e, "op", None)
                e_op_str = e_op.value if hasattr(e_op, "value") else str(e_op) if e_op else ""
                e_anchor = getattr(getattr(e, "params", None), "after_node", None)
                if e_op_str == "add" and e_anchor == anchor:
                    group.append(e)
                    j += 1
                else:
                    break
            result.extend(reversed(group))
            i = j
        else:
            result.append(edit)
            i += 1
    return result


def _topo_add(subtasks: List[SubTask], params: Any) -> tuple:
    """Insert a new node. Position determined by params.after_node.

    Returns:
        (new_list, new_st) — the updated subtask list and the newly created
        SubTask object.  The caller can use ``new_st`` as an object-identity
        anchor to resolve symbolic deps after all ops have been applied
        (the anchor survives all subsequent index remappings).
    """
    from roma_dspy.types.task_type import TaskType

    if params is None:
        logger.warning("[TOPO-ADD] No params provided, skipping")
        return subtasks, None

    goal = getattr(params, "goal", None) or "New subtask"
    task_type_str = getattr(params, "task_type", None) or "THINK"
    after_node = getattr(params, "after_node", None)
    deps = getattr(params, "deps", None) or []
    dynamic_prompt = getattr(params, "dynamic_prompt", None)

    try:
        task_type = TaskType(task_type_str)
    except (ValueError, KeyError):
        task_type = TaskType.THINK

    ref_depth = 0
    ref_parent = None
    if after_node is not None:
        try:
            ref_idx = int(after_node)
            if 0 <= ref_idx < len(subtasks):
                ref_depth = subtasks[ref_idx].depth
                ref_parent = subtasks[ref_idx].parent_node_id
        except (ValueError, TypeError):
            pass

    # Allow the caller to override depth/parent explicitly.
    # This is required for expand_write_chapters: inserting a THINK or WRITE
    # *under* a WRITE parent node must use depth=parent.depth+1 and
    # parent_node_id=parent.index — otherwise _topo_add would create a sibling
    # (same depth, same parent) rather than a child.
    explicit_depth = getattr(params, "depth", None)
    explicit_parent = getattr(params, "parent_node_id", None)
    if explicit_depth is not None:
        try:
            ref_depth = int(explicit_depth)
        except (ValueError, TypeError):
            pass
    if explicit_parent is not None:
        ref_parent = str(explicit_parent)

    new_st = SubTask(
        goal=goal,
        task_type=task_type,
        dependencies=deps,
        dynamic_prompt=dynamic_prompt,
        depth=ref_depth,
        parent_node_id=ref_parent,
    )

    insert_pos = len(subtasks)
    if after_node is not None:
        try:
            insert_pos = int(after_node) + 1
        except (ValueError, TypeError):
            pass

    new_list = subtasks[:insert_pos] + [new_st] + subtasks[insert_pos:]

    old_to_new: Dict[str, str] = {}
    for old_i in range(len(subtasks)):
        new_i = old_i if old_i < insert_pos else old_i + 1
        old_to_new[str(old_i)] = str(new_i)

    # _remap_deps uses old_to_new.get(d, d), so any dep that is not a plain
    # numeric string (e.g. "#symbol" references) passes through unchanged.
    # This means symbolic deps on new_st survive all intermediate remaps safely.
    _remap_deps(new_list, old_to_new)
    _remap_hierarchy(new_list, old_to_new)
    return new_list, new_st


def _topo_split(subtasks: List[SubTask], target: str, params: Any) -> List[SubTask]:
    """Split node at *target* into multiple nodes.

    After splitting node X into [X1, X2, ..., XN]:
    - X1 stays at position *idx* and inherits the original dependencies.
    - X2..XN each depend on X1 (parallel fan-out after X1 completes).
    - Any downstream node D that previously depended on X (now remapped to X1)
      is updated to ALSO depend on X2..XN, so D waits for all split branches
      before proceeding.  Without this update D can start before X2..XN finish,
      violating the intended sequential ordering (e.g. THINK synthesising before
      the second RETRIEVE branch completes).
    """
    from roma_dspy.types.task_type import TaskType

    idx = int(target)
    if not (0 <= idx < len(subtasks)):
        return subtasks

    new_goals = getattr(params, "new_goals", None) or []
    new_types = getattr(params, "new_types", None) or []
    if len(new_goals) < 2:
        logger.warning("[TOPO-SPLIT] Need at least 2 new_goals, skipping")
        return subtasks

    original = subtasks[idx]

    # Per-child prompts (B-solution, preferred).  Validate length when present.
    dynamic_prompts_list = getattr(params, "dynamic_prompts", None) or []
    if dynamic_prompts_list and len(dynamic_prompts_list) != len(new_goals):
        logger.warning(
            f"[TOPO-SPLIT] dynamic_prompts length ({len(dynamic_prompts_list)}) "
            f"!= new_goals length ({len(new_goals)}) — treating as invalid edit, skipping"
        )
        return subtasks

    # Fallback to legacy single-prompt field or parent's prompt.
    params_prompt = getattr(params, "dynamic_prompt", None)
    base_prompt = params_prompt or original.dynamic_prompt

    # --- Parallelism decision (auto mode) ---
    # explicit parallel_split overrides auto logic; None triggers auto.
    requested_parallel = getattr(params, "parallel_split", None)
    if requested_parallel is None:
        # depth>=1 nodes (RETRIEVE/WRITE children) always run in parallel.
        # depth-0 RETRIEVE and WRITE sibling nodes also run in parallel —
        # their downstream THINK/WRITE will be updated by the propagation pass.
        # depth-0 THINK nodes use legacy chain to preserve outline ordering.
        if original.depth >= 1:
            effective_parallel = True
        elif original.task_type in (TaskType.RETRIEVE, TaskType.WRITE):
            effective_parallel = True
        else:
            effective_parallel = False
    else:
        effective_parallel = bool(requested_parallel)

    split_nodes: List[SubTask] = []
    for gi, g in enumerate(new_goals):
        tt_str = new_types[gi] if gi < len(new_types) else original.task_type.value
        try:
            tt = TaskType(tt_str)
        except (ValueError, KeyError):
            tt = original.task_type
        # Use per-child prompt when available (B-solution); fall back to base.
        child_prompt = (
            dynamic_prompts_list[gi] if dynamic_prompts_list else base_prompt
        )
        # All children share original deps when parallel; else children 2..N
        # depend on child 1 (legacy chain).
        if effective_parallel:
            child_deps = list(original.dependencies)
        else:
            child_deps = list(original.dependencies) if gi == 0 else [str(idx)]
        split_nodes.append(SubTask(
            goal=g,
            task_type=tt,
            dependencies=child_deps,
            dynamic_prompt=child_prompt,
            depth=original.depth,
            parent_node_id=original.parent_node_id,
        ))

    new_list = subtasks[:idx] + split_nodes + subtasks[idx + 1:]

    old_to_new: Dict[str, str] = {}
    offset = len(split_nodes) - 1
    for old_i in range(len(subtasks)):
        if old_i < idx:
            old_to_new[str(old_i)] = str(old_i)
        elif old_i == idx:
            old_to_new[str(old_i)] = str(idx)
        else:
            old_to_new[str(old_i)] = str(old_i + offset)

    _remap_deps(new_list, old_to_new)
    _remap_hierarchy(new_list, old_to_new)

    # --- Downstream dependency propagation (Bug fix) ---
    # After _remap_deps, any node D that used to depend on the original X now
    # depends on X1 (= str(idx)).  But X2..XN (positions idx+1 .. idx+n-1) run
    # in parallel after X1 and are invisible to D.  Add them explicitly so D
    # waits for all split branches.
    if len(split_nodes) >= 2:
        split_range = set(range(idx, idx + len(split_nodes)))
        extra_deps = [str(idx + k) for k in range(1, len(split_nodes))]
        updated = 0
        for pos, st in enumerate(new_list):
            if pos in split_range:
                continue
            if str(idx) in st.dependencies:
                added = [d for d in extra_deps if d not in st.dependencies]
                if added:
                    st.dependencies.extend(added)
                    updated += 1
        if updated:
            logger.info(
                f"[TOPO-SPLIT] Propagated split deps to {updated} downstream "
                f"node(s): added {extra_deps} alongside split_first={idx}"
            )

    logger.info(
        f"[TOPO-SPLIT] Split node {idx} into {len(split_nodes)} nodes "
        f"(parallel={effective_parallel})"
    )
    return new_list


def _topo_reorder_deps(
    subtasks: List[SubTask],
    target: str,
    params: Any,
    orig_snapshot: Optional[List[SubTask]] = None,
) -> List[SubTask]:
    """Replace dependency list for node at *target*.

    Uses object-identity lookup against *orig_snapshot* to find the node's
    current position after preceding delete operations may have shifted indices.
    Falls back to a direct index lookup when no snapshot is provided.
    """
    new_deps = getattr(params, "new_deps", None)
    if new_deps is None:
        return subtasks

    orig_idx = int(target)
    current_idx: Optional[int] = None

    if orig_snapshot is not None and orig_idx < len(orig_snapshot):
        target_obj = orig_snapshot[orig_idx]
        current_idx = next(
            (i for i, s in enumerate(subtasks) if s is target_obj), None
        )
        if current_idx is None:
            logger.warning(
                f"[TOPO-EDIT] reorder_deps target={target}: "
                f"node was deleted in this batch, skipping"
            )
            return subtasks
    elif 0 <= orig_idx < len(subtasks):
        current_idx = orig_idx
    else:
        logger.warning(
            f"[TOPO-EDIT] reorder_deps target={target}: "
            f"index out of bounds (len={len(subtasks)}), skipping"
        )
        return subtasks

    # Pre-flight cycle check: build an adjacency graph with the proposed
    # new_deps applied and verify it is still a DAG before committing.
    # This prevents cycles from entering the blueprint where they would
    # only be silently dropped one-edge-at-a-time by dag.add_dependencies
    # at execution time, leaving incomplete dependency chains.
    n = len(subtasks)
    proposed_deps_int: List[int] = []
    for d in new_deps:
        try:
            di = int(d)
            if 0 <= di < n and di != current_idx:
                proposed_deps_int.append(di)
        except (ValueError, TypeError):
            pass

    if proposed_deps_int:
        # Adjacency: edge (u -> v) means "u must finish before v starts"
        from collections import defaultdict
        adj: Dict[int, List[int]] = defaultdict(list)
        for i, st in enumerate(subtasks):
            if i == current_idx:
                continue
            for d in st.dependencies:
                try:
                    di = int(d)
                    if 0 <= di < n:
                        adj[di].append(i)
                except (ValueError, TypeError):
                    pass
        for di in proposed_deps_int:
            adj[di].append(current_idx)

        # Iterative DFS cycle detection (avoids recursion limit on deep plans)
        WHITE, GRAY, BLACK = 0, 1, 2
        color = [WHITE] * n

        def _has_cycle() -> bool:
            for start in range(n):
                if color[start] != WHITE:
                    continue
                stack = [(start, False)]
                while stack:
                    node, leaving = stack.pop()
                    if leaving:
                        color[node] = BLACK
                        continue
                    if color[node] == GRAY:
                        return True
                    color[node] = GRAY
                    stack.append((node, True))
                    for nb in adj[node]:
                        if color[nb] == WHITE:
                            stack.append((nb, False))
                        elif color[nb] == GRAY:
                            return True
            return False

        if _has_cycle():
            logger.warning(
                f"[TOPO-EDIT] reorder_deps: orig={target} → current={current_idx} "
                f"with new_deps={new_deps} would create a dependency cycle — "
                f"skipping to preserve DAG integrity"
            )
            return subtasks

    # Deduplicate while preserving order — LLM may emit the same index twice.
    deduped_deps = list(dict.fromkeys(str(d) for d in new_deps))
    subtasks[current_idx].dependencies = deduped_deps
    logger.info(
        f"[TOPO-EDIT] reorder_deps: orig={target} → current={current_idx}, "
        f"new_deps={deduped_deps}"
    )
    return subtasks


# =====================================================================
# Topological sort (Kahn's algorithm) — ensures index order matches DAG
# =====================================================================


def _topo_sort_subtasks(
    subtasks: List[SubTask],
    deps_graph: Dict[str, List[str]],
) -> tuple:
    """Re-order subtasks so every node's index is lower than all nodes that
    depend on it (i.e. dependency index < dependent index).

    Uses Kahn's algorithm on the existing deps_graph.  If a cycle is detected
    the original order is returned unchanged with a warning.

    Returns:
        (sorted_subtasks, updated_deps_graph) with remapped string indices.
    """
    n = len(subtasks)
    if n == 0:
        return subtasks, deps_graph

    # Build adjacency: edge means "A must come before B" (A ∈ deps of B)
    # in_degree[i] = number of dependencies for node i
    in_degree: List[int] = [0] * n
    dependents: List[List[int]] = [[] for _ in range(n)]  # dependents[dep] → list of nodes that need dep

    for node_str, dep_list in deps_graph.items():
        try:
            node_idx = int(node_str)
        except (ValueError, TypeError):
            continue
        if not (0 <= node_idx < n):
            continue
        for dep_str in dep_list:
            try:
                dep_idx = int(dep_str)
            except (ValueError, TypeError):
                continue
            if 0 <= dep_idx < n:
                in_degree[node_idx] += 1
                dependents[dep_idx].append(node_idx)

    # Kahn's BFS: start with all nodes that have no dependencies
    from collections import deque
    queue: deque = deque()
    for i in range(n):
        if in_degree[i] == 0:
            queue.append(i)

    topo_order: List[int] = []
    while queue:
        node = queue.popleft()
        topo_order.append(node)
        for dependent in dependents[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(topo_order) != n:
        # Cycle detected — return unchanged to avoid silent data corruption
        logger.warning(
            f"[TOPO-SORT] Cycle detected in DAG ({n - len(topo_order)} nodes "
            f"unreachable). Skipping topological reorder."
        )
        return subtasks, deps_graph

    # Check if already in correct order — skip remapping to save work
    if topo_order == list(range(n)):
        return subtasks, deps_graph

    # Build old_index → new_index mapping
    old_to_new: Dict[str, str] = {str(old_idx): str(new_idx) for new_idx, old_idx in enumerate(topo_order)}

    # Reorder subtasks
    sorted_subtasks = [subtasks[old_idx] for old_idx in topo_order]

    # Remap dependencies in subtasks
    _remap_deps(sorted_subtasks, old_to_new)

    # Remap parent_node_id and children_ids in hierarchy fields
    for st in sorted_subtasks:
        if st.parent_node_id is not None:
            st.parent_node_id = old_to_new.get(st.parent_node_id, st.parent_node_id)
        st.children_ids = [old_to_new.get(c, c) for c in st.children_ids]

    # Rebuild deps_graph with new indices
    new_deps_graph: Dict[str, List[str]] = {}
    for new_idx, old_idx in enumerate(topo_order):
        old_str = str(old_idx)
        if old_str in deps_graph:
            remapped = [old_to_new.get(d, d) for d in deps_graph[old_str] if old_to_new.get(d) is not None]
            if remapped:
                new_deps_graph[str(new_idx)] = remapped

    changed = sum(1 for i, old in enumerate(topo_order) if old != i)
    logger.info(f"[TOPO-SORT] Reordered {changed} node(s) to restore topological index order.")
    return sorted_subtasks, new_deps_graph


# =====================================================================
# Hierarchy rebuild (programmatic, replaces _recover_multilevel_fields)
# =====================================================================


def _rebuild_hierarchy(subtasks: List[SubTask]) -> None:
    """Rebuild children_ids, is_leaf from parent_node_id.

    Called after topo edits to ensure hierarchy consistency.
    Also remaps parent_node_id references that may have shifted.

    Includes a defensive pass to detect orphaned nodes (depth=0,
    parent_node_id=None) whose dependencies reference deeper nodes,
    indicating they were created by a topo edit (split/add) that
    failed to preserve hierarchy fields.  These are re-parented to
    the depth-0 ancestor of their first valid dependency.
    """
    # --- Pass 1: rebuild children_ids / is_leaf from parent_node_id ---
    idx_children: Dict[str, List[str]] = {}
    for i, st in enumerate(subtasks):
        if st.parent_node_id is not None:
            try:
                parent_idx = int(st.parent_node_id)
            except (ValueError, TypeError):
                # Drop malformed parent pointers introduced by noisy edits.
                st.parent_node_id = None
                continue
            if not (0 <= parent_idx < len(subtasks)):
                # Drop stale parent pointers (e.g. after delete/reindex).
                st.parent_node_id = None
                continue
            idx_children.setdefault(str(parent_idx), []).append(str(i))

    for i, st in enumerate(subtasks):
        expected = idx_children.get(str(i), [])
        st.children_ids = expected
        st.is_leaf = len(expected) == 0

    # --- Pass 2: detect & repair orphaned nodes ---
    # An orphan is depth=0 + parent_node_id=None but depends on a
    # depth>=1 task.  Walk the dependency chain up to find the depth-0
    # ancestor and re-parent under it.
    repaired = 0
    for i, st in enumerate(subtasks):
        if st.depth != 0 or st.parent_node_id is not None:
            continue
        if not st.dependencies:
            continue

        has_deep_dep = False
        best_parent_idx: Optional[int] = None
        for dep_str in st.dependencies:
            try:
                dep_idx = int(dep_str)
            except (ValueError, TypeError):
                continue
            if 0 <= dep_idx < len(subtasks) and subtasks[dep_idx].depth >= 1:
                has_deep_dep = True
                ancestor = dep_idx
                while 0 <= ancestor < len(subtasks):
                    parent_id = subtasks[ancestor].parent_node_id
                    if parent_id is None:
                        break
                    try:
                        parent_idx = int(parent_id)
                    except (ValueError, TypeError):
                        break
                    if not (0 <= parent_idx < len(subtasks)) or parent_idx == ancestor:
                        break
                    ancestor = parent_idx
                if 0 <= ancestor < len(subtasks) and subtasks[ancestor].depth == 0 and ancestor != i:
                    best_parent_idx = ancestor
                    break

        if has_deep_dep and best_parent_idx is not None:
            st.depth = 1
            st.parent_node_id = str(best_parent_idx)
            repaired += 1

    if repaired:
        logger.info(
            f"[REBUILD-HIERARCHY] Repaired {repaired} orphaned nodes "
            f"(re-parented under depth-0 ancestors)"
        )
        idx_children.clear()
        for i, st in enumerate(subtasks):
            if st.parent_node_id is not None:
                idx_children.setdefault(st.parent_node_id, []).append(str(i))
        for i, st in enumerate(subtasks):
            expected = idx_children.get(str(i), [])
            st.children_ids = expected
            st.is_leaf = len(expected) == 0


def _rebuild_dependency_graph(subtasks: List[SubTask]) -> Dict[str, List[str]]:
    """Rebuild top-level dependencies_graph from per-node dependencies."""
    graph: Dict[str, List[str]] = {}
    for i, st in enumerate(subtasks):
        if st.dependencies:
            valid_deps = [
                d for d in st.dependencies
                if d.lstrip("-").isdigit() and 0 <= int(d) < len(subtasks)
            ]
            # Deduplicate while preserving order to guard against LLM-introduced
            # duplicate dependency entries (e.g. [4, 4] from reorder_deps).
            if valid_deps:
                deduped = list(dict.fromkeys(valid_deps))
                graph[str(i)] = deduped
                st.dependencies = deduped
            else:
                st.dependencies = []
    return graph
