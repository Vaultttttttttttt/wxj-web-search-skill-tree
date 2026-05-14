"""Sentinel Checkpoint — lightweight consistency checks at phase boundaries.

Two checkpoints are inserted into the DAG execution loop:

  Sentinel-1 (post-retrieve): After all RETRIEVE tasks complete.
      Checks coverage gaps, empty results, unexpected findings.

  Sentinel-2 (post-think): After all THINK tasks complete.
      Checks outline/blueprint alignment, content density, overlap.

Each Sentinel call costs **1 LLM invocation**.  If the Sentinel
determines that adaptation is needed it returns an ``adjustment_type``:

  - ``none``              → continue as planned (0 cost)
  - ``directive_only``    → fill template placeholders (0 cost)
  - ``subtree_replan``    → targeted Planner call on affected subtree (1 LLM call)
  - ``topology_change``   → activate fallback_topology or broader replan (1-2 LLM calls)

This module also houses the ``micro_replan`` helper that executes the
targeted Planner call when ``subtree_replan`` is requested.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from roma_dspy.core.signatures.base_models.plan_blueprint import PlanBlueprint
    from roma_dspy.core.engine.context_store import ContextStore


# =====================================================================
# Data Structures
# =====================================================================


@dataclass
class NodeResult:
    """Quality-annotated result from a single DAG node."""

    node_id: str
    task_id: str
    goal: str
    result_summary: str
    quality_signals: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SentinelResult:
    """Outcome of a single Sentinel checkpoint evaluation."""

    adjustment_needed: bool = False
    adjustment_type: str = "none"
    affected_nodes: List[str] = field(default_factory=list)
    directive_fill_hints: Optional[Dict[str, str]] = None
    prompt_overrides: Optional[Dict[str, str]] = None
    replan_guidance: Optional[str] = None
    confidence: float = 1.0
    raw_reasoning: Optional[str] = None


# =====================================================================
# Sentinel Checkpoint
# =====================================================================


class SentinelCheckpoint:
    """Phase-boundary consistency checker for PlanBlueprint alignment.

    Instantiated once per execution when MIPE evolution is enabled.
    The Sentinel LLM agent is lazily created on first use.
    """

    def __init__(
        self,
        blueprint: "PlanBlueprint",
        context_store: "ContextStore",
        sentinel_config: Optional[Any] = None,
        roma_config: Optional[Any] = None,
        mlflow_manager: Optional[Any] = None,
    ) -> None:
        self._blueprint = blueprint
        self._context_store = context_store
        self._sentinel_config = sentinel_config
        self._roma_config = roma_config
        self._agent: Optional[Any] = None

        from roma_dspy.core.observability.mipe_span_logger import MIPESpanLogger
        self._span_logger = MIPESpanLogger(mlflow_manager)

    # ------------------------------------------------------------------
    # Lazy agent construction (follows warmup.py _create_custom_sig_agent)
    # ------------------------------------------------------------------

    def _ensure_agent(self) -> Any:
        if self._agent is not None:
            return self._agent

        from roma_dspy.config.schemas.agents import AgentConfig
        from roma_dspy.core.modules.base_module import BaseModule
        from roma_dspy.core.signatures.signatures import SentinelSignature
        from roma_dspy.core.utils.instruction_loader import InstructionLoader

        llm_config = None
        instructions_path = None

        if self._sentinel_config is not None:
            llm_config = getattr(self._sentinel_config, "llm", None)
            instructions_path = getattr(
                self._sentinel_config, "signature_instructions", None
            )

        if llm_config is None and self._roma_config:
            default_agent = getattr(self._roma_config.agents, "executor", None)
            if default_agent and hasattr(default_agent, "llm"):
                llm_config = default_agent.llm

        signature = SentinelSignature
        if instructions_path:
            try:
                loader = InstructionLoader()
                instructions = loader.load(instructions_path)
                if instructions:
                    signature = type(
                        "SentinelSignatureConfigured",
                        (SentinelSignature,),
                        {"__doc__": instructions, "__module__": SentinelSignature.__module__},
                    )
            except Exception as e:
                logger.warning(f"[SENTINEL] Failed to load instructions: {e}")

        agent_config = AgentConfig(
            llm=llm_config,
            prediction_strategy="chain_of_thought",
        )
        self._agent = BaseModule(signature=signature, config=agent_config)
        return self._agent

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_post_retrieve(
        self, node_results: List[NodeResult], goal: str
    ) -> SentinelResult:
        """Sentinel-1: run after all RETRIEVE tasks complete."""
        return await self._run_check("post_retrieve", node_results, goal)

    async def check_post_think(
        self, node_results: List[NodeResult], goal: str
    ) -> SentinelResult:
        """Sentinel-2: run after all THINK tasks complete."""
        return await self._run_check("post_think", node_results, goal)

    async def _run_check(
        self,
        checkpoint_type: str,
        node_results: List[NodeResult],
        goal: str,
    ) -> SentinelResult:
        """Core check logic shared by both checkpoints."""
        sl = self._span_logger
        span_name = f"Sentinel: {checkpoint_type}"

        with sl.phase(span_name, {"checkpoint_type": checkpoint_type, "node_count": len(node_results)}):
            try:
                agent = self._ensure_agent()
                blueprint_summary = self._blueprint.to_planner_context()
                results_summary = self._format_results(node_results, checkpoint_type)

                result = await agent.aforward(
                    goal=goal,
                    blueprint_summary=blueprint_summary,
                    actual_results_summary=results_summary,
                    checkpoint_type=checkpoint_type,
                )

                adj_needed = bool(getattr(result, "adjustment_needed", False))
                adj_type = str(getattr(result, "adjustment_type", "none")).strip().lower()

                valid_types = {"none", "directive_only", "subtree_replan", "topology_change"}
                if adj_type not in valid_types:
                    logger.warning(
                        f"[SENTINEL] Unknown adjustment_type '{adj_type}', defaulting to 'none'"
                    )
                    adj_type = "none"

                affected = getattr(result, "affected_nodes", []) or []
                if isinstance(affected, str):
                    affected = [affected]

                fill_hints = getattr(result, "directive_fill_hints", None)
                if isinstance(fill_hints, str):
                    try:
                        fill_hints = json.loads(fill_hints)
                    except (json.JSONDecodeError, TypeError):
                        fill_hints = None

                prompt_overrides = getattr(result, "prompt_overrides", None)
                if isinstance(prompt_overrides, str):
                    try:
                        prompt_overrides = json.loads(prompt_overrides)
                    except (json.JSONDecodeError, TypeError):
                        prompt_overrides = None

                confidence = 1.0
                try:
                    confidence = float(getattr(result, "confidence", 1.0))
                except (TypeError, ValueError):
                    pass

                sentinel_result = SentinelResult(
                    adjustment_needed=adj_needed,
                    adjustment_type=adj_type,
                    affected_nodes=list(affected),
                    directive_fill_hints=fill_hints if isinstance(fill_hints, dict) else None,
                    prompt_overrides=prompt_overrides if isinstance(prompt_overrides, dict) else None,
                    replan_guidance=getattr(result, "replan_guidance", None),
                    confidence=confidence,
                )

                logger.info(
                    f"[SENTINEL] {checkpoint_type}: adjustment_needed={adj_needed}, "
                    f"type={adj_type}, affected={len(sentinel_result.affected_nodes)} nodes, "
                    f"confidence={confidence:.2f}"
                )

                sl.log_sentinel_result(
                    checkpoint_type=checkpoint_type,
                    adjustment_needed=adj_needed,
                    adjustment_type=adj_type,
                    affected_count=len(sentinel_result.affected_nodes),
                    confidence=confidence,
                )

                return sentinel_result

            except Exception as e:
                logger.warning(
                    f"[SENTINEL] {checkpoint_type} check failed, continuing without: {e}"
                )
                sl.log_sentinel_result(
                    checkpoint_type=checkpoint_type,
                    adjustment_needed=False,
                    adjustment_type="error",
                    affected_count=0,
                    confidence=0.0,
                )
                return SentinelResult()

    # ------------------------------------------------------------------
    # Micro-Replan
    # ------------------------------------------------------------------

    async def micro_replan(
        self,
        affected_task_ids: List[str],
        replan_guidance: str,
        goal: str,
        planner_agent: Optional[Any] = None,
    ) -> Optional[Any]:
        """Execute a targeted Planner call on affected subtask subtree.

        This is NOT a full MIPE evolution — just one Planner invocation
        with the Sentinel's guidance injected as context.

        Args:
            affected_task_ids: Task IDs that need replanning.
            replan_guidance: Sentinel's concrete guidance for the replan.
            goal: Original user query.
            planner_agent: The Planner agent to use.  If ``None``, a
                fresh Planner is not created (caller must provide one).

        Returns:
            Planner result or ``None`` if replanning fails / is skipped.
        """
        if not planner_agent:
            logger.warning("[SENTINEL] No planner_agent provided for micro-replan, skipping")
            return None

        if not affected_task_ids:
            return None

        context = (
            "<context>\n"
            "<sentinel_replan_guidance>\n"
            f"{replan_guidance}\n"
            "</sentinel_replan_guidance>\n"
            "</context>"
        )

        try:
            logger.info(
                f"[SENTINEL] Micro-replan for {len(affected_task_ids)} affected nodes"
            )
            result = await planner_agent.aforward(goal=goal, context=context)
            return result
        except Exception as e:
            logger.warning(f"[SENTINEL] Micro-replan failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_results(
        node_results: List[NodeResult], checkpoint_type: str
    ) -> str:
        """Build a structured summary of node results for the Sentinel LLM."""
        lines = [f"## {checkpoint_type.upper()} 阶段结果汇总\n"]

        for nr in node_results:
            lines.append(f"### 节点 {nr.node_id} (task: {nr.task_id[:8]}...)")
            lines.append(f"目标: {nr.goal}")

            summary = nr.result_summary
            if len(summary) > 500:
                summary = summary[:500] + "..."
            lines.append(f"结果摘要: {summary}")

            if nr.quality_signals:
                signals = ", ".join(
                    f"{k}={v}" for k, v in nr.quality_signals.items()
                )
                lines.append(f"质量信号: {signals}")

            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def collect_quality_signals(
        task_result: Optional[str],
        task_type: str,
    ) -> Dict[str, Any]:
        """Extract lightweight quality signals from a task result string.

        Called by runtime.py when collecting node results for Sentinel input.
        """
        if not task_result:
            return {"is_empty": True, "result_length": 0}

        result_len = len(task_result)
        signals: Dict[str, Any] = {
            "is_empty": result_len == 0,
            "result_length": result_len,
        }

        if task_type.upper() == "RETRIEVE":
            import re
            url_count = len(re.findall(r"https?://", task_result))
            signals["url_count"] = url_count
            signals["has_urls"] = url_count > 0

        return signals
