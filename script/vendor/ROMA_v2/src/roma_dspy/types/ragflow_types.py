"""
RAGFlow 相关类型定义和 URI 工具

Module 0 - Step 0.2: 确定 RAGFlow 内部引用 URI 规范
定义内部引用格式：ragflow://kb/<kb_id>/doc/<doc_id>#chunk=<chunk_id>
"""

from dataclasses import dataclass
from typing import Optional
import re


# URI 规范常量
RAGFLOW_URI_SCHEME = "ragflow://"
RAGFLOW_URI_PATTERN = r"^ragflow://kb/([^/]+)/doc/([^#]+)(?:#chunk=(.+))?$"


@dataclass
class RAGFlowReference:
    """
    RAGFlow 内部引用结构
    
    Attributes:
        kb_id: 知识库 ID
        doc_id: 文档 ID
        chunk_id: chunk ID（可选）
        original_url: 原始 URL（如果文档有来源 URL，可选）
    """
    kb_id: str
    doc_id: str
    chunk_id: Optional[str] = None
    original_url: Optional[str] = None

    def to_uri(self) -> str:
        """
        生成标准 RAGFlow URI
        
        格式：ragflow://kb/<kb_id>/doc/<doc_id>#chunk=<chunk_id>
        
        Returns:
            标准格式的 URI 字符串
            
        Example:
            >>> ref = RAGFlowReference(kb_id="abc123", doc_id="doc456", chunk_id="chunk789")
            >>> ref.to_uri()
            'ragflow://kb/abc123/doc/doc456#chunk=chunk789'
        """
        base_uri = f"{RAGFLOW_URI_SCHEME}kb/{self.kb_id}/doc/{self.doc_id}"
        if self.chunk_id:
            base_uri += f"#chunk={self.chunk_id}"
        return base_uri

    def to_display_url(self) -> str:
        """
        返回用于显示的 URL（优先使用 original_url）
        
        Returns:
            显示用的 URL 字符串
        """
        return self.original_url if self.original_url else self.to_uri()

    @classmethod
    def from_uri(cls, uri: str) -> Optional["RAGFlowReference"]:
        """
        从 URI 字符串解析 RAGFlowReference
        
        Args:
            uri: RAGFlow URI 字符串
            
        Returns:
            解析成功返回 RAGFlowReference 实例，失败返回 None
            
        Example:
            >>> ref = RAGFlowReference.from_uri("ragflow://kb/abc123/doc/doc456#chunk=chunk789")
            >>> ref.kb_id
            'abc123'
        """
        match = re.match(RAGFLOW_URI_PATTERN, uri)
        if not match:
            return None
        
        kb_id, doc_id, chunk_id = match.groups()
        return cls(
            kb_id=kb_id,
            doc_id=doc_id,
            chunk_id=chunk_id,
        )

    def __repr__(self) -> str:
        """字符串表示"""
        return f"RAGFlowReference(kb={self.kb_id}, doc={self.doc_id}, chunk={self.chunk_id})"


def create_ragflow_uri(kb_id: str, doc_id: str, chunk_id: Optional[str] = None) -> str:
    """
    快捷函数：创建 RAGFlow URI
    
    Args:
        kb_id: 知识库 ID
        doc_id: 文档 ID  
        chunk_id: chunk ID（可选）
        
    Returns:
        标准格式的 URI 字符串
        
    Example:
        >>> create_ragflow_uri("kb001", "doc123", "chunk456")
        'ragflow://kb/kb001/doc/doc123#chunk=chunk456'
    """
    ref = RAGFlowReference(kb_id=kb_id, doc_id=doc_id, chunk_id=chunk_id)
    return ref.to_uri()


def parse_ragflow_uri(uri: str) -> Optional[dict]:
    """
    快捷函数：解析 RAGFlow URI 为字典
    
    Args:
        uri: RAGFlow URI 字符串
        
    Returns:
        解析结果字典或 None
        
    Example:
        >>> parse_ragflow_uri("ragflow://kb/kb001/doc/doc123#chunk=chunk456")
        {'kb_id': 'kb001', 'doc_id': 'doc123', 'chunk_id': 'chunk456'}
    """
    ref = RAGFlowReference.from_uri(uri)
    if ref:
        return {
            "kb_id": ref.kb_id,
            "doc_id": ref.doc_id,
            "chunk_id": ref.chunk_id,
        }
    return None


def is_ragflow_uri(uri: str) -> bool:
    """
    判断是否为有效的 RAGFlow URI
    
    Args:
        uri: 待检查的 URI 字符串
        
    Returns:
        是否为有效 RAGFlow URI
        
    Example:
        >>> is_ragflow_uri("ragflow://kb/kb001/doc/doc123")
        True
        >>> is_ragflow_uri("https://example.com")
        False
    """
    return uri.startswith(RAGFLOW_URI_SCHEME) and bool(re.match(RAGFLOW_URI_PATTERN, uri))

