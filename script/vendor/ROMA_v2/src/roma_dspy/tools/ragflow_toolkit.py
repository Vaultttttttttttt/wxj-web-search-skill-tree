"""
RAGFlow Toolkit - 内部知识库检索工具

Module 1 - Step 1.1: 实现 RAGFlowToolkit
Module 1 - Step 1.3: 注册到 ROMA Toolkit 系统

提供与 RAGFlow API 的集成，用于检索内部知识库。
"""

import json
import logging
import os
from typing import List, Dict, Optional, Any

from roma_dspy.tools.base.base import BaseToolkit

# 导入 RAGFlow SDK
try:
    from ragflow_sdk import RAGFlow
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    RAGFlow = None

# 默认超时配置（如果 toolkit_config 中未指定）
DEFAULT_RAGFLOW_TIMEOUT_SECONDS = 3.0


logger = logging.getLogger(__name__)


class RAGFlowAPIError(Exception):
    """RAGFlow API 调用错误"""
    pass


class RAGFlowTimeoutError(RAGFlowAPIError):
    """RAGFlow API 超时错误"""
    pass


class RAGFlowToolkit(BaseToolkit):
    """
    RAGFlow 知识库检索工具
    
    用于调用 RAGFlow API 进行内部知识库检索，并返回结构化的 chunk 列表。
    
    配置参数（通过 toolkit_config 传入）:
        api_url: RAGFlow API 基础 URL（例如：http://localhost:9380/api）
        api_key: RAGFlow API 密钥（也可通过环境变量 RAGFLOW_API_KEY 设置）
        kb_id: 知识库 ID（也可通过环境变量 RAGFLOW_KB_ID 设置）
        timeout: 请求超时时间（秒，默认 3.0）
    
    Example:
        # 在 YAML 配置中使用：
        # toolkits:
        #   - class_name: "RAGFlowToolkit"
        #     enabled: true
        #     toolkit_config:
        #       api_url: "http://localhost:9380/api"
        #       api_key: "your-api-key"
        #       kb_id: "your-kb-id"
    """
    
    def _setup_dependencies(self) -> None:
        """Setup RAGFlow toolkit dependencies."""
        if not SDK_AVAILABLE:
            raise ImportError(
                "ragflow-sdk is not installed. Please run 'pip install ragflow-sdk' to use this toolkit."
            )

        # 从 config 或环境变量获取 API 配置
        self.api_url = self.config.get("api_url") or os.getenv("RAGFLOW_API_URL")
        self.api_key = self.config.get("api_key") or os.getenv("RAGFLOW_API_KEY")
        self.kb_id = self.config.get("kb_id") or os.getenv("RAGFLOW_KB_ID")
        
        if not self.api_url:
            raise ValueError(
                "RAGFlow API URL is required. Set it as environment variable RAGFLOW_API_URL "
                "or pass 'api_url' in toolkit_config."
            )
        if not self.api_key:
            raise ValueError(
                "RAGFlow API key is required. Set it as environment variable RAGFLOW_API_KEY "
                "or pass 'api_key' in toolkit_config."
            )
        if not self.kb_id:
            raise ValueError(
                "RAGFlow KB ID is required. Set it as environment variable RAGFLOW_KB_ID "
                "or pass 'kb_id' in toolkit_config."
            )
        
        # SDK 的 base_url 通常不带 /api 后缀
        base_url = self.api_url.rstrip('/')
        if base_url.endswith('/api'):
            base_url = base_url[:-4]
        elif base_url.endswith('/api/v1'):
            base_url = base_url[:-7]
            
        self.client = RAGFlow(api_key=self.api_key, base_url=base_url)
    
    def _initialize_tools(self) -> None:
        """Initialize RAGFlow toolkit configuration."""
        # 超时时间配置：优先使用 toolkit_config 中的配置，否则使用默认值
        self.timeout = float(self.config.get("timeout", DEFAULT_RAGFLOW_TIMEOUT_SECONDS))
        
        # 注意：SDK 目前可能不直接支持在 retrieve 调用中设置单次请求超时
        # 这里的 self.timeout 主要用于记录
        self.log_info(
            f"RAGFlowToolkit (SDK) 初始化: base_url={self.api_url}, "
            f"kb_id={self.kb_id}, timeout={self.timeout}s"
        )
    
    def retrieve(
        self,
        query: str,
        top_n: int = 10,
        top_k: int = 1024,
    ) -> List[Dict]:
        """
        使用 RAGFlow SDK 检索相关文档
        
        Args:
            query: 查询问题
            top_n: 返回的文档数量上限（默认 10）
            top_k: 候选文档数量（用于统计信号，默认 1024）
        
        Returns:
            格式化后的 chunks 列表
        """
        if not query or not query.strip():
            self.log_warning("检索查询为空，返回空列表")
            return []
        
        self.log_debug(
            f"RAGFlow SDK 检索开始: query='{query[:50]}...', "
            f"top_n={top_n}, top_k={top_k}"
        )
        
        try:
            import time
            start_time = time.time()
            
            # 使用 SDK 的 retrieve 方法
            # 参数名参考 python_api_reference.md 第 976 行
            chunks_objects = self.client.retrieve(
                question=query,
                dataset_ids=[self.kb_id],
                page_size=top_n,
                top_k=top_k,
                similarity_threshold=0.2,
                vector_similarity_weight=0.3
            )
            
            elapsed = (time.time() - start_time) * 1000
            
            # 提取并格式化 chunks
            formatted_chunks = self._parse_sdk_response(chunks_objects)
            
            self.log_debug(
                f"RAGFlow SDK 检索成功: 返回 {len(formatted_chunks)} 条结果, "
                f"耗时 {elapsed:.0f}ms"
            )
            
            return formatted_chunks
        
        except Exception as e:
            error_msg = f"RAGFlow SDK 调用失败: {str(e)}"
            self.log_error(error_msg)
            # 为了保持兼容性，如果是超时相关的错误，可以抛出 RAGFlowTimeoutError
            if "timeout" in str(e).lower():
                raise RAGFlowTimeoutError(error_msg) from e
            raise RAGFlowAPIError(error_msg) from e
    
    def _parse_sdk_response(self, chunk_objects: List[Any]) -> List[Dict]:
        """
        解析 SDK 返回的 Chunk 对象列表并转换为字典格式
        """
        chunks = []
        if not chunk_objects:
            return []
            
        for chunk in chunk_objects:
            try:
                # SDK 返回的是对象，使用 getattr 获取属性或直接访问（取决于 SDK 实现）
                # 兼容两种方式，并映射到内部使用的字典键名
                
                def get_val(obj, attr, default=None):
                    if hasattr(obj, attr):
                        return getattr(obj, attr)
                    if isinstance(obj, dict):
                        return obj.get(attr, default)
                    return default

                formatted_chunk = {
                    "chunk_id": get_val(chunk, "id", ""),
                    "doc_id": get_val(chunk, "document_id", ""),
                    "kb_id": get_val(chunk, "dataset_id", self.kb_id),
                    "text": get_val(chunk, "content", ""),
                    "score": float(get_val(chunk, "similarity", 0.0)),
                    
                    "title": get_val(chunk, "document_name", ""),
                    "keywords": get_val(chunk, "important_keywords", []),
                    
                    "vector_similarity": get_val(chunk, "vector_similarity"),
                    "term_similarity": get_val(chunk, "term_similarity"),
                    "image_id": get_val(chunk, "image_id") or get_val(chunk, "img_id"),
                    "position": get_val(chunk, "position", []),
                }
                
                if formatted_chunk["text"]:
                    chunks.append(formatted_chunk)
            except Exception as e:
                self.log_warning(f"解析 SDK chunk 对象失败: {e}")
                continue
                
        return chunks
    
    def health_check(self) -> bool:
        """
        健康检查：验证 RAGFlow API 是否可访问
        
        Returns:
            True 如果 API 可访问，False 否则
        
        Example:
            >>> if toolkit.health_check():
            ...     print("RAGFlow API 正常")
        """
        try:
            # 尝试一个简单的查询
            self.retrieve("test", top_n=1)
            return True
        except Exception as e:
            self.log_error(f"RAGFlow 健康检查失败: {e}")
            return False
    
    def format_ragflow_contexts(self, chunks: List[Dict]) -> List[Dict]:
        """
        Module 1 - Step 1.2: 把 RAGFlow chunks 转成统一 contexts 格式
        
        将 RAGFlow 返回的 chunks 转换为符合 Module 0 定义的 RetrieveResult.contexts 格式。
        
        Args:
            chunks: RAGFlow retrieve() 方法返回的 chunks 列表
        
        Returns:
            标准化的 contexts 列表，每个 context 包含：
            - text: 证据正文
            - source: "ragflow"（固定值）
            - url: 可追溯引用 URI（ragflow://kb/<kb_id>/doc/<doc_id>#chunk=<chunk_id>）
            - title: 文档标题
            - score: 检索分数（0-1）
            - metadata: 额外的元数据（可选）
        
        Example:
            >>> chunks = toolkit.retrieve("AI教育", top_n=5)
            >>> contexts = toolkit.format_ragflow_contexts(chunks)
            >>> print(contexts[0]["url"])
            ragflow://kb/abc123/doc/def456#chunk=xyz789
        """
        contexts = []
        
        for chunk in chunks:
            # 构造内部引用 URI
            # 格式: ragflow://kb/<kb_id>/doc/<doc_id>#chunk=<chunk_id>
            url = (
                f"ragflow://kb/{chunk['kb_id']}"
                f"/doc/{chunk['doc_id']}"
                f"#chunk={chunk['chunk_id']}"
            )
            
            # 构造标准化的 context 对象
            context = {
                # 必需字段
                "text": chunk["text"],
                "source": "ragflow",
                "url": url,
                "title": chunk.get("title", ""),
                "score": chunk.get("score", 0.0),
            }
            
            # 可选的元数据（保留额外信息）
            metadata = {}
            
            # 关键词
            if chunk.get("keywords"):
                metadata["keywords"] = chunk["keywords"]
            
            # 向量相似度和词项相似度
            if chunk.get("vector_similarity") is not None:
                metadata["vector_similarity"] = chunk["vector_similarity"]
            if chunk.get("term_similarity") is not None:
                metadata["term_similarity"] = chunk["term_similarity"]
            
            # 图片 ID（用于 PDF/PPT 快照）
            if chunk.get("image_id"):
                metadata["image_id"] = chunk["image_id"]
            
            # 位置信息
            if chunk.get("position"):
                metadata["position"] = chunk["position"]
            
            # 只有当有元数据时才添加 metadata 字段
            if metadata:
                context["metadata"] = metadata
            
            contexts.append(context)
        
        self.log_debug(
            f"格式化 {len(chunks)} 个 chunks -> {len(contexts)} 个 contexts"
        )
        
        return contexts
    
    def search_knowledge_base(
        self,
        query: str,
        top_n: int = 10,
    ) -> str:
        """
        Search the RAGFlow knowledge base and return formatted contexts as JSON.
        
        This is the main tool method for use in ROMA agents. It performs a complete
        search and formatting workflow:
        1. Retrieves chunks from RAGFlow API
        2. Formats them into standardized contexts
        3. Returns JSON string for agent consumption
        
        Args:
            query: Search query string
            top_n: Maximum number of results to return (default: 10)
        
        Returns:
            JSON string containing formatted contexts with the following structure:
            {
                "query": str,
                "contexts": [
                    {
                        "text": str,
                        "source": "ragflow",
                        "url": str,  # ragflow://kb/.../doc/...#chunk=...
                        "title": str,
                        "score": float,
                        "metadata": {...}  # optional
                    },
                    ...
                ],
                "count": int
            }
        
        Example:
            >>> result = toolkit.search_knowledge_base("AI education", top_n=5)
            >>> import json
            >>> data = json.loads(result)
            >>> print(f"Found {data['count']} contexts")
        """
        try:
            # 1. 检索 chunks
            chunks = self.retrieve(query, top_n=top_n)
            
            # 2. 格式化为 contexts
            contexts = self.format_ragflow_contexts(chunks)
            
            # 3. 构造返回结果
            result = {
                "query": query,
                "contexts": contexts,
                "count": len(contexts),
            }
            
            # 4. 返回 JSON 字符串
            return json.dumps(result, ensure_ascii=False, indent=2)
            
        except Exception as e:
            self.log_error(f"RAGFlow search failed: {e}")
            # 返回错误信息（也是 JSON 格式）
            error_result = {
                "query": query,
                "error": str(e),
                "contexts": [],
                "count": 0,
            }
            return json.dumps(error_result, ensure_ascii=False)
    
    def __repr__(self) -> str:
        """字符串表示"""
        return (
            f"RAGFlowToolkit(api_url='{self.api_url}', "
            f"kb_id='{self.kb_id}', timeout={self.timeout}s)"
        )


# 便捷函数：创建默认 RAGFlow Toolkit 实例
def create_ragflow_toolkit(
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
    kb_id: Optional[str] = None,
) -> RAGFlowToolkit:
    """
    便捷函数：从环境变量创建 RAGFlow Toolkit
    
    Args:
        api_url: API URL（如果为 None，从环境变量 RAGFLOW_API_URL 读取）
        api_key: API Key（如果为 None，从环境变量 RAGFLOW_API_KEY 读取）
        kb_id: 知识库 ID（如果为 None，从环境变量 RAGFLOW_KB_ID 读取）
    
    Returns:
        RAGFlowToolkit 实例
    
    Raises:
        ValueError: 如果必需的参数未提供且环境变量未设置
    
    Example:
        >>> # 从环境变量创建
        >>> toolkit = create_ragflow_toolkit()
        >>> # 或显式提供参数
        >>> toolkit = create_ragflow_toolkit(
        ...     api_url="http://localhost:9380/api",
        ...     api_key="your-key",
        ...     kb_id="your-kb-id"
        ... )
    """
    import os
    
    # 从环境变量读取
    api_url = api_url or os.getenv("RAGFLOW_API_URL")
    api_key = api_key or os.getenv("RAGFLOW_API_KEY")
    kb_id = kb_id or os.getenv("RAGFLOW_KB_ID")
    
    # 验证必需参数
    if not api_url:
        raise ValueError("api_url 未提供，请设置环境变量 RAGFLOW_API_URL")
    if not api_key:
        raise ValueError("api_key 未提供，请设置环境变量 RAGFLOW_API_KEY")
    if not kb_id:
        raise ValueError("kb_id 未提供，请设置环境变量 RAGFLOW_KB_ID")
    
    return RAGFlowToolkit(
        api_url=api_url,
        api_key=api_key,
        kb_id=kb_id,
    )

