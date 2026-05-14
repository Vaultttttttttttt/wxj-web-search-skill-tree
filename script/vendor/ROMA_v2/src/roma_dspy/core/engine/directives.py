"""Dynamic prompt injection for agent context construction.

Each task node carries a ``dynamic_prompt`` field set by the unified Planner.
This is the sole per-task instruction injected into the executor's context.

The warm-up phase no longer stores type-based global directives; instead
each subtask carries its own self-contained ``dynamic_prompt``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from loguru import logger

from roma_dspy.core.engine.context_store import _sanitize_directive
from roma_dspy.core.signatures import TaskNode
from roma_dspy.types import AgentType, TaskType

if TYPE_CHECKING:
    from roma_dspy.core.modules.base_module import BaseModule


class DirectivesMixin:
    """Mixin providing dynamic_prompt injection for ModuleRuntime.

    Expects the host class to have:
    - self.context_store: ContextStore
    - self.config: Optional[ROMAConfig]
    """

    def _get_effective_agent_config(
        self, agent_type: AgentType, task_type: Optional[TaskType]
    ) -> Optional[Any]:
        """Resolve the config that matches the routed agent instance.

        The registry already routes by ``(agent_type, task_type)`` with fallback
        to the default agent. Context construction should use the same routing
        decision; otherwise task-specific settings like artifact injection mode
        silently fall back to the default config.
        """
        if not self.config:
            return None

        agent_mapping = getattr(self.config, "agent_mapping", None)
        if agent_mapping:
            mapping_attrs = {
                AgentType.ATOMIZER: ("atomizers", "default_atomizer"),
                AgentType.PLANNER: ("planners", "default_planner"),
                AgentType.EXECUTOR: ("executors", "default_executor"),
                AgentType.AGGREGATOR: ("aggregators", "default_aggregator"),
                AgentType.VERIFIER: ("verifiers", "default_verifier"),
            }
            task_map_attr, default_attr = mapping_attrs[agent_type]
            task_configs = getattr(agent_mapping, task_map_attr, {}) or {}

            if task_type is not None:
                task_config = task_configs.get(task_type.value)
                if task_config is not None:
                    return task_config

            default_config = getattr(agent_mapping, default_attr, None)
            if default_config is not None:
                return default_config

        if hasattr(self.config, "agents") and self.config.agents:
            return self.config.agents.get_config_for_agent(agent_type)

        return None

    @staticmethod
    def _build_dynamic_prompt_block(dynamic_prompt: Optional[str]) -> str:
        """Wrap task.dynamic_prompt in an XML directive block for context injection.

        Returns an empty string when dynamic_prompt is absent or null-like,
        so callers can safely check ``if directive_block:`` before appending.
        """
        prompt = _sanitize_directive(dynamic_prompt)
        if not prompt:
            return ""

        logger.debug(
            f"[DIRECTIVE-BUILD] Injecting dynamic_prompt "
            f"({len(prompt)} chars) into agent context"
        )

        return (
            "<task_directive>\n"
            f"{prompt.strip()}\n"
            "</task_directive>"
        )
