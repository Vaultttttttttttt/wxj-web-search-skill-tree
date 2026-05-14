from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import dspy
from loguru import logger

from roma_dspy.tools.base.base import BaseToolkit
from roma_dspy.tools.utils.retrieval_evaluator import LLMRetrievalEvaluator
from roma_dspy.types.retrieve_result import (
    DecisionType,
    RetrieveContext,
    RetrieveDebugInfo,
    RetrieveResult,
    SourceType,
)

try:
    import mlflow
    from mlflow.tracing.fluent import start_span as mlflow_start_span
    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False
    mlflow = None
    mlflow_start_span = None


class ExaMCPPool:
    """管理多个 EXA MCP 实例，提供并发安全的 round-robin 负载均衡与冷却管理。

    设计原则
    --------
    * 无共享游标：每次 pick_instance() 调用都是同步原子操作（asyncio 单线程协作调度），
      不依赖全局 _current_idx，避免并发任务互相"踩踏"游标导致同一实例被多任务同时选中。
    * Round-robin 轮转：_rr_counter 在成功选中实例后前进，确保多个并发任务在初次
      选择时均匀分布到不同实例，实现真正的负载分散。
    * 冷却只增不减：mark_rate_limited 使用 max() 保证已有冷却时间不会被更短的新
      调用覆盖缩短。
    * 超时与速率限制分开：调用方可以传入不同的 cooldown_seconds，使两种错误有不同的
      惩罚窗口（超时一般比速率限制短）。
    """

    def __init__(self, toolkits: List["MCPToolkit"]):
        if not toolkits:
            raise ValueError("ExaMCPPool requires at least one MCPToolkit instance")
        self._toolkits = toolkits
        self._cooldowns: Dict[int, float] = {}  # idx -> cooldown_until 时间戳
        self._rr_counter: int = 0               # round-robin 起始游标

    @property
    def size(self) -> int:
        return len(self._toolkits)

    def mark_rate_limited(self, idx: int, cooldown_seconds: float = 60.0) -> None:
        """将实例 idx 标记为冷却期（不杀进程）。冷却截止时间只增不减。"""
        new_until = time.time() + cooldown_seconds
        self._cooldowns[idx] = max(self._cooldowns.get(idx, 0.0), new_until)

    def pick_instance(self, exclude: Optional[set] = None) -> Tuple[Optional[int], Optional["MCPToolkit"]]:
        """从池中选出下一个可用实例（round-robin，跳过已排除和冷却中的实例）。

        该方法为纯同步操作，在 asyncio 单线程模型下无需额外锁即可保证原子性：
        函数从进入到返回之间不存在 await 点，不会被其他协程抢占。

        Parameters
        ----------
        exclude : set, optional
            本次调用中已尝试过、需要跳过的实例索引集合。

        Returns
        -------
        (idx, toolkit) 若找到可用实例；(None, None) 若所有实例均在冷却或被排除。
        """
        now = time.time()
        n = len(self._toolkits)
        _exclude = exclude or set()

        for i in range(n):
            idx = (self._rr_counter + i) % n
            if idx in _exclude:
                continue
            if self._cooldowns.get(idx, 0.0) <= now:
                # 前进游标，保证下一个并发调用从不同位置开始
                self._rr_counter = (idx + 1) % n
                return idx, self._toolkits[idx]

        return None, None  # 所有实例均不可用

    def all_toolkits(self) -> List["MCPToolkit"]:
        return self._toolkits

    def cooldown_snapshot(self) -> Dict[int, float]:
        """返回各实例剩余冷却秒数（仅包含仍在冷却中的实例）。"""
        now = time.time()
        return {
            idx: round(max(0.0, until - now), 2)
            for idx, until in self._cooldowns.items()
            if until > now
        }


class AdaptiveRetrieveToolkit(BaseToolkit):
    """
    Adaptive retrieval toolkit supporting multiple modes.

    Modes:
    - mode="auto": Adaptive routing (Recommended) - Driven by LLM-as-Judge
    - mode="rag": RAGFlow internal knowledge base only
    - mode="web": Exa Web search only
    - mode="hybrid": RAGFlow + Exa hybrid

    Configuration (toolkit_config):
        default_mode: str - Default retrieval mode ("auto", "rag", "web", "hybrid", default "auto")
        
        # RAGFlow
        ragflow_toolkit_instance: RAGFlowToolkit instance (optional)
        ragflow_config: dict - (api_url, api_key, kb_id, timeout)
        
        # Web Search
        web_backend: str - "exa" | "skill_tree" | "mixed" (default "exa")
          - exa: MCP Exa only
          - skill_tree: local skill-tree scripts only
          - mixed: skill_tree primary + Exa backfill when evidence is insufficient

        # MCP Exa
        web_toolkit_instance: MCPToolkit instance (optional, legacy injection path)
        web_toolkit_configs: list[dict] - Exa MCP instance configs for pool mode
        web_tool_method: str - MCP tool method name (default "web_search_exa")
        web_tool_kwargs: dict - Extra search parameters

        # Skill Tree
        skill_tree_toolkit_instance: SkillTreeWebSearchToolkit instance (optional)
        skill_tree_config: dict - config passed to SkillTreeWebSearchToolkit
        web_backfill_min_sources: int - min unique sources before skipping Exa backfill
        web_backfill_min_results: int - min results before skipping Exa backfill
        
        # Evaluator
        evaluator_mode: str - "heuristic" or "llm" (default "heuristic")
        evaluator_config: dict - LLM evaluator config (when evaluator_mode="llm")
        
        # General
        top_n_default: int - Default results count (default 10)
        mlflow_logging: bool - Enable MLflow tracing (default True)
    """

    def _setup_dependencies(self) -> None:
        """Setup RAGFlow and Exa MCP toolkits."""
        self.web_backend = str(self.config.get("web_backend", "exa")).lower()

        # 1) RAGFlow toolkit
        self.ragflow_toolkit = self.config.get("ragflow_toolkit_instance")
        if self.ragflow_toolkit is None:
            ragflow_config = self.config.get("ragflow_config", {})
            if not ragflow_config:
                ragflow_config = {
                    key: self.config.get(key)
                    for key in ("api_url", "api_key", "kb_id", "timeout")
                    if self.config.get(key) is not None
                }
            from roma_dspy.tools.ragflow_toolkit import RAGFlowToolkit
            self.ragflow_toolkit = RAGFlowToolkit(enabled=True, **ragflow_config)

        # 2) Web search toolkit (MCP Exa)
        # NOTE: when backend=skill_tree, skip MCP Exa initialization entirely to
        # avoid startup failures from unavailable exa servers.
        self.web_toolkit = None
        self.web_toolkit_pool = None
        if self.web_backend in ("exa", "mixed"):
            self.web_toolkit = self.config.get("web_toolkit_instance")
            if self.web_toolkit is not None:
                # 兼容注入单实例：统一封装为连接池，避免后续调用分叉
                self.web_toolkit_pool = ExaMCPPool([self.web_toolkit])
            else:
                web_toolkit_configs = self.config.get("web_toolkit_configs")
                if web_toolkit_configs:
                    from roma_dspy.tools.mcp.toolkit import MCPToolkit
                    toolkits = []
                    for cfg in web_toolkit_configs:
                        kwargs = dict(cfg)
                        kwargs.setdefault("enabled", True)
                        kwargs.setdefault("include_tools", None)
                        kwargs.setdefault("exclude_tools", None)
                        toolkits.append(MCPToolkit(**kwargs))
                    self.web_toolkit_pool = ExaMCPPool(toolkits)
                    self.web_toolkit = None

        self.web_tool_method = self.config.get("web_tool_method", "web_search_exa")
        self.web_tool_kwargs = self.config.get("web_tool_kwargs", {})

        # 3) Skill-tree web toolkit (local script based)
        self.skill_tree_toolkit = self.config.get("skill_tree_toolkit_instance")
        if self.skill_tree_toolkit is None and self.web_backend in ("skill_tree", "mixed"):
            from roma_dspy.tools.web_search.skill_tree import SkillTreeWebSearchToolkit

            skill_tree_cfg = dict(self.config.get("skill_tree_config", {}))
            skill_tree_cfg.setdefault("enabled", True)
            self.skill_tree_toolkit = SkillTreeWebSearchToolkit(**skill_tree_cfg)

    def _initialize_tools(self) -> None:
        """Initialize default parameters."""
        self.top_n_default = int(self.config.get("top_n_default", 10))
        self.web_backend = str(self.config.get("web_backend", self.web_backend)).lower()
        self.web_unlimited_mode = bool(self.config.get("web_unlimited_mode", False))
        self.web_unlimited_top_n = int(self.config.get("web_unlimited_top_n", 200))
        self.web_backfill_min_sources = int(self.config.get("web_backfill_min_sources", 6))
        self.web_backfill_min_results = int(self.config.get("web_backfill_min_results", 5))
        self.web_url_backfill_enabled = bool(self.config.get("web_url_backfill_enabled", True))
        self.web_url_backfill_max_rounds = int(self.config.get("web_url_backfill_max_rounds", 2))
        self.web_url_backfill_multiplier = int(self.config.get("web_url_backfill_multiplier", 2))
        self.web_url_backfill_cap = int(self.config.get("web_url_backfill_cap", 48))
        self.web_relax_query_on_empty = bool(self.config.get("web_relax_query_on_empty", True))

        self.default_mode = self.config.get("default_mode", "auto")
        kb_id = self.config.get("kb_id")
        self.log_info(f"[AdaptiveRetrieveToolkit] Initialized with mode={self.default_mode}, kb_id={kb_id}")
        
        self.mlflow_enabled = MLFLOW_AVAILABLE and self.config.get("mlflow_logging", True)
        
        # Evaluator Configuration
        self.evaluator_mode = self.config.get("evaluator_mode", "heuristic")  # "heuristic" or "llm"
        self.evaluator = None
        if self.evaluator_mode == "llm":
            self.evaluator = LLMRetrievalEvaluator(self.config.get("evaluator_config", {}))
            self.log_info("Initialized LLM Retrieval Evaluator")

        self._mcp_init_lock = asyncio.Lock()

    def _build_tool_schema(self) -> Dict[str, Any]:
        """Build tool argument schema.

        Keep ``mode`` always visible to avoid adapter validation errors when the
        model emits ``mode`` in tool calls. Fixed-mode enforcement is handled in
        runtime logic (``adaptive_retrieve_async``), where incompatible modes are
        ignored with an informational log.
        """
        schema: Dict[str, Any] = {
            "query": {
                "type": "string",
                "description": "Search query (single string)"
            },
            "queries": {
                "type": ["string", "array"],
                "items": {"type": "string"},
                "description": "Legacy alias for 'query'."
            },
            "top_n": {
                "type": ["integer", "null"],
                "description": "Number of results to return. If omitted, uses profile top_n_default.",
                "default": None
            }
        }
        schema["mode"] = {
            "type": ["string", "null"],
            "enum": ["auto", "rag", "web", "hybrid", None],
            "description": (
                "Retrieval mode hint: auto, rag, web, or hybrid. "
                "If profile enforces a fixed mode, this argument is accepted but ignored."
            ),
            "default": None
        }
        return schema

    def _build_tool_arg_desc(self) -> Dict[str, str]:
        desc: Dict[str, str] = {
            "query": "The search query",
            "queries": "Alternative for query",
            "top_n": "Maximum number of results to return",
            "mode": (
                "Retrieval mode hint: auto, rag, web, or hybrid. "
                "Ignored when profile enforces default_mode."
            ),
        }
        return desc

    def _register_all_tools(self) -> None:
        """
        Override to register search_adaptive as a dspy.Tool with explicit schema.
        """
        search_tool = dspy.Tool(
            func=self.search_adaptive_impl,
            name="search_adaptive",
            desc=self.__class__.search_adaptive.__doc__ or "Adaptive search tool",
            args=self._build_tool_schema(),
            arg_types={
                "query": str,
                "mode": Optional[str],
                "top_n": Optional[int]
            },
            arg_desc=self._build_tool_arg_desc()
        )
        
        from roma_dspy.tools.metrics.decorators import track_tool_invocation
        wrapped_func = track_tool_invocation(
            tool_name="search_adaptive",
            toolkit_class=self.__class__.__name__
        )(search_tool.func)
        
        object.__setattr__(search_tool, "func", wrapped_func)
        
        self._tools["search_adaptive"] = search_tool
        self.log_debug("Registered search_adaptive as dspy.Tool with explicit schema")

    def get_available_tool_names(self) -> set[str]:
        return {"search_adaptive"}

    def _calculate_confidence(self, query: str, chunks: List[Dict[str, Any]], force_heuristic: bool = False) -> Tuple[float, str]:
        """
        Calculate retrieval confidence using Heuristic or LLM-as-judge.
        
        Args:
            query: The search query.
            chunks: Retrieved RAG chunks.
            force_heuristic: If True, bypass LLM evaluator even if configured (optimization for forced modes).
        """
        if not chunks:
            return 0.0, "No chunks found"

        # LLM Evaluation
        if not force_heuristic and self.evaluator_mode == "llm" and self.evaluator:
            with self._trace_span("llm_judge_evaluate", {"query": query, "chunk_count": len(chunks)}) as span:
                score, reason = self.evaluator.evaluate(query, chunks)
                
                if span:
                    span.set_outputs({"score": score, "reason": reason})
                    span.set_attributes({
                        "roma.evaluator.score": score,
                        "roma.evaluator.mode": "llm"
                    })
                return score, reason

        # Heuristic Rules
        top1_score = chunks[0].get("score", 0.0)
        result_count = len(chunks)
        reason = f"Heuristic: top1={top1_score:.2f}, count={result_count}"

        if result_count == 0:
            return 0.0, reason
        elif result_count == 1:
            # Discount for single result to avoid high confidence on lone outliers
            score = min(top1_score * 0.8, 0.65)
            return score, reason
        else:
            score = min(top1_score, 1.0)
            return score, reason

    def _format_ragflow_contexts(self, chunks: List[Dict[str, Any]]) -> List[RetrieveContext]:
        """Convert RAGFlow chunks to RetrieveContext list."""
        raw_contexts = self.ragflow_toolkit.format_ragflow_contexts(chunks)
        contexts: List[RetrieveContext] = []
        for ctx in raw_contexts:
            contexts.append(
                RetrieveContext(
                    text=ctx["text"],
                    source=SourceType.RAGFLOW,
                    url=ctx["url"],
                    title=ctx.get("title", ""),
                    score=ctx.get("score", 0.0),
                )
            )
        return contexts

    def _format_web_contexts(self, web_results: Dict[str, Any]) -> List[RetrieveContext]:
        """Convert MCP Exa results to RetrieveContext list."""
        results = web_results.get("results", [])
        if not isinstance(results, list):
            return []

        contexts = []
        for item in results:
            url = str(item.get("url", "")).strip()
            # Keep final web evidence citable: skip non-url snippets in final contexts.
            if not url:
                self.log_debug(
                    f"[Web Search] Skip non-citable result (missing URL): "
                    f"{item.get('title', 'unknown')}"
                )
                continue

            # 优先级策略（防止上下文爆炸）：
            # 1. summary（简洁摘要，信息密度高，推荐）
            # 2. highlights（LLM 友好的高亮片段，几百字符）
            # 3. autopromptString（自动生成的提示词优化字符串）
            # 4. text（完整网页内容，避免使用！可能导致上下文爆炸）
            
            text = ""
            
            # 优先：summary（推荐，简洁且信息密度高）
            text = item.get("summary", "")
            
            # 回退：highlights
            if not text:
                highlights = item.get("highlights", [])
                if highlights and isinstance(highlights, list):
                    text = " ... ".join(str(h) for h in highlights if h)
            
            # 最后：autopromptString
            if not text:
                text = item.get("autopromptString", "")
            
            # 警告：避免使用 text 字段（可能包含完整网页内容）
            # 如果前面都没有内容，记录警告而不是使用 text
            if not text:
                self.log_warning(
                    f"[Web Search] Result missing summary/highlights: {item.get('title', 'unknown')} "
                    f"({item.get('url', 'no-url')}). Available keys: {list(item.keys())}"
                )
                # 仅在调试模式下使用 text 的前 500 字符作为后备
                if item.get("text"):
                    text = item["text"][:500] + "..."
            
            if text:
                provider = str(item.get("provider", "")).strip()
                raw_title = item.get("title", "")
                label = self._web_result_display_label(url=url, provider=provider)
                title = f"[{label}] {raw_title}" if label and raw_title else raw_title
                contexts.append(RetrieveContext(
                    text=text.strip(),
                    source=SourceType.EXA,
                    url=url,
                    title=title,
                    score=item.get("score", item.get("autopromptScore", 1.0)),
                ))

        return contexts

    @staticmethod
    def _web_result_display_label(url: str, provider: str) -> str:
        """Prefer the cited website over the discovery backend in user-facing titles."""
        broad_discovery_providers = {
            "tavily", "metaso", "duckduckgo", "brave", "baidu", "volcengine",
            "google", "bing", "yahoo", "yandex", "jina",
        }
        provider_l = str(provider or "").strip().lower()
        if provider_l in broad_discovery_providers:
            host = urllib.parse.urlparse(url if "://" in url else f"https://{url}").netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                return host
        return str(provider or "").strip()

    async def _ensure_toolkit_instance_ready(self, toolkit: Any, max_retries: int = 3, use_lock: bool = True, force_reinit: bool = False, invalid_context_id: Optional[int] = None) -> None:
        """Ensure specific Web Toolkit instance is initialized and ready, with auto-reconnection logic."""
        
        async def _reinitialize():
            server_name = getattr(toolkit, 'server_name', 'unknown')

            if force_reinit:
                current_context_id = id(getattr(toolkit, '_context', None))
                if invalid_context_id is None or current_context_id == invalid_context_id:
                    self.log_warning(f"Forcing cleanup of MCP toolkit '{server_name}'")
                    if hasattr(toolkit, 'cleanup'):
                        try:
                            await toolkit.cleanup()
                        except Exception as cleanup_error:
                            self.log_debug(f"Cleanup error (ignored): {cleanup_error}")
                    await asyncio.sleep(0.5)
                else:
                    self.log_debug("Skipping cleanup - toolkit already re-initialized by another thread")

            # Try initial initialization; if it throws, fall through to retry loop
            last_error: Optional[Exception] = None
            try:
                if hasattr(toolkit, 'initialize'):
                    await toolkit.initialize()
                
                enabled_tools = toolkit.get_enabled_tools()
                if enabled_tools:
                    return
            except Exception as e:
                last_error = e
                self.log_warning(
                    f"MCP toolkit '{server_name}' initial connection failed: {e}"
                )
            
            for attempt in range(1, max_retries + 1):
                self.log_warning(
                    f"MCP toolkit '{server_name}' not ready. "
                    f"Attempting re-initialization (attempt {attempt}/{max_retries})..."
                )
                
                try:
                    if hasattr(toolkit, 'cleanup'):
                        try:
                            await toolkit.cleanup()
                        except Exception as cleanup_error:
                            self.log_debug(f"Cleanup error (ignored): {cleanup_error}")
                    
                    await asyncio.sleep(1.0 * attempt)
                    
                    if hasattr(toolkit, 'initialize'):
                        await toolkit.initialize()
                    
                    enabled_tools = toolkit.get_enabled_tools()
                    if enabled_tools:
                        self.log_info(f"MCP toolkit '{server_name}' re-initialized successfully on attempt {attempt}")
                        return
                    
                except Exception as e:
                    last_error = e
                    self.log_warning(f"Re-initialization attempt {attempt} failed: {e}")
            
            raise RuntimeError(
                f"MCP toolkit '{server_name}' failed to initialize after {max_retries} attempts. "
                f"Last error: {last_error}"
            )
        
        if use_lock:
            async with self._mcp_init_lock:
                await _reinitialize()
        else:
            await _reinitialize()
    
    def _find_web_search_tool(self, enabled_tools: Dict[str, Any]) -> Optional[Any]:
        """Find the web search tool by configured name or aliases."""
        tool = enabled_tools.get(self.web_tool_method)
        if tool:
            return tool
        
        for name in ['web_search_exa', 'search_exa', 'exa_search']:
            if name in enabled_tools:
                self.log_debug(f"[Web Search] Found tool with alias: {name}")
                self.web_tool_method = name
                return enabled_tools[name]
        
        return None

    async def _call_web_search_async(self, query: str, top_n: int) -> Dict[str, Any]:
        """Call MCP Exa web search with multi-hop fallback across all pool instances.

        重试策略
        --------
        * 每次请求通过 pick_instance() 选取实例，pick_instance() 是同步原子操作，
          保证并发任务在初次选取时均匀分布（round-robin），不会多个任务同时打同一实例。
        * 遇到错误后，将当前实例加入 tried 集合并循环重试，直到遍历完所有可用实例。
        * 速率限制 (429)：冷却 60 s，继续尝试下一实例。
        * 超时：冷却 30 s（超时通常是暂时性网络问题，惩罚比速率限制更轻），继续下一实例。
        * 连接异常：不加冷却（进程可能只是断连），直接跳到下一实例；若无其他实例可用，
          对当前实例执行重连后再尝试一次。
        * 其他未知异常：立即向上抛出，不消化。
        """
        if getattr(self, "web_toolkit_pool", None) is None:
            raise RuntimeError("MCP web toolkit pool not configured")

        # ── 构造请求参数（一次性，所有实例共用） ──────────────────────────────
        call_kwargs: Dict[str, Any] = {"query": query, "numResults": top_n, **self.web_tool_kwargs}
        call_kwargs.setdefault("type", "fast")       # "auto"|"fast"，fast 性能更好
        call_kwargs.setdefault("text", False)         # 不返回完整网页正文，防止上下文爆炸
        call_kwargs.setdefault("summary", True)       # 返回简洁摘要
        call_kwargs.setdefault("highlights", True)    # 返回 LLM 友好高亮片段
        call_kwargs.setdefault("useAutoprompt", True) # Exa 自动优化查询

        self.log_debug(f"[Web Search] Calling {self.web_tool_method} with params: {list(call_kwargs.keys())}")

        async def _execute_tool_call(tool_obj: Any) -> Any:
            if hasattr(tool_obj, "func") and asyncio.iscoroutinefunction(tool_obj.func):
                return await tool_obj.func(**call_kwargs)
            elif asyncio.iscoroutinefunction(tool_obj):
                return await tool_obj(**call_kwargs)
            else:
                return tool_obj(**call_kwargs)

        # ── 多跳 fallback 循环 ────────────────────────────────────────────────
        tried: set = set()
        last_error_str: str = ""
        error_category: str = ""   # "rate_limit" | "timeout" | "connection" | ""

        _CONNECTION_ERRORS = (
            "Client is not connected",
            "Server disconnected",
            "MCP client not available",
            "Session task completed unexpectedly",
            "Session was closed",
        )

        while True:
            # pick_instance 是同步原子操作，并发安全
            idx, toolkit = self.web_toolkit_pool.pick_instance(exclude=tried)

            if toolkit is None:
                # 所有实例已尝试或处于冷却
                cooldowns = self.web_toolkit_pool.cooldown_snapshot()
                if error_category == "rate_limit":
                    raise RuntimeError(
                        f"所有 Exa MCP 实例均已触发速率限制 (429)，请稍后重试或补充 API 配额。"
                        f"冷却状态: {cooldowns}。最后错误: {last_error_str[:150]}"
                    )
                elif error_category == "timeout":
                    raise RuntimeError(
                        f"所有 Exa MCP 实例均已超时，可能并发过高或查询过于复杂。"
                        f"冷却状态: {cooldowns}。最后错误: {last_error_str[:150]}"
                    )
                else:
                    raise RuntimeError(
                        f"所有 Exa MCP 实例均不可用（连接异常或无实例配置）。"
                        f"冷却状态: {cooldowns}。最后错误: {last_error_str[:150]}"
                    )

            tried.add(idx)
            attempt_num = len(tried)
            self.log_debug(f"[Pool] 选择实例 {idx}（第 {attempt_num} 次尝试，已跳过: {tried - {idx}}）")

            try:
                # 首次尝试允许更多重连次数；fallback 实例只尝试一次初始化
                max_init_retries = 3 if attempt_num == 1 else 1
                await self._ensure_toolkit_instance_ready(toolkit, max_retries=max_init_retries, use_lock=True)

                tool = self._find_web_search_tool(toolkit.get_enabled_tools())
                if not tool:
                    self.log_warning(f"[Pool] 实例 {idx} 未找到工具 '{self.web_tool_method}'，跳过")
                    continue

                result = await _execute_tool_call(tool)

                if attempt_num > 1:
                    self.log_info(f"[Pool] 实例 {idx} 请求成功（共经过 {attempt_num} 次尝试）")
                return self._parse_web_search_result(result)

            except Exception as e:
                error_str = str(e)
                last_error_str = error_str

                is_rate_limit = (
                    "429" in error_str
                    or "Too Many Requests" in error_str
                    or "rate limit" in error_str.lower()
                )
                is_timeout = (
                    "timeout of 25000ms exceeded" in error_str
                    or ("timeout" in error_str.lower() and not is_rate_limit)
                )
                is_connection = any(kw in error_str for kw in _CONNECTION_ERRORS)

                if is_rate_limit:
                    error_category = "rate_limit"
                    self.web_toolkit_pool.mark_rate_limited(idx, cooldown_seconds=60.0)
                    self.log_warning(
                        f"[Pool] 实例 {idx} 触发速率限制 (429)，进入 60s 冷却，尝试下一实例。"
                        f"详情: {error_str[:150]}"
                    )
                elif is_timeout:
                    error_category = "timeout"
                    self.web_toolkit_pool.mark_rate_limited(idx, cooldown_seconds=30.0)
                    self.log_warning(
                        f"[Pool] 实例 {idx} 请求超时，进入 30s 冷却，尝试下一实例。"
                        f"详情: {error_str[:150]}"
                    )
                elif is_connection:
                    error_category = "connection"
                    self.log_warning(
                        f"[Pool] 实例 {idx} 连接异常，跳过并尝试下一实例。"
                        f"详情: {error_str[:150]}"
                    )
                    # 若这是池中最后一个可用实例，尝试原地重连后再给一次机会
                    next_probe, _ = self.web_toolkit_pool.pick_instance(exclude=tried)
                    if next_probe is None:
                        self.log_warning(
                            f"[Pool] 无其他可用实例，尝试对实例 {idx} 执行重连..."
                        )
                        try:
                            await self._ensure_toolkit_instance_ready(
                                toolkit,
                                max_retries=1,
                                use_lock=True,
                                force_reinit=True,
                                invalid_context_id=id(getattr(toolkit, "_context", None)),
                            )
                            retry_tool = self._find_web_search_tool(toolkit.get_enabled_tools())
                            if retry_tool:
                                result = await _execute_tool_call(retry_tool)
                                self.log_info(f"[Pool] 实例 {idx} 重连后请求成功。")
                                return self._parse_web_search_result(result)
                        except Exception as reconnect_e:
                            self.log_error(f"[Pool] 实例 {idx} 重连失败: {reconnect_e}")
                            last_error_str = str(reconnect_e)
                else:
                    # 未知异常，立即向上传播，不消化
                    raise

    def _parse_web_search_result(self, result: Any) -> Dict[str, Any]:
        """将 MCP 工具返回值统一解析为 dict 格式。"""
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return {
                    "results": [{
                        "text": result,
                        "summary": result,
                        "title": "Raw Search Result",
                        "score": 1.0,
                    }]
                }
        return result if isinstance(result, dict) else {"results": []}

    async def _call_skill_tree_search_async(self, query: str, top_n: int) -> Dict[str, Any]:
        """Call local skill-tree search toolkit."""
        if not getattr(self, "skill_tree_toolkit", None):
            raise RuntimeError("SkillTree web toolkit not configured")
        toolkit = self.skill_tree_toolkit
        if hasattr(toolkit, "search") and asyncio.iscoroutinefunction(toolkit.search):
            result = await toolkit.search(query=query, top_n=top_n)
        elif hasattr(toolkit, "search"):
            result = toolkit.search(query=query, top_n=top_n)
        else:
            raise RuntimeError("SkillTree toolkit missing `search` method")
        return self._parse_web_search_result(result)

    def _score_web_results_quality(self, web_results: Dict[str, Any]) -> Dict[str, int]:
        results = web_results.get("results", [])
        if not isinstance(results, list):
            return {"result_count": 0, "unique_sources": 0}

        urls = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if url:
                urls.add(url)

        return {"result_count": len(results), "unique_sources": len(urls)}

    def _count_citable_web_results(self, web_results: Dict[str, Any]) -> int:
        results = web_results.get("results", [])
        if not isinstance(results, list):
            return 0
        return sum(
            1 for item in results
            if isinstance(item, dict) and str(item.get("url", "")).strip()
        )

    def _relax_web_query(self, query: str) -> str:
        """Relax strict search operators for fallback recall."""
        if not query:
            return query

        relaxed = query
        relaxed = re.sub(r"\bsite:[^\s]+", " ", relaxed, flags=re.IGNORECASE)
        relaxed = re.sub(r"\b(?:OR|AND)\b", " ", relaxed, flags=re.IGNORECASE)
        relaxed = relaxed.replace("|", " ").replace("(", " ").replace(")", " ")
        relaxed = re.sub(r"\s+", " ", relaxed).strip()
        return relaxed or query

    def _generate_web_fallback_queries(self, query: str) -> List[str]:
        """Generate degraded fallback queries to reduce empty-result risk."""
        if not query:
            return []

        variants: List[str] = []
        base = self._relax_web_query(query)
        if base and base != query:
            variants.append(base)

        no_year = re.sub(r"\b20\d{2}\b", " ", base)
        no_year = re.sub(r"\s+", " ", no_year).strip()
        if no_year and no_year not in variants:
            variants.append(no_year)

        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9][a-zA-Z0-9_\-\.]*", no_year)
        stop = {"site", "or", "and", "news", "today", "latest", "2024", "2025", "2026", "2027"}
        filtered = [t for t in tokens if t.lower() not in stop]
        if filtered:
            focused = " ".join(filtered[:8]).strip()
            if focused and focused not in variants:
                variants.append(focused)

        low = no_year.lower()
        if any(k in no_year for k in ("36氪", "36kr")):
            tailored = "36kr 教育 AI 教育科技"
            if tailored not in variants:
                variants.append(tailored)
        elif any(k in low for k in ("venturebeat", "techcrunch", "the verge", "wired")):
            tailored = "AI education edtech news"
            if tailored not in variants:
                variants.append(tailored)

        if any(
            k in no_year
            for k in (
                "土地财政", "土地出让", "房产税", "房地产税", "普通商品房",
                "利润税", "改善型住房", "刘尚希", "贾康",
            )
        ):
            for tailored in (
                "刘尚希 贾康 房产税 征收环节 税率",
                "普通商品房 利润税 土地财政 转型",
                "土地财政 转型 房产税 土地出让金 改善型住房",
                "2021 国有土地使用权出让收入 财政部 地方一般公共预算收入",
            ):
                if tailored not in variants:
                    variants.append(tailored)

        deduped: List[str] = []
        seen = set()
        for v in variants:
            key = v.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(v)
        return deduped[:6]

    def _web_route_note(self, web_results: Dict[str, Any]) -> str:
        route = web_results.get("skill_tree_route")
        if not isinstance(route, dict):
            return ""
        branches = route.get("selected_branches")
        if not isinstance(branches, list) or not branches:
            return ""
        branches_text = ",".join(str(b) for b in branches if b)
        return f" route={branches_text}" if branches_text else ""

    def _needs_exa_backfill(self, web_results: Dict[str, Any], top_n: int) -> bool:
        if self.web_unlimited_mode:
            return True
        quality = self._score_web_results_quality(web_results)
        result_floor = min(top_n, self.web_backfill_min_results)
        return (
            quality["result_count"] < result_floor
            or quality["unique_sources"] < self.web_backfill_min_sources
        )

    def _merge_web_results(
        self, primary: Dict[str, Any], secondary: Dict[str, Any], limit: Optional[int]
    ) -> Dict[str, Any]:
        merged: List[Dict[str, Any]] = []
        seen: set[str] = set()

        def push(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", "")).strip()
                title = str(item.get("title", "")).strip()
                key = url or title
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)

        push(primary.get("results"))
        push(secondary.get("results"))

        out = dict(primary)
        if limit is None or int(limit) <= 0:
            out["results"] = merged
        else:
            out["results"] = merged[: max(1, int(limit))]
        out["fallback_merged"] = True
        return out

    async def _retrieve_web_by_backend(
        self, query: str, top_n: int
    ) -> Tuple[Dict[str, Any], str]:
        backend = self.web_backend
        self.log_info(
            f"[Web Backend] backend={backend}, top_n={top_n}, query={query[:120]}"
        )

        if backend == "exa":
            return await self._call_web_search_async(query, top_n=top_n), "exa"

        if backend == "skill_tree":
            return await self._call_skill_tree_search_async(query, top_n=top_n), "skill_tree"

        if backend == "mixed":
            try:
                primary = await self._call_skill_tree_search_async(query, top_n=top_n)
            except Exception as e:
                self.log_warning(
                    f"[Web Mixed] skill_tree primary failed, fallback to Exa: {e}"
                )
                exa_only = await self._call_web_search_async(query, top_n=top_n)
                exa_only["backend"] = "mixed(exa_only)"
                return exa_only, "mixed"
            if self._needs_exa_backfill(primary, top_n):
                try:
                    exa = await self._call_web_search_async(query, top_n=top_n)
                    merged = self._merge_web_results(primary, exa, limit=top_n)
                    merged["backend"] = "mixed(skill_tree+exa)"
                    return merged, "mixed"
                except Exception as e:
                    self.log_warning(
                        f"[Web Mixed] EXA backfill failed, using skill_tree only: {e}"
                    )
            primary["backend"] = "mixed(skill_tree_only)"
            return primary, "mixed"

        raise ValueError(f"Invalid web_backend '{backend}'. Use exa|skill_tree|mixed.")

    @contextmanager
    def _trace_span(self, name: str, inputs: Optional[Dict[str, Any]] = None):
        """Context manager for MLflow spans."""
        if self.mlflow_enabled and mlflow_start_span:
            with mlflow_start_span(name=name) as span:
                try:
                    if inputs:
                        span.set_inputs(inputs)
                    yield span
                except Exception as e:
                    self.log_debug(f"[MLflow] Error in span '{name}': {e}")
                    if span:
                        try:
                            span.set_attributes({"error": True, "error.message": str(e)[:500]})
                        except Exception:
                            pass
                    raise
        else:
            yield None

    def _log_span_results(self, span: Any, results: List[Any], source_type: str):
        """Log retrieval results to span for observability."""
        if not span or not results:
            return
        
        display_results = []
        for r in results[:5]:
            if source_type == "ragflow":
                display_results.append({
                    "text": r.get("content", "")[:200] + "...",
                    "score": r.get("score", 0.0),
                    "doc": r.get("document_name", "")
                })
            else:
                display_results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "score": r.get("score", 0.0)
                })
        
        try:
            span.set_outputs({
                "source": source_type,
                "count": len(results),
                "top_results": display_results
            })
            span.set_attributes({
                "roma.source": source_type,
                "roma.result_count": len(results),
                "roma.top1_score": results[0].get("score", 0.0) if results else 0.0
            })
        except Exception as e:
            self.log_debug(f"[MLflow] Failed to set outputs for span: {e}")

    def _retrieve_rag_step(self, query: str, top_n: int, span_name: str = "ragflow_retrieve") -> List[Dict[str, Any]]:
        with self._trace_span(span_name, {"query": query, "top_n": top_n}) as span:
            chunks = self.ragflow_toolkit.retrieve(query, top_n=top_n)
            self._log_span_results(span, chunks, "ragflow")
            return chunks

    async def _retrieve_web_step(
        self,
        query: str,
        top_n: int,
        span_name: str = "web_search",
        min_citable: Optional[int] = None,
    ) -> Dict[str, Any]:
        if self.web_unlimited_mode:
            top_n = max(int(top_n), int(self.web_unlimited_top_n))
        with self._trace_span(span_name, {"query": query, "top_n": top_n}) as span:
            web_results, source_tag = await self._retrieve_web_by_backend(query, top_n=top_n)

            target_citable = int(min_citable or top_n)
            if (
                self.web_url_backfill_enabled
                and target_citable > 0
                and self._count_citable_web_results(web_results) < target_citable
            ):
                merged = dict(web_results)
                for round_idx in range(1, self.web_url_backfill_max_rounds + 1):
                    current_citable = self._count_citable_web_results(merged)
                    if current_citable >= target_citable:
                        break
                    proposed_top_n = max(
                        top_n + round_idx * top_n,
                        int(top_n * (self.web_url_backfill_multiplier ** round_idx)),
                    )
                    next_top_n = (
                        proposed_top_n
                        if self.web_unlimited_mode
                        else min(self.web_url_backfill_cap, proposed_top_n)
                    )
                    if next_top_n <= top_n:
                        break
                    try:
                        extra_results, _ = await self._retrieve_web_by_backend(query, top_n=next_top_n)
                        merged = self._merge_web_results(
                            merged,
                            extra_results,
                            limit=None
                            if self.web_unlimited_mode
                            else max(self.web_url_backfill_cap, next_top_n),
                        )
                        merged["url_backfill_rounds"] = round_idx
                        merged["url_backfill_target"] = target_citable
                    except Exception as e:
                        self.log_warning(
                            f"[Web Search] URL backfill round {round_idx} failed: {e}"
                        )
                        break
                web_results = merged

            if (
                self.web_relax_query_on_empty
                and self._count_citable_web_results(web_results) == 0
            ):
                relaxed_query = self._relax_web_query(query)
                if relaxed_query and relaxed_query != query:
                    try:
                        relaxed_results, _ = await self._retrieve_web_by_backend(
                            relaxed_query, top_n=top_n
                        )
                        web_results = self._merge_web_results(
                            web_results,
                            relaxed_results,
                            limit=None if self.web_unlimited_mode else max(1, top_n),
                        )
                        web_results["relaxed_query_used"] = relaxed_query
                        self.log_info(
                            f"[Web Search] Empty results on strict query; retried with relaxed query: {relaxed_query}"
                        )
                    except Exception as e:
                        self.log_warning(
                            f"[Web Search] Relaxed-query retry failed: {e}"
                        )

            if self._count_citable_web_results(web_results) == 0:
                for alt_query in self._generate_web_fallback_queries(query):
                    if self._count_citable_web_results(web_results) > 0:
                        break
                    try:
                        alt_results, _ = await self._retrieve_web_by_backend(
                            alt_query, top_n=top_n
                        )
                        web_results = self._merge_web_results(
                            web_results,
                            alt_results,
                            limit=None if self.web_unlimited_mode else max(1, top_n),
                        )
                        self.log_info(
                            f"[Web Search] Empty results fallback retry with degraded query: {alt_query}"
                        )
                    except Exception as e:
                        self.log_warning(
                            f"[Web Search] Degraded-query retry failed ({alt_query}): {e}"
                        )

            results = web_results.get("results", [])
            self._log_span_results(span, results, source_tag)
            return web_results

    async def adaptive_retrieve_async(
        self,
        query: str,
        mode: Optional[str] = None,
        top_n: Optional[int] = None,
    ) -> RetrieveResult:
        """
        Adaptive retrieval (async) with auto-routing logic.
        
        Logic:
        1. "web": Force web search, skip RAG.
        2. "rag" / "hybrid" / "auto": Execute RAG first.
        3. "auto": Use Judge (LLM/Heuristic) to decide next step based on RAG quality.
           - High confidence: Return RAG.
           - Medium: Return Hybrid.
           - Low: Return Web only.
        """
        start = time.time()
        
        if self.default_mode != "auto":
            if mode and mode != self.default_mode:
                self.log_info(
                    f"[Mode Override] LLM requested mode='{mode}', "
                    f"but config enforces default_mode='{self.default_mode}'. Using '{self.default_mode}'."
                )
            mode = self.default_mode
        else:
            mode = mode or self.default_mode
        top_n = top_n or self.top_n_default
        if self.web_unlimited_mode and mode in ("web", "hybrid", "auto"):
            top_n = max(int(top_n), int(self.web_unlimited_top_n))
        
        active_span = mlflow.get_current_active_span() if self.mlflow_enabled else None

        rag_chunks: List[Dict[str, Any]] = []
        web_results: Dict[str, Any] = {"results": []}
        contexts: List[RetrieveContext] = []
        decision = DecisionType.RAG
        confidence = 0.0
        web_triggered = False
        decision_reason = f"mode={mode}"

        if mode == "web":
            web_results = await self._retrieve_web_step(query, top_n, min_citable=top_n)
            contexts = self._format_web_contexts(web_results)
            decision = DecisionType.WEB
            confidence = 1.0
            web_triggered = True
            decision_reason = f"mode=web (forced, skip RAG){self._web_route_note(web_results)}"
        
        else:
            # Step 1: Always execute RAG for rag/hybrid/auto
            rag_chunks = self._retrieve_rag_step(query, top_n, span_name="ragflow_initial_retrieve")
            
            # Step 2: Calculate confidence
            # Optimization: Only run expensive LLM evaluation if mode is "auto".
            # For forced modes ("rag", "hybrid"), we fallback to heuristic to save latency/cost.
            force_heuristic = (mode != "auto")
            confidence, conf_reason = self._calculate_confidence(query, rag_chunks, force_heuristic=force_heuristic)
            
            # Step 3: Decision
            if mode == "rag":
                contexts = self._format_ragflow_contexts(rag_chunks)
                decision = DecisionType.RAG
                decision_reason = f"mode=rag (forced). Judge eval: {conf_reason}, confidence={confidence:.2f}"
            
            elif mode == "hybrid":
                rag_top_n = max(1, top_n // 2)
                web_top_n = max(1, top_n - rag_top_n)
                
                rag_chunks_hybrid = rag_chunks[:rag_top_n]
                web_results = await self._retrieve_web_step(
                    query,
                    web_top_n,
                    span_name="web_search_hybrid",
                    min_citable=web_top_n,
                )

                contexts = self._format_ragflow_contexts(rag_chunks_hybrid) + self._format_web_contexts(web_results)
                decision = DecisionType.HYBRID
                web_triggered = True
                decision_reason = (
                    f"mode=hybrid (forced). Judge eval: {conf_reason}, confidence={confidence:.2f}"
                    f"{self._web_route_note(web_results)}"
                )
            
            elif mode == "auto":
                if confidence >= 0.7:
                    # High confidence -> RAG only
                    contexts = self._format_ragflow_contexts(rag_chunks)
                    decision = DecisionType.RAG
                    decision_reason = f"High confidence ({confidence:.2f} >= 0.7), RAGFlow sufficient. {conf_reason}"
                    self.log_info(f"[Judge Decision] RAG only (confidence={confidence:.2f})")
                
                elif confidence >= 0.4:
                    # Medium confidence -> Hybrid
                    rag_top_n = max(1, top_n // 2)
                    web_top_n = max(1, top_n - rag_top_n)
                    
                    rag_chunks_hybrid = rag_chunks[:rag_top_n]
                    web_results = await self._retrieve_web_step(
                        query,
                        web_top_n,
                        span_name="web_search_hybrid",
                        min_citable=web_top_n,
                    )

                    contexts = self._format_ragflow_contexts(rag_chunks_hybrid) + self._format_web_contexts(web_results)
                    decision = DecisionType.HYBRID
                    web_triggered = True
                    decision_reason = (
                        f"Medium confidence ({confidence:.2f} in [0.4, 0.7)), using Hybrid. {conf_reason}"
                        f"{self._web_route_note(web_results)}"
                    )
                    self.log_info(f"[Judge Decision] Hybrid (confidence={confidence:.2f})")

                else:
                    # Low confidence -> Web only
                    web_results = await self._retrieve_web_step(
                        query,
                        top_n,
                        span_name="web_search_only",
                        min_citable=top_n,
                    )
                    contexts = self._format_web_contexts(web_results)
                    decision = DecisionType.WEB
                    web_triggered = True
                    decision_reason = (
                        f"Low confidence ({confidence:.2f} < 0.4), discard RAG and use Web only. {conf_reason}"
                        f"{self._web_route_note(web_results)}"
                    )
                    self.log_info(f"[Judge Decision] Web only, RAG discarded (confidence={confidence:.2f})")
            
            else:
                raise ValueError(f"Invalid mode '{mode}'. Use 'rag', 'web', 'hybrid', or 'auto'.")

        if active_span:
            active_span.set_attributes({
                "roma.decision": decision.value,
                "roma.confidence": confidence,
                "roma.web_triggered": web_triggered,
                "roma.reason": decision_reason,
                "roma.mode": mode
            })

        from roma_dspy.core.context import ExecutionContext
        sources_list = [ctx.url for ctx in contexts if ctx.url]
        ExecutionContext.add_sources(sources_list)

        duration_ms = int((time.time() - start) * 1000)
        debug = RetrieveDebugInfo(
            trigger_reason=decision_reason,
            rag_top1_score=rag_chunks[0].get("score", 0.0) if rag_chunks else 0.0,
            rag_result_count=len(rag_chunks),
            web_triggered=web_triggered,
            duration_ms=duration_ms,
        )
        
        return RetrieveResult(
            query=query,
            decision=decision,
            confidence=confidence,
            contexts=contexts,
            sources=[ctx.url for ctx in contexts],
            debug=debug,
        )

    def adaptive_retrieve(
        self,
        query: str,
        mode: Optional[str] = None,
        top_n: Optional[int] = None,
    ) -> RetrieveResult:
        """Sync wrapper for adaptive_retrieve_async."""
        return asyncio.run(self.adaptive_retrieve_async(query, mode, top_n))

    async def adaptive_retrieve_json_async(
        self,
        query: str,
        mode: Optional[str] = None,
        top_n: Optional[int] = None,
    ) -> str:
        """Return retrieval results in JSON format (async)."""
        result = await self.adaptive_retrieve_async(query, mode=mode, top_n=top_n)
        return json.dumps(result.to_dict(), ensure_ascii=False)

    def adaptive_retrieve_json(
        self,
        query: str,
        mode: Optional[str] = None,
        top_n: Optional[int] = None,
    ) -> str:
        """Return retrieval results in JSON format (sync wrapper)."""
        return asyncio.run(self.adaptive_retrieve_json_async(query, mode, top_n))

    async def search_adaptive_impl(
        self,
        query: Optional[str] = None,
        queries: Optional[Union[str, List[str]]] = None,
        mode: Optional[str] = None,
        top_n: Optional[int] = None
    ) -> str:
        """
        Adaptive search implementation (async).
        Internal function exposed via dspy.Tool.

        Raw search results are automatically persisted as artifacts in the
        execution's ``artifacts/`` directory so that downstream WRITE tasks
        can consume first-hand evidence without information loss.
        """
        final_query = query
        if not final_query and queries:
            if isinstance(queries, list):
                final_query = " ".join([str(q) for q in queries if q])
            else:
                final_query = str(queries)
        
        if not final_query:
            return "[Error] Please provide a valid search query in the 'query' argument."

        try:
            result = await self.adaptive_retrieve_async(query=final_query, mode=mode, top_n=top_n)
            formatted_text = self._format_contexts_for_llm(result)

            await self._auto_persist_results(result, final_query)

            return formatted_text
        except Exception as e:
            error_msg = f"### [检索失败]\n> {str(e)}"
            self.log_error(f"Adaptive search error: {e}")
            return error_msg
    
    # ------------------------------------------------------------------
    # Auto-persistence: save raw search results as artifacts
    # ------------------------------------------------------------------

    async def _auto_persist_results(
        self, result: RetrieveResult, query: str
    ) -> Optional[str]:
        """Persist raw search results to artifacts/ so downstream WRITE
        tasks can consume first-hand evidence.

        Returns the absolute path of the saved file, or ``None`` on failure.
        """
        if not result.contexts:
            return None

        try:
            from roma_dspy.core.context import ExecutionContext

            ctx = ExecutionContext.get()
            if not ctx or not ctx.file_storage:
                self.log_debug(
                    "No ExecutionContext available, skipping auto-persist"
                )
                return None

            artifacts_dir = Path(ctx.file_storage.get_artifacts_path())
            artifacts_dir.mkdir(parents=True, exist_ok=True)

            topic_slug = self._slugify_query(query)
            filename = f"retrieve_{topic_slug}.md"
            file_path = artifacts_dir / filename

            counter = 1
            while file_path.exists():
                counter += 1
                filename = f"retrieve_{topic_slug}_{counter}.md"
                file_path = artifacts_dir / filename

            content = self._format_for_persistence(result, query)
            file_path.write_text(content, encoding="utf-8")

            registry = ctx.artifact_registry
            if registry:
                from roma_dspy.core.artifacts import ArtifactBuilder
                from roma_dspy.types import ArtifactType

                builder = ArtifactBuilder()
                artifact = await builder.build(
                    name=topic_slug,
                    artifact_type=ArtifactType.DATA_FETCH,
                    storage_path=str(file_path.resolve()),
                    created_by_task=(
                        ExecutionContext.resolve_artifact_creator() or "unknown"
                    ),
                    created_by_module="AdaptiveRetrieveToolkit",
                    description=f"Raw search results for: {query[:120]}",
                )
                await registry.register(artifact)

            logger.info(
                f"[AUTO-PERSIST] Saved raw search results → {file_path.relative_to(Path(ctx.file_storage.root)).as_posix()}"
            )
            return str(file_path)

        except Exception as e:
            self.log_warning(f"Auto-persist failed (non-fatal): {e}")
            return None

    @staticmethod
    def _slugify_query(query: str, max_length: int = 80) -> str:
        """Derive a filesystem-safe, topic-descriptive slug from *query*.

        Strategy:
        1. Extract all valid tokens (Chinese, English, Numbers) from the first 20 chars to preserve context.
        2. For the rest of the query, extract English words and numbers.
        3. If the suffix English + numeric tokens are descriptive enough (>=8 chars), use them alone.
        4. Otherwise include Chinese characters for the suffix as well.
        5. Truncate to *max_length* and strip trailing underscores.
        """
        prefix_tokens = []
        en_suffix_tokens = []
        all_suffix_tokens = []

        for match in re.finditer(r"[a-zA-Z][a-zA-Z0-9]*|[0-9]+|[\u4e00-\u9fff]+", query):
            token = match.group(0)
            if match.start() < 20:
                prefix_tokens.append(token)
            else:
                all_suffix_tokens.append(token)
                # Check if token is strictly English/Numeric
                if re.match(r"^[a-zA-Z][a-zA-Z0-9]*$|^[0-9]+$", token):
                    en_suffix_tokens.append(token)

        prefix_slug = "_".join(prefix_tokens).lower()
        en_slug = "_".join(en_suffix_tokens).lower()

        if len(en_slug) >= 8:
            suffix_slug = en_slug
        else:
            suffix_slug = "_".join(all_suffix_tokens).lower() if all_suffix_tokens else ""

        if prefix_slug and suffix_slug:
            slug = f"{prefix_slug}_{suffix_slug}"
        else:
            slug = prefix_slug or suffix_slug or "query"

        if len(slug) > max_length:
            slug = slug[:max_length].rstrip("_")
        return slug or "query"

    def _format_for_persistence(self, result: RetrieveResult, query: str) -> str:
        """Produce a Markdown file optimised for downstream WRITE consumption.

        Key differences from ``_format_contexts_for_llm``:
        - Uses ``[Source: URL]`` citation format that WRITE tasks expect.
        - Preserves **full** text content from every search result.
        - Includes per-result metadata (title, score) for evidence evaluation.
        """
        parts: list[str] = [
            f"# 检索结果: {query}",
            "",
            f"- **检索模式**: {result.decision.value}",
            f"- **置信度**: {result.confidence:.2f}",
            f"- **结果数量**: {len(result.contexts)}",
            "",
        ]

        rag_contexts = [c for c in result.contexts if c.source == SourceType.RAGFLOW]
        web_contexts = [c for c in result.contexts if c.source == SourceType.EXA]

        if rag_contexts:
            parts.append(f"## 内部知识库 ({len(rag_contexts)} 条)")
            parts.append("")
            for i, ctx in enumerate(rag_contexts, 1):
                title = ctx.title or f"文档片段 {i}"
                parts.append(f"### [{i}] {title}")
                if ctx.url:
                    parts.append(f"[Source: {ctx.url}]")
                parts.append(f"相关度: {ctx.score:.2f}")
                parts.append("")
                parts.append(ctx.text.strip())
                parts.append("")

        if web_contexts:
            parts.append(f"## 互联网搜索 ({len(web_contexts)} 条)")
            parts.append("")
            for i, ctx in enumerate(web_contexts, 1):
                title = ctx.title or "无标题网页"
                parts.append(f"### [{i}] {title}")
                if ctx.url:
                    parts.append(f"[Source: {ctx.url}]")
                parts.append("")
                parts.append(ctx.text.strip())
                parts.append("")

        return "\n".join(parts)

    def _format_contexts_for_llm(self, result: RetrieveResult) -> str:
        """Format retrieval results for LLM consumption."""
        if not result.contexts:
            return f"[检索结果为空]\n查询: {result.query}\n决策: {result.decision.value}"
        
        rag_contexts = [c for c in result.contexts if c.source == SourceType.RAGFLOW]
        web_contexts = [c for c in result.contexts if c.source == SourceType.EXA]
        
        parts = []
        
        parts.append(f"### 检索报告")
        parts.append(f"- **决策模式**: {result.decision.value.upper()}")
        parts.append(f"- **置信度**: {result.confidence:.2f}")
        parts.append(f"- **触发原因**: {result.debug.trigger_reason}")
        parts.append("")

        if rag_contexts:
            parts.append(f"### 📚 内部知识库 ({len(rag_contexts)} 条)")
            for i, ctx in enumerate(rag_contexts, 1):
                title = ctx.title if ctx.title else f"文档片段 {i}"
                parts.append(f"#### [{i}] {title}")
                parts.append(f"> **相关度**: {ctx.score:.2f}")
                parts.append(f"> {ctx.text.strip()}")
                if ctx.url:
                    parts.append(f"> *来源: {ctx.url}*")
                parts.append("")
        
        if web_contexts:
            parts.append(f"### 🌐 互联网搜索 ({len(web_contexts)} 条)")
            for i, ctx in enumerate(web_contexts, 1):
                title = ctx.title or "无标题网页"
                url = ctx.url
                score = ctx.score
                content = ctx.text.strip()
                
                parts.append(f"#### [{i}] {title}")
                parts.append(f"**URL**: {url}")
                parts.append("")
                parts.append(content)

        return "\n".join(parts)

    async def cleanup(self) -> None:
        """Clean up internal toolkits."""
        # 统一从连接池清理所有 EXA MCP 实例，避免残留子进程
        pool = getattr(self, "web_toolkit_pool", None)
        if pool is not None:
            for toolkit in pool.all_toolkits():
                if hasattr(toolkit, "cleanup"):
                    try:
                        await toolkit.cleanup()
                    except Exception as e:
                        self.log_warning(f"Error cleaning up pooled web toolkit: {e}")
        elif hasattr(self, 'web_toolkit') and self.web_toolkit and hasattr(self.web_toolkit, 'cleanup'):
            # 旧路径兜底（理论上不会进入）
            try:
                await self.web_toolkit.cleanup()
            except Exception as e:
                self.log_warning(f"Error cleaning up web_toolkit: {e}")
                
        if hasattr(self, 'ragflow_toolkit') and self.ragflow_toolkit and hasattr(self.ragflow_toolkit, 'cleanup'):
            try:
                await self.ragflow_toolkit.cleanup()
            except Exception as e:
                self.log_warning(f"Error cleaning up ragflow_toolkit: {e}")

        if (
            hasattr(self, "skill_tree_toolkit")
            and self.skill_tree_toolkit
            and hasattr(self.skill_tree_toolkit, "cleanup")
        ):
            try:
                maybe = self.skill_tree_toolkit.cleanup()
                if asyncio.iscoroutine(maybe):
                    await maybe
            except Exception as e:
                self.log_warning(f"Error cleaning up skill_tree_toolkit: {e}")

    def search_adaptive(
        self,
        query: Optional[str] = None,
        queries: Optional[Union[str, List[str]]] = None,
        mode: Optional[str] = None,
        top_n: Optional[int] = None
    ) -> str:
        """Adaptive search combining internal knowledge base (RAGFlow) and external web search.

        This tool performs information retrieval by intelligently routing between internal and external sources.
        It supports automatic routing ("auto"), forced internal ("rag"), forced external ("web"), or hybrid ("hybrid") modes.

        Args:
            query: The search query.
            queries: Legacy alias for query.
            mode: Retrieval mode ("auto", "rag", "web", "hybrid"). 
                  If None, uses default_mode from configuration (defaults to "auto").
            top_n: Number of results to return. If omitted, uses profile top_n_default.

        Returns:
            Formatted string with retrieval decision, results, and citations.
        """
        return asyncio.run(self.search_adaptive_impl(query=query, queries=queries, mode=mode, top_n=top_n))
