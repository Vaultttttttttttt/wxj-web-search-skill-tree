import dspy
from typing import Optional, Dict, List, Any
from roma_dspy.core.signatures.base_models.subtask import SubTask
from roma_dspy.core.signatures.base_models.plan_blueprint import DirectiveTemplate
from roma_dspy.core.signatures.base_models.mutation_patch import PromptEdit, TopoEdit
from roma_dspy.types import NodeType


class AtomizerSignature(dspy.Signature):
    """Signature for task atomization."""

    goal: str = dspy.InputField(description="Task to atomize")
    context: Optional[str] = dspy.InputField(
        default=None, description="Execution context (XML)"
    )
    is_atomic: bool = dspy.OutputField(
        description="True if task can be executed directly"
    )
    node_type: NodeType = dspy.OutputField(
        description="Type of node to process (PLAN or EXECUTE)"
    )


class PlannerSignature(dspy.Signature):
    """
    Planner decomposition result.

    Contains the breakdown of a complex task into executable subtasks,
    plus per-agent dynamic directives for adaptive Deep Research.
    """

    goal: str = dspy.InputField(
        description="Task that needs to be decomposed into subtasks through planner"
    )
    parent_task_type: Optional[str] = dspy.InputField(
        default=None,
        description=(
            "The task_type of the node currently being decomposed. "
            "This is a HARD CONSTRAINT on which child task_types are allowed: "
            "• RETRIEVE → children must be RETRIEVE or THINK only (no WRITE). "
            "• THINK   → children must be THINK or RETRIEVE only (no WRITE). "
            "• WRITE   → children must be THINK or WRITE only (no RETRIEVE). "
            "• None (root query) → any combination is permitted. "
            "Violating this rule produces an invalid plan."
        ),
    )
    directive: Optional[str] = dspy.InputField(
        default=None,
        description=(
            "Task-specific directive bundle for this planning step. This is a "
            "separate, high-priority input channel for dynamic directives "
            "(for example user-confirmed, warm-up, or local planner refinements), "
            "kept separate from the general evidence context."
        ),
    )
    context: Optional[str] = dspy.InputField(
        default=None, description="Execution context (XML)"
    )
    subtasks: List[SubTask] = dspy.OutputField(
        description="List of generated subtasks from planner"
    )
    dependencies_graph: Optional[Dict[str, List[str]]] = dspy.OutputField(
        default=None,
        description="Task dependency mapping. Keys are subtask indices as strings (e.g., '0', '1'), values are lists of dependency indices as strings. Example: {'1': ['0'], '2': ['0', '1']}",
    )

    # Global report strategy (unified planner output)
    report_policy: Optional[str] = dspy.OutputField(
        default=None,
        description=(
            "Global report strategy covering target audience, tone, depth, "
            "total length target, depth allocation across chapters, evidence "
            "integration strategy, and domain-specific requirements. "
            "This provides the overarching context for the entire report. "
            "All content must be written in the same language as the goal."
        ),
    )


class ExecutorSignature(dspy.Signature):
    """
    Executor execution result.

    Contains the output of atomic task execution.
    """

    goal: str = dspy.InputField(description="Task that needs to be executed")
    directive: Optional[str] = dspy.InputField(
        default=None,
        description=(
            "Task-specific directive bundle for this execution step. This is a "
            "separate, high-priority input channel for dynamic directives "
            "(for example user-confirmed, warm-up, or local planner refinements), "
            "kept separate from the general evidence context."
        ),
    )
    context: Optional[str] = dspy.InputField(
        default=None, description="Execution context (XML)"
    )
    output: str = dspy.OutputField(description="Execution result")
    sources: Optional[List[str]] = dspy.OutputField(
        default_factory=list, description="Information sources used"
    )


class AggregatorSignature(dspy.Signature):
    """
    Aggregator synthesis result.

    Contains the synthesis of multiple subtask results into a cohesive output.
    """

    original_goal: str = dspy.InputField(description="Original goal of the task")
    subtasks_results: List[SubTask] = dspy.InputField(
        description="List of subtask results to synthesize"
    )
    context: Optional[str] = dspy.InputField(
        default=None, description="Execution context (XML)"
    )
    synthesized_result: str = dspy.OutputField(description="Final synthesized output")


class VerifierSignature(dspy.Signature):
    """Signature for validating synthesized results against the goal."""

    goal: str = dspy.InputField(description="Task goal the output should satisfy")
    candidate_output: str = dspy.InputField(
        description="Output produced by previous modules"
    )
    context: Optional[str] = dspy.InputField(
        default=None, description="Execution context (XML)"
    )
    verdict: bool = dspy.OutputField(
        description="True if the candidate output satisfies the goal"
    )
    feedback: Optional[str] = dspy.OutputField(
        default=None, description="Explanation or fixes when the verdict is False"
    )



# =====================================================================
# Phase 1: MIPE Evolution Signatures (Edit-Script Architecture)
# =====================================================================


class DPEvolverSignature(dspy.Signature):
    """DP-island edit-script evolver: output targeted prompt edits only.

    The evolver autonomously selects the best mutation strategy based on
    diagnostic feedback, then applies targeted edits via
    ``EvolutionarySolution.apply_prompt_edits()``.
    """

    goal: str = dspy.InputField(description="Original user query")
    plan_skeleton: str = dspy.InputField(
        description=(
            "Compact plan skeleton: each node has index, goal, task_type, "
            "dependencies (NO dynamic_prompt text for non-target nodes). "
            "Multi-level plans also include depth, parent_node_id, "
            "children_ids, is_leaf."
        )
    )
    available_strategies: str = dspy.InputField(
        description="Comma-separated list of available DP mutation strategies to choose from."
    )
    previous_feedback: Optional[str] = dspy.InputField(
        default=None,
        description=(
            "Judge feedback from a prior round. Analyze this to decide which "
            "strategy to use and which nodes to fix."
        ),
    )

    chosen_strategy: str = dspy.OutputField(
        description=(
            "The atomic anchor-section op you selected from available_strategies "
            "(set_scope_boundary / set_evidence_spec / set_execution_method / "
            "set_depth_allocation / transfer_anchor), based on weakness analysis."
        )
    )
    prompt_edits: List["PromptEdit"] = dspy.OutputField(
        description=(
            "List of anchor-section edits for target nodes ONLY. Each edit contains: "
            "node_index (str), op (PromptEditOp), section_content (replacement text for "
            "the target anchor section), source_node (only for transfer_anchor), "
            "rationale (why this anchor change improves the plan). "
            "Output 2-4 edits targeting distinct anchor sections."
        )
    )
    mutation_log: str = dspy.OutputField(
        description="Concise summary of which anchor sections were changed and why."
    )


class TopoEvolverSignature(dspy.Signature):
    """T-island edit-script evolver: output structural DAG operations only.

    The evolver autonomously selects the best mutation strategy based on
    diagnostic feedback, then outputs targeted structural operations
    applied deterministically. Hierarchy fields are rebuilt programmatically.
    """

    goal: str = dspy.InputField(description="Original user query")
    plan_skeleton: str = dspy.InputField(
        description=(
            "Plan structure skeleton: each node has index, goal, task_type, "
            "dependencies, depth, parent_node_id, children_ids, is_leaf. "
            "Does NOT include dynamic_prompt text."
        )
    )
    available_strategies: str = dspy.InputField(
        description="Comma-separated list of available topology mutation strategies to choose from."
    )
    previous_feedback: Optional[str] = dspy.InputField(
        default=None,
        description=(
            "Judge feedback from a prior round. Analyze this to decide which "
            "strategy to use and which structural changes to make."
        ),
    )

    chosen_strategy: str = dspy.OutputField(
        description=(
            "The atomic structural op you selected from available_strategies "
            "(add_node / split_node / redirect_deps / delete_node), "
            "based on weakness analysis."
        )
    )
    topo_edits: List["TopoEdit"] = dspy.OutputField(
        description=(
            "List of atomic structural operations. Each edit contains: "
            "op (add / split / delete / reorder_deps), "
            "target_node (index), params (operation-specific), "
            "rationale (why this improves DAG coverage or efficiency)."
        )
    )
    mutation_log: str = dspy.OutputField(
        description="Concise summary of all structural changes made and why."
    )


class PlanJudgeSignature(dspy.Signature):
    """Unified plan evaluation: diagnose a single plan OR compare two variants.

    In **diagnose mode** (bootstrap), ``edit_log_a`` and ``edit_log_b`` are
    left at their defaults.  The LLM evaluates the plan skeleton as-is and
    returns defects, a score, and ``focus_nodes``.

    In **compare mode** (tournament), both edit logs are populated and the
    LLM decides which set of edits is better.
    """

    goal: str = dspy.InputField(description="Original user query")
    seed_plan_skeleton: str = dspy.InputField(
        description=(
            "Plan skeleton. In diagnose mode: the full plan to evaluate. "
            "In compare mode: the shared seed plan (base for both variants)."
        )
    )
    edit_log_a: str = dspy.InputField(
        default="(no edits — evaluating seed plan as-is)",
        description=(
            "Variant A modifications: mutation strategy + list of edits "
            "with rationale. Left at default for diagnose mode."
        ),
    )
    edit_log_b: str = dspy.InputField(
        default="(no edits — evaluating seed plan as-is)",
        description=(
            "Variant B modifications: mutation strategy + list of edits "
            "with rationale. Left at default for diagnose mode."
        ),
    )
    evaluation_dimension: str = dspy.InputField(
        description=(
            "Primary dimension to judge: 'topology' | 'dynamic_prompt' | 'overall'. "
            "Weight this dimension more heavily while still checking others."
        )
    )
    static_checklist: str = dspy.InputField(
        description="Static defect checklist with penalty criteria (XML format)."
    )

    preferred_plan: str = dspy.OutputField(
        description="Which variant's edits are better: 'A' | 'B' | 'tie' | 'N/A' (N/A for diagnose mode)"
    )
    plan_a_score: float = dspy.OutputField(
        description="Quality score (0-10). In diagnose mode: the plan's overall score."
    )
    plan_b_score: float = dspy.OutputField(
        description="Quality score (0-10). In diagnose mode: same as plan_a_score."
    )
    plan_a_defects: List[str] = dspy.OutputField(
        description="Defects found, each citing specific node indices"
    )
    plan_b_defects: List[str] = dspy.OutputField(
        description="Issues with Variant B's edits (empty list in diagnose mode)"
    )
    reasoning: str = dspy.OutputField(
        description="Structured analysis and final verdict."
    )
    improvement_signals: str = dspy.OutputField(
        description=(
            "Free-form weakness description for the next optimization round. "
            "Include: primary_weakness (the core problem and its impact), "
            "prior_attempt_analysis (what was tried and why it failed, from mutation_log), "
            "node_level_hints (specific per-node issues with node indices and defect types)."
        )
    )
    focus_nodes: List[int] = dspy.OutputField(
        description=(
            "Node indices that should be prioritized in the next evolution "
            "iteration. Include nodes with identified defects and their "
            "critical dependencies."
        )
    )


# Backward-compatible aliases so existing imports keep working during migration
EditLogJudgeSignature = PlanJudgeSignature
SelfDiagnoseSignature = PlanJudgeSignature


class ValidityJudgeSignature(dspy.Signature):
    """Phase 3 validity check: compare P_final vs P0 (no shared seed).

    Uses compressed plan representations with diff highlights since
    the two plans may differ substantially.
    """

    goal: str = dspy.InputField(description="Original user query")
    plan_a_compressed: str = dspy.InputField(
        description="Compressed skeleton of P_final (with all prompts)"
    )
    plan_b_compressed: str = dspy.InputField(
        description="Compressed skeleton of P0 (with all prompts)"
    )
    diff_highlights: str = dspy.InputField(
        description="Summary of key differences between P_final and P0"
    )
    static_checklist: str = dspy.InputField(
        description="Static defect checklist (XML format)"
    )

    preferred_plan: str = dspy.OutputField(
        description="Which plan is better: 'A' (P_final) | 'B' (P0) | 'tie'"
    )
    plan_a_score: float = dspy.OutputField(
        description="Quality score for P_final (0-10)"
    )
    plan_b_score: float = dspy.OutputField(
        description="Quality score for P0 (0-10)"
    )
    plan_a_defects: List[str] = dspy.OutputField(
        description="Defects in P_final"
    )
    plan_b_defects: List[str] = dspy.OutputField(
        description="Defects in P0"
    )
    reasoning: str = dspy.OutputField(
        description="Structured comparison and verdict."
    )
    improvement_signals: str = dspy.OutputField(
        description="Feedback for future evolution runs."
    )




class ConsistencyCheckerSignature(dspy.Signature):
    """Post-assembly cross-node consistency check.

    Scans ALL dynamic_prompts after programmatic merge for three issues:
      1. Cross-node redundancy (two nodes targeting the same content)
      2. Up/downstream conflicts (upstream output vs downstream expectation)
      3. Topology gaps (newly added nodes with generic/missing prompts)

    Outputs ONLY the fixes needed — an empty list means no issues found.

    .. deprecated::
        Superseded by ``CrossIslandMergeSignature`` which performs merge
        and consistency checking in a single LLM call.
    """

    goal: str = dspy.InputField(description="Original user query")
    plan_with_prompts: str = dspy.InputField(
        description=(
            "Complete plan skeleton with ALL dynamic_prompts included. "
            "The checker needs to see all prompts to detect cross-node issues."
        )
    )

    fixes: List["PromptEdit"] = dspy.OutputField(
        description=(
            "List of PromptEdit fixes for cross-node issues. Each fix has "
            "node_index, new_dynamic_prompt, rationale. Return EMPTY list "
            "if no cross-node issues are found."
        )
    )
    checker_log: str = dspy.OutputField(
        description=(
            "Summary of issues found: which redundancies, conflicts, or gaps "
            "were detected and how each fix addresses them."
        )
    )


class CrossIslandMergeSignature(dspy.Signature):
    """Cross-island intelligent merge: map DP prompt improvements onto T topology.

    The T-island winner has an optimised DAG topology but may still carry
    the original (P0) dynamic_prompts.  The DP-island winner has optimised
    prompts but on P0's (possibly different) topology.

    This signature asks the LLM to:
      1. Identify which T_best nodes correspond to each DP prompt improvement
         (by semantic similarity of goals, NOT by index).
      2. Adapt each DP prompt so it is consistent with the target node's
         actual goal and task_type in T_best.
      3. Detect and fix any cross-node consistency issues (redundancy,
         upstream/downstream conflicts, missing prompts on newly-added nodes).
      4. Skip DP improvements that have no suitable target in T_best.

    HARD CONSTRAINTS:
      - Do NOT change the topology (no add/delete/split/reorder_deps).
      - ONLY output PromptEdit operations that modify dynamic_prompt fields.
      - Each PromptEdit must target a valid node index in T_best.
      - The adapted prompt MUST match the target node's task_type semantics
        (e.g. a RETRIEVE node must have a retrieval-oriented prompt).
    """

    goal: str = dspy.InputField(description="Original user query")
    t_best_plan: str = dspy.InputField(
        description=(
            "T-island winner: complete plan skeleton with ALL dynamic_prompts "
            "included.  This is the topology that will be preserved as-is."
        )
    )
    dp_prompt_improvements: str = dspy.InputField(
        description=(
            "DP-island prompt improvements extracted by diffing DP_best vs P0. "
            "Each entry shows the P0 node's index, goal, task_type, and the "
            "optimised prompt text.  These were optimised on P0's topology "
            "which may differ from T_best's topology."
        )
    )
    island_feedback: Optional[str] = dspy.InputField(
        default=None,
        description="Combined diagnostic feedback from both island tournaments."
    )

    prompt_edits: List["PromptEdit"] = dspy.OutputField(
        description=(
            "PromptEdit list to apply to T_best.  For each DP improvement: "
            "(1) find the semantically matching node in T_best, "
            "(2) adapt the prompt for the target node's actual goal/task_type, "
            "(3) skip if no suitable target exists.  "
            "Also include fixes for any cross-node consistency issues detected.  "
            "Return an EMPTY list if T_best's prompts are already adequate."
        )
    )
    merge_log: str = dspy.OutputField(
        description=(
            "Per-improvement mapping log: which T_best node each DP improvement "
            "was mapped to and why (or why it was skipped).  "
            "Plus any cross-node consistency issues found and addressed."
        )
    )




# =====================================================================
# Phase 2: Runtime Adaptation Signatures
# =====================================================================


class SentinelSignature(dspy.Signature):
    """Lightweight consistency check between PlanBlueprint and actual execution state.

    Called at two checkpoints during DAG execution:
      - post_retrieve: after all RETRIEVE tasks finish
      - post_think:    after all THINK tasks finish

    Determines whether runtime adaptation is needed and at what level,
    returning the minimum intervention required to keep the plan on track.
    """

    goal: str = dspy.InputField(description="Original user query")
    blueprint_summary: str = dspy.InputField(
        description=(
            "Concise summary of the PlanBlueprint: topology skeleton node list, "
            "key directive constraints, depth allocation targets, known risks."
        )
    )
    actual_results_summary: str = dspy.InputField(
        description=(
            "Summary of execution results so far. For post_retrieve: retrieved evidence "
            "topics, coverage gaps, unexpected findings. For post_think: outline structure, "
            "content density per section, structural divergences from blueprint."
        )
    )
    checkpoint_type: str = dspy.InputField(
        description="Which checkpoint this is: 'post_retrieve' | 'post_think'"
    )

    adjustment_needed: bool = dspy.OutputField(
        description="True if any adaptation is required to keep the plan on track"
    )
    adjustment_type: str = dspy.OutputField(
        description=(
            "Minimum intervention level needed: "
            "'none' — everything matches, continue as planned; "
            "'directive_only' — rewrite dynamic_prompt directives for pending tasks "
            "based on actual execution results (e.g. refine focus, add constraints, "
            "adjust analysis angle). Use prompt_overrides to provide complete "
            "replacement prompts per node, or directive_fill_hints for simple "
            "placeholder filling; "
            "'subtree_replan' — one or more subtask subtrees need targeted replanning "
            "(goals, task types, or dependencies need restructuring, costs 1 LLM call); "
            "'topology_change' — major structural change needed, consider fallback_topology."
        )
    )
    affected_nodes: List[str] = dspy.OutputField(
        description=(
            "Node IDs that need adjustment. Empty list if adjustment_type is 'none'. "
            "For 'directive_only', these are nodes whose dynamic_prompts need revision."
        )
    )
    prompt_overrides: Optional[Dict[str, str]] = dspy.OutputField(
        default=None,
        description=(
            "For 'directive_only': mapping of node_id to the COMPLETE new "
            "dynamic_prompt text that should replace the existing one. "
            "E.g., {'3': '聚焦AI芯片供应链安全领域的深度分析。采用先结论后论据的结构。"
            "每个论点必须用产业数据和政策文件类型的证据支撑。', "
            "'5': '从技术可行性和商业化前景两个维度展开分析...'}. "
            "Each value should be a self-contained directive. Preferred over "
            "directive_fill_hints for maximum flexibility."
        ),
    )
    directive_fill_hints: Optional[Dict[str, str]] = dspy.OutputField(
        default=None,
        description=(
            "For 'directive_only': mapping of placeholder names to their runtime values. "
            "E.g., {'evidence_focus': 'AI芯片供应链安全', 'skip_topic': '半导体制造工艺'}. "
            "Only used when dynamic_prompts contain {placeholder} tokens. "
            "Prefer prompt_overrides for direct prompt rewriting."
        ),
    )
    replan_guidance: Optional[str] = dspy.OutputField(
        default=None,
        description=(
            "For 'subtree_replan' or 'topology_change': concrete guidance for the "
            "micro-replan. Describes what changed, which aspects need more/less focus, "
            "and what the replanned subtree should achieve."
        ),
    )
    confidence: float = dspy.OutputField(
        description=(
            "Sentinel's confidence in this assessment (0.0-1.0). "
            "Low confidence (<0.5) suggests the situation is ambiguous and conservative "
            "intervention is preferred."
        )
    )


