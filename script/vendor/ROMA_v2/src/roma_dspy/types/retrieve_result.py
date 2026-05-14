"""
检索结果数据模型

Module 0 - Step 0.1: 定义统一输出格式 RetrieveResult
这是所有检索相关代码的输出契约，用于 RAGFlow + Web Search 的自适应检索集成。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Literal, Optional
from enum import Enum


class DecisionType(str, Enum):
    """检索决策类型"""
    RAG = "rag"  # 只使用内部知识库（RAGFlow）
    WEB = "web"  # 只使用 Web Search
    HYBRID = "hybrid"  # 内部 + 外部混合


class SourceType(str, Enum):
    """证据来源类型"""
    RAGFLOW = "ragflow"  # 来自 RAGFlow 内部知识库
    EXA = "exa"  # 来自 Exa Web Search


@dataclass
class RetrieveContext:
    """
    单条检索证据的结构
    
    Attributes:
        text: 证据正文内容
        source: 证据来源（"ragflow" 或 "exa"）
        url: 可追溯引用（URL 或 ragflow://...）
        title: 证据标题（可选）
        score: 检索相关性分数（可选，0-1）
    """
    text: str
    source: SourceType
    url: str
    title: Optional[str] = ""
    score: Optional[float] = 0.0

    def to_dict(self) -> Dict:
        """转换为字典格式"""
        return {
            "text": self.text,
            "source": self.source.value if isinstance(self.source, SourceType) else self.source,
            "url": self.url,
            "title": self.title,
            "score": self.score,
        }


@dataclass
class RetrieveDebugInfo:
    """
    检索过程的调试信息
    
    Attributes:
        trigger_reason: 决策原因说明
        rag_top1_score: RAGFlow 最高分（如果有）
        rag_result_count: RAGFlow 返回数量
        web_triggered: 是否触发了 Web Search
        duration_ms: 总耗时（毫秒）
        timestamp: 检索时间戳（可选）
    """
    trigger_reason: str
    rag_top1_score: float = 0.0
    rag_result_count: int = 0
    web_triggered: bool = False
    duration_ms: int = 0
    timestamp: Optional[str] = None

    def to_dict(self) -> Dict:
        """转换为字典格式"""
        result = {
            "trigger_reason": self.trigger_reason,
            "rag_top1_score": self.rag_top1_score,
            "rag_result_count": self.rag_result_count,
            "web_triggered": self.web_triggered,
            "duration_ms": self.duration_ms,
        }
        if self.timestamp:
            result["timestamp"] = self.timestamp
        return result


@dataclass
class RetrieveResult:
    """
    统一检索结果输出格式（Module 0 核心契约）
    
    所有检索相关代码（RAGFlow、Exa、CRAG 路由）都必须返回此格式。
    
    Attributes:
        query: 原始查询问题
        decision: 检索决策类型（"rag" | "web" | "hybrid"）
        confidence: 检索质量置信度（0-1）
        contexts: 证据数组列表
        sources: 扁平 URL 列表（给 Aggregator 使用）
        debug: 调试信息对象
    
    Example:
        >>> result = RetrieveResult(
        ...     query="AI教育现状",
        ...     decision=DecisionType.HYBRID,
        ...     confidence=0.65,
        ...     contexts=[
        ...         RetrieveContext(
        ...             text="AI教育正在快速发展...",
        ...             source=SourceType.RAGFLOW,
        ...             url="ragflow://kb/abc123/doc/doc456#chunk=chunk789",
        ...             score=0.85
        ...         )
        ...     ],
        ...     sources=["ragflow://kb/abc123/doc/doc456#chunk=chunk789"],
        ...     debug=RetrieveDebugInfo(
        ...         trigger_reason="confidence=0.65, hybrid mode triggered",
        ...         rag_result_count=3,
        ...         web_triggered=True,
        ...         duration_ms=450
        ...     )
        ... )
    """
    query: str
    decision: DecisionType
    confidence: float
    contexts: List[RetrieveContext]
    sources: List[str]
    debug: RetrieveDebugInfo

    def to_dict(self) -> Dict:
        """
        转换为字典格式（兼容现有代码）
        
        Returns:
            完整的字典表示，可序列化为 JSON
        """
        return {
            "query": self.query,
            "decision": self.decision.value if isinstance(self.decision, DecisionType) else self.decision,
            "confidence": self.confidence,
            "contexts": [ctx.to_dict() for ctx in self.contexts],
            "sources": self.sources,
            "debug": self.debug.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "RetrieveResult":
        """
        从字典创建 RetrieveResult 实例
        
        Args:
            data: 包含所有必需字段的字典
            
        Returns:
            RetrieveResult 实例
        """
        # 解析 contexts
        contexts = []
        for ctx_data in data.get("contexts", []):
            contexts.append(RetrieveContext(
                text=ctx_data["text"],
                source=SourceType(ctx_data["source"]) if isinstance(ctx_data["source"], str) else ctx_data["source"],
                url=ctx_data["url"],
                title=ctx_data.get("title", ""),
                score=ctx_data.get("score", 0.0),
            ))
        
        # 解析 debug
        debug_data = data.get("debug", {})
        debug = RetrieveDebugInfo(
            trigger_reason=debug_data.get("trigger_reason", ""),
            rag_top1_score=debug_data.get("rag_top1_score", 0.0),
            rag_result_count=debug_data.get("rag_result_count", 0),
            web_triggered=debug_data.get("web_triggered", False),
            duration_ms=debug_data.get("duration_ms", 0),
            timestamp=debug_data.get("timestamp"),
        )
        
        return cls(
            query=data["query"],
            decision=DecisionType(data["decision"]) if isinstance(data["decision"], str) else data["decision"],
            confidence=data["confidence"],
            contexts=contexts,
            sources=data.get("sources", []),
            debug=debug,
        )

    def __repr__(self) -> str:
        """字符串表示，便于调试"""
        return (
            f"RetrieveResult(query='{self.query[:30]}...', "
            f"decision={self.decision.value}, "
            f"confidence={self.confidence:.2f}, "
            f"contexts={len(self.contexts)}, "
            f"sources={len(self.sources)})"
        )

