"""PlanBlueprint — elastic planning skeleton produced by MIPE warm-up evolution.

A PlanBlueprint is NOT a frozen plan. It specifies:
  - Rigid constraints  (runtime MUST NOT violate)
  - Elastic constraints (runtime MAY adjust within bounds)
  - Evolution knowledge (reference material for runtime decisions)

The warm-up evolution decides the "strategy framework"; runtime decides
the "specific content" by filling placeholders and adjusting within
elastic bounds based on actual retrieval results.

Key helpers for subsequent phases:
  - validate_internal_consistency()   — Phase 1: verify assembled blueprint before storing
  - get_directive_template(node_id)   — Phase 2: retrieve template for a node during runtime
  - to_planner_context()              — Phase 2: inject blueprint as "strong reference" into
                                        runtime Planner context
  - model_dump() / model_validate()   — Phase 4: JSON serialization for Archive storage
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from roma_dspy.types.task_type import TaskType

if TYPE_CHECKING:
    from roma_dspy.core.signatures.base_models.subtask import SubTask


# =====================================================================
# Mandate Checklist — Structured per-WRITE constraint channel
# =====================================================================


class MandateItem(BaseModel):
    """A single hard constraint extracted from the user query.

    Two kinds:
    - ``verbatim``: A string that MUST appear character-for-character in the
      WRITE output (e.g. exact table titles, column names, row labels, named
      entities, key numeric values).
    - ``dimension``: An analytical dimension that MUST have at least one
      dedicated paragraph addressing it (e.g. "缴费年限", "因果链推导").
    """

    kind: Literal["verbatim", "dimension"] = Field(
        ..., description="'verbatim' = must appear literally; 'dimension' = must have dedicated coverage"
    )
    id: str = Field(
        ..., description="Short unique identifier, e.g. V1, V2, D1, D2"
    )
    content: str = Field(
        ..., description="The exact string (verbatim) or dimension name (dimension)"
    )
    source_quote: Optional[str] = Field(
        default=None,
        description="The raw query fragment from which this item was extracted",
    )
    applies_to_heading: Optional[str] = Field(
        default=None,
        description="heading_path of the section this item belongs to (from Heading Skeleton)",
    )


class MandateChecklist(BaseModel):
    """Collection of MandateItems for one WRITE section.

    Produced by the Outline THINK executor and attached to each WRITE
    SubtaskSpec by the Planner. The WRITE executor reads this from
    ``<mandate_checklist>`` in its XML context and self-checks every item
    before calling finish.
    """

    items: List[MandateItem] = Field(
        default_factory=list,
        description="All verbatim and dimension constraints for this section",
    )

    def to_xml(self) -> str:
        """Serialize to ``<mandate_checklist>`` XML block for context injection."""
        if not self.items:
            return ""
        lines = ["<mandate_checklist>"]
        for item in self.items:
            tag = item.kind
            attrs = f'id="{item.id}"'
            if item.applies_to_heading:
                escaped = item.applies_to_heading.replace('"', "&quot;")
                attrs += f' applies_to="{escaped}"'
            lines.append(f"  <{tag} {attrs}>")
            lines.append(f"    {item.content}")
            if item.source_quote:
                lines.append(f"    <!-- source: {item.source_quote[:120]} -->")
            lines.append(f"  </{tag}>")
        lines.append("</mandate_checklist>")
        return "\n".join(lines)


# =====================================================================
# Supporting Models
# =====================================================================


class NodeRigidity(str, Enum):
    """Whether a DAG node is rigid (immovable) or flexible."""
    RIGID = "rigid"           # Cannot be removed or reordered
    FLEXIBLE = "flexible"     # Can be adjusted, merged, or skipped by runtime
    INSERTION_POINT = "insertion_point"  # Placeholder where runtime can add nodes


class SkeletonNode(BaseModel):
    """A node in the DAG topology skeleton.

    Core nodes are rigid (must appear in the final plan).
    Flexible nodes can be adjusted based on actual retrieval results.
    Insertion points mark where runtime may add supplementary nodes.
    """

    node_id: str = Field(
        ..., description="Unique identifier (0-based index as string)"
    )
    task_type: TaskType = Field(
        ..., description="Expected task type for this node"
    )
    title: str = Field(
        ..., description="Short title describing the node's role"
    )
    rigidity: NodeRigidity = Field(
        default=NodeRigidity.RIGID,
        description="Whether this node is rigid, flexible, or an insertion point",
    )
    scope_summary: Optional[str] = Field(
        default=None,
        description="Brief description of what this node should cover",
    )


class DAGSkeleton(BaseModel):
    """Topology skeleton of the execution DAG.

    Defines core nodes that must appear and flexible nodes that can be
    adjusted. The dependencies structure specifies required edges
    (rigid) and optional edges (flexible).
    """

    nodes: List[SkeletonNode] = Field(
        ..., description="Ordered list of skeleton nodes"
    )
    rigid_dependencies: Dict[str, List[str]] = Field(
        default_factory=dict,
        description=(
            "Dependencies that MUST be preserved in the final plan. "
            "Keys are node IDs, values are lists of dependency node IDs."
        ),
    )
    flexible_dependencies: Dict[str, List[str]] = Field(
        default_factory=dict,
        description=(
            "Dependencies that CAN be adjusted by runtime. "
            "E.g., chapter chain dependencies may be relaxed if chapters "
            "are truly independent."
        ),
    )
    min_nodes: int = Field(
        default=8,
        description="Minimum number of nodes in the final plan",
    )
    max_nodes: int = Field(
        default=14,
        description="Maximum number of nodes in the final plan",
    )
    max_insertion_nodes: int = Field(
        default=2,
        description="Maximum supplementary nodes runtime may insert",
    )


class DirectiveTemplate(BaseModel):
    """A directive template with placeholder tokens for runtime filling.

    Placeholders use ``{placeholder_name}`` syntax and are filled at
    runtime based on actual retrieval results.

    Example template:
        "聚焦{evidence_focus}领域的深度分析。采用'先结论后论据'的结构。
         每个论点必须用{evidence_type}类型的证据支撑。
         Strictly_Avoid: 花费篇幅科普{skip_topic}的基础概念。"

    After runtime filling:
        "聚焦【国产AI芯片供应链安全】领域的深度分析。采用'先结论后论据'的结构。
         每个论点必须用【产业数据和政策文件】类型的证据支撑。
         Strictly_Avoid: 花费篇幅科普【半导体制造工艺】的基础概念。"
    """

    template: str = Field(
        ..., description="Directive text with {placeholder} tokens"
    )
    placeholders: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of placeholder names to their descriptions / filling hints. "
            "E.g., {'evidence_focus': 'from RETRIEVE high-frequency themes', "
            "'evidence_type': 'from RETRIEVE material types'}"
        ),
    )
    node_id: Optional[str] = Field(
        default=None,
        description="Which skeleton node this template applies to (None = global)",
    )


class DepthAllocation(BaseModel):
    """Cognitive budget allocation with elastic bounds.

    Instead of a fixed percentage, each node gets a range that runtime
    can adjust based on actual content density.
    """

    node_id: str = Field(..., description="Skeleton node ID")
    target_percentage: float = Field(
        ..., description="Target percentage of total budget (0-100)"
    )
    min_percentage: float = Field(
        ..., description="Minimum allowed percentage"
    )
    max_percentage: float = Field(
        ..., description="Maximum allowed percentage"
    )
    target_words: Optional[int] = Field(
        default=None, description="Target word count for this node"
    )


# =====================================================================
# PlanBlueprint — Top-level Model
# =====================================================================


class PlanBlueprint(BaseModel):
    """Elastic planning skeleton produced by MIPE warm-up evolution.

    Contains three categories of constraints:

    1. Rigid Constraints (runtime MUST NOT violate):
       - topology_skeleton.nodes with rigidity=RIGID
       - topology_skeleton.rigid_dependencies
       - hard_requirements

    2. Elastic Constraints (runtime MAY adjust within bounds):
       - topology_skeleton.flexible_dependencies
       - directive_templates (placeholder filling)
       - topology_skeleton.nodes with rigidity=FLEXIBLE

    3. Evolution Knowledge (reference for runtime decisions):
       - optimization_rationale
       - known_risks
       - fallback_topology (tournament runner-up)
       - parameterized_static_config (winning knob settings)
    """

    # --- Rigid Constraints ---
    topology_skeleton: DAGSkeleton = Field(
        ..., description="DAG topology skeleton with rigid and flexible nodes"
    )
    hard_requirements: List[str] = Field(
        default_factory=list,
        description=(
            "Non-negotiable requirements the plan must satisfy. "
            "E.g., 'Must include at least 3 RETRIEVE tasks', "
            "'Final chapter must contain recommendations'."
        ),
    )
    report_policy: Optional[str] = Field(
        default=None,
        description="Global report strategy (audience, tone, length, etc.)",
    )

    # --- Elastic Constraints ---
    directive_templates: List[DirectiveTemplate] = Field(
        default_factory=list,
        description="Directive templates with placeholders for runtime filling",
    )
    flexible_nodes: List[str] = Field(
        default_factory=list,
        description=(
            "Node IDs whose directives can be adjusted based on actual "
            "retrieval results (subset of topology_skeleton.nodes)"
        ),
    )

    # --- Evolution Knowledge ---
    optimization_rationale: Optional[str] = Field(
        default=None,
        description=(
            "Why this blueprint was selected as the winner. "
            "Includes comparison notes from the MIPE tournament."
        ),
    )
    known_risks: List[str] = Field(
        default_factory=list,
        description=(
            "Risks identified during evolution. E.g., "
            "'Evidence for Chapter 3 may be sparse — consider merging with Chapter 2'."
        ),
    )
    fallback_topology: Optional[DAGSkeleton] = Field(
        default=None,
        description=(
            "Tournament runner-up topology. Can be activated by Sentinel "
            "if the primary topology encounters major issues."
        ),
    )
    parameterized_static_snapshot: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "The Parameterized Static config values that won the evolution. "
            "E.g., {'narrative_mode': 'data_driven', 'audience_level': 'professional'}."
        ),
    )
    generation: int = Field(
        default=0,
        description="Which evolution generation produced this blueprint",
    )
    fitness_score: float = Field(
        default=0.0,
        description="Head-to-head tournament cumulative fitness score",
    )

    # --- Prebuilt Multi-Level Topology ---
    prebuilt_subtasks: Dict[str, List] = Field(
        default_factory=dict,
        description=(
            "Pre-planned subtask lists produced by MIPE Phase 0 recursive "
            "decomposition.  Key = parent node ID (from the skeleton), "
            "value = list of SubTask dicts for that parent's children. "
            "When present, RecursiveSolver skips plan_async for matching "
            "nodes and loads these subtasks directly."
        ),
    )

    # ------------------------------------------------------------------
    # Validation Helpers (Phase 1: blueprint integrity before storage)
    # ------------------------------------------------------------------

    def validate_internal_consistency(self) -> List[str]:
        """Check that all cross-references inside the blueprint are valid.

        Should be called by the MIPE Assembler before storing the final
        blueprint, and optionally by Sentinel at runtime.

        Returns:
            List of issue descriptions (empty list = no issues found).
        """
        issues: List[str] = []
        node_ids = {n.node_id for n in self.topology_skeleton.nodes}

        # directive_templates must reference known nodes (or be global)
        for tmpl in self.directive_templates:
            if tmpl.node_id and tmpl.node_id not in node_ids:
                issues.append(
                    f"directive_templates: unknown node_id '{tmpl.node_id}' "
                    f"(known: {sorted(node_ids)})"
                )

        # flexible_nodes must reference known nodes
        for fnode in self.flexible_nodes:
            if fnode not in node_ids:
                issues.append(
                    f"flexible_nodes: unknown node_id '{fnode}' "
                    f"(known: {sorted(node_ids)})"
                )

        # rigid_dependencies must reference known nodes
        for src, deps in self.topology_skeleton.rigid_dependencies.items():
            if src not in node_ids:
                issues.append(
                    f"rigid_dependencies: source node_id '{src}' not in skeleton"
                )
            for dep in deps:
                if dep not in node_ids:
                    issues.append(
                        f"rigid_dependencies['{src}']: dependency '{dep}' not in skeleton"
                    )

        # node count constraints
        n = len(self.topology_skeleton.nodes)
        if n < self.topology_skeleton.min_nodes:
            issues.append(
                f"topology_skeleton has {n} nodes, below min_nodes={self.topology_skeleton.min_nodes}"
            )
        if n > self.topology_skeleton.max_nodes:
            issues.append(
                f"topology_skeleton has {n} nodes, above max_nodes={self.topology_skeleton.max_nodes}"
            )

        return issues

    # ------------------------------------------------------------------
    # Directive Template Helpers (Phase 2: runtime template filling)
    # ------------------------------------------------------------------

    def get_directive_template(self, node_id: str) -> Optional["DirectiveTemplate"]:
        """Return the directive template for a specific node.

        Lookup priority:
          1. Template whose node_id matches exactly.
          2. Global template (node_id is None) as fallback.

        Returns None if no template is found for the node.
        """
        for tmpl in self.directive_templates:
            if tmpl.node_id == node_id:
                return tmpl
        for tmpl in self.directive_templates:
            if tmpl.node_id is None:
                return tmpl
        return None

    def get_all_placeholders(self) -> Dict[str, List[str]]:
        """Return all placeholder names grouped by node_id.

        Useful for the template_filler to know in advance which
        placeholders need to be resolved from RETRIEVE results.

        Returns:
            Dict mapping node_id (or '__global__' for node_id=None)
            to a list of placeholder names found in that template.
        """
        import re
        _RE = re.compile(r"\{(\w+)\}")
        result: Dict[str, List[str]] = {}
        for tmpl in self.directive_templates:
            key = tmpl.node_id if tmpl.node_id is not None else "__global__"
            result[key] = _RE.findall(tmpl.template)
        return result

    # ------------------------------------------------------------------
    # Planner Context Serialization (Phase 2: runtime Planner injection)
    # ------------------------------------------------------------------

    def to_planner_context(self) -> str:
        """Serialize to a structured text block for injection into Planner context.

        The Planner uses this as a "strong reference" but is not rigidly
        bound — it can make minor adjustments based on actual conditions.
        Includes directive templates so the Planner knows which placeholder
        patterns to work within.
        """
        lines = [
            "## 规划参考（来自优化阶段，请作为强参考但非绝对约束）",
            "以下结构经过多方案对比评估后被选为最优骨架：",
            "",
        ]

        lines.append("### 拓扑骨架")
        for node in self.topology_skeleton.nodes:
            rigidity_label = {
                NodeRigidity.RIGID: "[核心]",
                NodeRigidity.FLEXIBLE: "[可调]",
                NodeRigidity.INSERTION_POINT: "[可插入]",
            }.get(node.rigidity, "")
            lines.append(
                f"  - {rigidity_label} 节点 {node.node_id}: "
                f"{node.task_type.value} — {node.title}"
            )
            if node.scope_summary:
                lines.append(f"    范围: {node.scope_summary}")

        if self.optimization_rationale:
            lines.append("")
            lines.append("### 该结构胜出的原因")
            lines.append(self.optimization_rationale)

        if self.flexible_nodes or self.topology_skeleton.max_insertion_nodes > 0:
            lines.append("")
            lines.append("### 弹性空间")
            lines.append(
                f"- 可以在不改变核心节点的前提下新增最多 "
                f"{self.topology_skeleton.max_insertion_nodes} 个补充节点"
            )
            if self.flexible_nodes:
                lines.append(
                    f"- 以下节点的 directive 可根据实际检索结果调整：{self.flexible_nodes}"
                )

        if self.directive_templates:
            lines.append("")
            lines.append("### Directive 模板（含占位符，运行时填充）")
            lines.append(
                "以下 directive 模板中的 {占位符} 将在检索阶段完成后由实际内容替换："
            )
            for tmpl in self.directive_templates:
                scope = f"节点 {tmpl.node_id}" if tmpl.node_id else "全局"
                lines.append(f"  [{scope}] {tmpl.template}")
                if tmpl.placeholders:
                    for ph, hint in tmpl.placeholders.items():
                        lines.append(f"    · {{{ph}}}: {hint}")

        if self.hard_requirements:
            lines.append("")
            lines.append("### 硬性要求（不可违反）")
            for req in self.hard_requirements:
                lines.append(f"  - {req}")

        if self.known_risks:
            lines.append("")
            lines.append("### 注意事项（演化中识别的风险）")
            for risk in self.known_risks:
                lines.append(f"  - {risk}")

        if self.fallback_topology:
            lines.append("")
            lines.append(
                "### 备用拓扑（锦标赛亚军，若主拓扑遇到重大问题可切换）"
            )
            for node in self.fallback_topology.nodes:
                lines.append(
                    f"  - 节点 {node.node_id}: {node.task_type.value} — {node.title}"
                )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Node-Scoped Context (Phase 2: Sub-Planner injection — ~200 tokens)
    # ------------------------------------------------------------------

    def to_node_scoped_context(self, node_id: str) -> Optional[str]:
        """Return a compact context block scoped to a single skeleton node.

        Designed for Sub-Planner injection at depth >= 1.  Instead of the
        full blueprint (~1700 tokens), the Sub-Planner receives only:

          1. Current node identity (title, scope, rigidity)
          2. Directive template for that node (with placeholder hints)
          3. Direct neighbour summaries (title + scope only)

        This reduces Sub-Planner context overhead by ~88% while still
        giving it enough information to avoid content overlap.

        Returns ``None`` if *node_id* is not found in the skeleton.
        """
        nodes = self.topology_skeleton.nodes
        node_map = {n.node_id: n for n in nodes}
        node = node_map.get(node_id)
        if node is None:
            return None

        rigidity_label = {
            NodeRigidity.RIGID: "[核心]",
            NodeRigidity.FLEXIBLE: "[可调]",
            NodeRigidity.INSERTION_POINT: "[可插入]",
        }.get(node.rigidity, "")

        lines = [
            "## 当前章节的规划约束（来自优化阶段）",
            "",
            "### 你正在处理的章节",
            f"节点 {node.node_id} {rigidity_label}: "
            f"{node.task_type.value} — {node.title}",
        ]
        if node.scope_summary:
            lines.append(f"范围: {node.scope_summary}")

        tmpl = self.get_directive_template(node_id)
        if tmpl:
            lines.append("")
            lines.append("### 执行引导（带占位符，检索完成后填充）")
            lines.append(f'"{tmpl.template}"')
            if tmpl.placeholders:
                for ph, hint in tmpl.placeholders.items():
                    lines.append(f"  · {{{ph}}}: {hint}")

        neighbours = self._get_neighbour_summaries(node_id)
        if neighbours:
            lines.append("")
            lines.append("### 邻居节点概要（避免内容重叠）")
            for direction, nid, title, scope in neighbours:
                entry = f"- 节点 {nid}（{direction}）: {title}"
                if scope:
                    entry += f" — {scope}"
                lines.append(entry)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers for node-scoped context
    # ------------------------------------------------------------------

    def _get_neighbour_summaries(
        self, node_id: str
    ) -> List[Tuple[str, str, str, Optional[str]]]:
        """Return (direction, node_id, title, scope_summary) for direct neighbours.

        "Direct neighbours" = nodes that are immediately before/after in
        the ordered node list, plus any nodes explicitly connected via
        rigid or flexible dependencies.
        """
        nodes = self.topology_skeleton.nodes
        idx_map = {n.node_id: i for i, n in enumerate(nodes)}
        idx = idx_map.get(node_id)
        if idx is None:
            return []

        neighbour_ids: set[str] = set()
        if idx > 0:
            neighbour_ids.add(nodes[idx - 1].node_id)
        if idx < len(nodes) - 1:
            neighbour_ids.add(nodes[idx + 1].node_id)

        all_deps = {
            **self.topology_skeleton.rigid_dependencies,
            **self.topology_skeleton.flexible_dependencies,
        }
        for dep_id in all_deps.get(node_id, []):
            neighbour_ids.add(dep_id)
        for src, deps in all_deps.items():
            if node_id in deps:
                neighbour_ids.add(src)

        neighbour_ids.discard(node_id)

        result: List[Tuple[str, str, str, Optional[str]]] = []
        for nid in sorted(neighbour_ids, key=lambda x: idx_map.get(x, 999)):
            n = {nd.node_id: nd for nd in nodes}.get(nid)
            if n is None:
                continue
            n_idx = idx_map[nid]
            direction = "上游" if n_idx < idx else "下游"
            result.append((direction, nid, n.title, n.scope_summary))

        return result
