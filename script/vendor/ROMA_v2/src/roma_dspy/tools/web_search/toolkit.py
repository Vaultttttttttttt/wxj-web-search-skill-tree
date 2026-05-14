"""Web search toolkit using DSPy Predict with web search enabled models."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import dspy
from loguru import logger

from roma_dspy.tools.base.base import BaseToolkit

if TYPE_CHECKING:
    from roma_dspy.core.storage import FileStorage


class WebSearchProvider(str, Enum):
    """Web search provider backends."""

    OPENROUTER = "openrouter"
    OPENAI = "openai"


class WebSearchSignature(dspy.Signature):
    """You are an expert data searcher with 20+ years of experience in searching and retrieving information from reliable sources.

    Your task is to RETRIEVE and FETCH all necessary data to answer the query. Focus on comprehensive data retrieval, not reasoning or analysis.

    Guidelines:
    1. COMPREHENSIVE DATA RETRIEVAL:
       - If it's a table, retrieve the ENTIRE table (even if it has 50, 100, or more rows)
       - If it's a list, include ALL items in the list
       - If it's statistics or rankings, include ALL available data points
       - For articles/paragraphs, include ALL relevant sections and mentions
       - Present data in its complete form - do not truncate or summarize

    2. SOURCE RELIABILITY PRIORITY:
       - Wikipedia is the MOST PREFERRED source when available
       - Other reputable sources in order of preference:
         • Official government databases and statistics
         • Academic institutions and research papers
         • Established news organizations (BBC, Reuters, AP, etc.)
         • Industry-standard databases and professional organizations
       - Always cite your sources

    3. DATA PRESENTATION:
       - Present data EXACTLY as found in the source
       - Maintain original formatting (tables, lists, etc.)
       - Include all columns, rows, and data points
       - Do NOT analyze, interpret, or reason about the data
       - Do NOT summarize or condense - present everything

    4. TEMPORAL AWARENESS:
       - Prioritize recent information when relevant
       - When data has timestamps or dates, include them
       - For time-sensitive queries, focus on the most current available data
    """

    query: str = dspy.InputField(
        desc="The search query or question to answer. Use this to search for comprehensive data from reliable sources."
    )
    answer: str = dspy.OutputField(
        desc="Complete and comprehensive data retrieved from web search results. Include ALL relevant facts, details, tables, lists, and data points. Present data EXACTLY as found in sources without summarizing or analyzing. Maintain original formatting."
    )
    citations: list[str] = dspy.OutputField(
        desc="List of source URLs used to generate the answer. Prioritize Wikipedia and other reliable sources (government databases, academic institutions, established news organizations)."
    )


# near other signature definitions
class PlainWebSearchSignature(dspy.Signature):
    """You are an expert data searcher with 20+ years of experience in searching and retrieving information from reliable sources.

    Your task is to RETRIEVE and FETCH all necessary data to answer the query. Focus on comprehensive data retrieval, not reasoning or analysis.

    Guidelines:
    1. COMPREHENSIVE DATA RETRIEVAL:
       - If it's a table, retrieve the ENTIRE table (even if it has 50, 100, or more rows)
       - If it's a list, include ALL items in the list
       - If it's statistics or rankings, include ALL available data points
       - For articles/paragraphs, include ALL relevant sections and mentions
       - Present data in its complete form - do not truncate or summarize

    2. SOURCE RELIABILITY PRIORITY:
       - Wikipedia is the MOST PREFERRED source when available
       - Other reputable sources in order of preference:
         • Official government databases and statistics
         • Academic institutions and research papers
         • Established news organizations (BBC, Reuters, AP, etc.)
         • Industry-standard databases and professional organizations
       - Always cite your sources

    3. DATA PRESENTATION:
       - Present data EXACTLY as found in the source
       - Maintain original formatting (tables, lists, etc.)
       - Include all columns, rows, and data points
       - Do NOT analyze, interpret, or reason about the data
       - Do NOT summarize or condense - present everything

    4. TEMPORAL AWARENESS:
       - Prioritize recent information when relevant
       - When data has timestamps or dates, include them
       - For time-sensitive queries, focus on the most current available data
    """

    query: str = dspy.InputField(desc="Search query.")
    retrieved_data: str = dspy.OutputField(
        desc=(
            "Verbatim data retrieved from sources. Include full tables, lists, statistics, and excerpts "
            "exactly as found without summarizing or interpreting. Prefer structured formats when possible."
        )
    )


class WebSearchToolkit(BaseToolkit):
    """Web search toolkit using DSPy with web-search-enabled language models.

    Provides web search capabilities by configuring a DSPy language model with
    web search features (OpenRouter plugins or OpenAI web_search_preview).

    The toolkit uses `dspy.Predict` with a web search signature, allowing the
    language model to search the web and incorporate real-time information into
    its responses. Citations are automatically extracted from the LM response.

    Configuration:
        model: Model to use for web search (e.g., "openai/gpt-4o", "anthropic/claude-sonnet-4")
        provider: WebSearchProvider.OPENROUTER or WebSearchProvider.OPENAI (default: OPENROUTER)
        search_engine: Search engine for OpenRouter ("exa" recommended)
        search_context_size: "low", "medium", or "high" (default: "medium")
        max_results: Maximum search results to include (default: 5)
        temperature: Model temperature (default: 0.0 for deterministic results)
        max_tokens: Maximum tokens in response (default: 4000)

    Example:
        ```yaml
        toolkits:
          - class_name: WebSearchToolkit
            toolkit_config:
              model: openrouter/anthropic/claude-sonnet-4
              provider: openrouter  # or "openai"
              search_engine: exa
              search_context_size: medium
              max_results: 5
        ```

    Usage:
        ```python
        result = await toolkit.web_search(
            query="What is the current price of Bitcoin?"
        )
        # Returns: {
        #   success: True,
        #   data: "Bitcoin is currently...",
        #   citations: [{url: "...", title: "..."}],
        #   ...
        # }
        ```
    """

    def __init__(
        self,
        model: str,
        search_engine: str = "exa",
        search_context_size: str = "medium",
        max_results: int = 10,
        temperature: float = 0.5,
        max_tokens: int = 32000,
        enabled: bool = True,
        include_tools: Optional[List[str]] = None,
        exclude_tools: Optional[List[str]] = None,
        file_storage: Optional["FileStorage"] = None,
        max_query_length: int = 150,
        max_retries: int = 3,
        **config,
    ):
        """Initialize web search toolkit.

        Args:
            model: Language model to use (must support web search)
                   - OpenRouter models: "openrouter/..." (uses plugins)
                   - OpenAI models: "openai/..." (uses Responses API)
            search_engine: Search engine for OpenRouter ("exa" recommended, omit for native)
            search_context_size: Context depth - "low", "medium", or "high"
            max_results: Maximum number of search results to include
            temperature: Model temperature for response generation
            max_tokens: Maximum tokens in model response
            enabled: Whether toolkit is enabled
            include_tools: Specific tools to include (None = all)
            exclude_tools: Tools to exclude
            file_storage: Optional file storage for large responses
            max_query_length: Maximum query string length (default: 150)
            max_retries: Maximum retry attempts for failed searches (default: 3)
            **config: Additional configuration
        """
        self.model = model
        self._chat_adapter = dspy.ChatAdapter(use_native_function_calling=True)

        # Auto-detect provider from model identifier
        if model.startswith("openrouter/"):
            self.provider = WebSearchProvider.OPENROUTER
        elif model.startswith("openai/"):
            self.provider = WebSearchProvider.OPENAI
        else:
            raise ValueError(
                f"Invalid model identifier: {model}. "
                "Must start with 'openrouter/' or 'openai/'"
            )

        self.search_engine = search_engine
        self.search_context_size = search_context_size
        self.max_results = max_results
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_query_length = max_query_length
        self.max_retries = max_retries
        
        # Web search result cache: query_hash -> (result, timestamp)
        # Cache TTL: 1 hour (web search results may become stale)
        self._search_cache: Dict[str, Tuple[dict, datetime]] = {}
        self._cache_ttl_hours: float = config.get("cache_ttl_hours", 1.0)
        self._cache_enabled: bool = config.get("enable_cache", True)
        self._max_cache_size: int = config.get("max_cache_size", 100)

        # Validate search context size
        if search_context_size not in ("low", "medium", "high"):
            raise ValueError(
                f"Invalid search_context_size: {search_context_size}. "
                "Must be 'low', 'medium', or 'high'"
            )

        super().__init__(
            enabled=enabled,
            include_tools=include_tools,
            exclude_tools=exclude_tools,
            file_storage=file_storage,
            **config,
        )

        logger.info(
            f"Initialized WebSearchToolkit: model={model}, provider={self.provider.value}, "
            f"engine={search_engine}, max_results={max_results}"
        )

    def _setup_dependencies(self) -> None:
        """Setup external dependencies - DSPy is always available."""
        pass

    def _initialize_tools(self) -> None:
        """Initialize web search predictor with configured LM."""
        # Build LM configuration based on provider
        if self.provider == WebSearchProvider.OPENROUTER:
            # OpenRouter uses plugins parameter
            web_config = {
                "id": "web",
                "engine": self.search_engine,
                "max_results": self.max_results,
            }

            # Add search context size if not default
            if self.search_context_size != "medium":
                web_config["search_context_size"] = self.search_context_size

            # Create LM with plugins in extra_body
            self.lm = dspy.LM(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                extra_body={"plugins": [web_config]},
            )

        elif self.provider == WebSearchProvider.OPENAI:
            # OpenAI uses web_search tool in Responses API
            # Format: tools=[{"type": "web_search", "search_context_size": "low"}]
            # Reference: https://platform.openai.com/docs/guides/tools-web-search
            tool_config = {"type": "web_search"}

            # Add search context size if not default
            if self.search_context_size != "medium":
                tool_config["search_context_size"] = self.search_context_size

            self.lm = dspy.LM(
                model=self.model,
                model_type="responses",  # OpenAI Responses API
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                tools=[tool_config],
                tool_choice={"type": "web_search"},  # Force use of web search tool
            )
            # Use plain text signature to avoid structured-output/JSON mode
            self.predictor = dspy.Predict(PlainWebSearchSignature)
            self.predictor.lm = self.lm
            return

        # Create web search predictor
        self.predictor = dspy.Predict(WebSearchSignature)
        self.predictor.lm = self.lm

        logger.debug(
            f"Initialized web search predictor with {self.provider.value} provider"
        )

    def _generate_cache_key(
        self, query: str, max_results: Optional[int], search_context_size: Optional[str]
    ) -> str:
        """Generate cache key for a search query.
        
        Args:
            query: Search query string
            max_results: Optional max_results override
            search_context_size: Optional search_context_size override
            
        Returns:
            SHA256 hash (32 chars) of the normalized query parameters
        """
        # Normalize query parameters for cache key
        effective_max_results = max_results or self.max_results
        effective_context_size = search_context_size or self.search_context_size
        
        # Create normalized query string (lowercase, trimmed)
        normalized_query = query.strip().lower()
        
        # Create cache key components
        key_parts = [
            normalized_query,
            str(effective_max_results),
            effective_context_size,
            self.model,  # Include model to avoid cross-model cache hits
        ]
        key_string = "|".join(key_parts)
        
        # Generate SHA256 hash (32 chars)
        return hashlib.sha256(key_string.encode("utf-8")).hexdigest()[:32]
    
    def _get_cached_result(self, cache_key: str) -> Optional[dict]:
        """Get cached search result if available and not expired.
        
        Args:
            cache_key: Cache key for the query
            
        Returns:
            Cached result dict if found and not expired, None otherwise
        """
        if not self._cache_enabled:
            return None
            
        if cache_key not in self._search_cache:
            return None
            
        result, timestamp = self._search_cache[cache_key]
        
        # Check if cache entry has expired
        age = datetime.now(timezone.utc) - timestamp
        if age > timedelta(hours=self._cache_ttl_hours):
            # Expired, remove from cache
            del self._search_cache[cache_key]
            logger.debug(f"Cache entry expired for key: {cache_key[:16]}...")
            return None
            
        logger.debug(f"Cache hit for query: {cache_key[:16]}...")
        return result.copy()  # Return a copy to avoid mutations
    
    def _store_cached_result(self, cache_key: str, result: dict) -> None:
        """Store search result in cache.
        
        Args:
            cache_key: Cache key for the query
            result: Search result dict to cache
        """
        if not self._cache_enabled:
            return
            
        # Clean up expired entries if cache is getting large
        if len(self._search_cache) >= self._max_cache_size:
            self._cleanup_cache()
            
        # Store result with current timestamp
        self._search_cache[cache_key] = (result.copy(), datetime.now(timezone.utc))
        logger.debug(f"Cached search result: {cache_key[:16]}...")
    
    def _cleanup_cache(self) -> None:
        """Remove expired entries from cache."""
        now = datetime.now(timezone.utc)
        expired_keys = [
            key
            for key, (_, timestamp) in self._search_cache.items()
            if (now - timestamp) > timedelta(hours=self._cache_ttl_hours)
        ]
        
        for key in expired_keys:
            del self._search_cache[key]
            
        # If still over limit, remove oldest entries
        if len(self._search_cache) >= self._max_cache_size:
            sorted_entries = sorted(
                self._search_cache.items(),
                key=lambda x: x[1][1]  # Sort by timestamp
            )
            # Remove oldest 20% of entries
            remove_count = max(1, len(sorted_entries) // 5)
            for key, _ in sorted_entries[:remove_count]:
                del self._search_cache[key]
                
        logger.debug(
            f"Cache cleanup: removed {len(expired_keys)} expired entries, "
            f"cache size: {len(self._search_cache)}"
        )

    async def web_search(
        self,
        query: str,
        max_results: Optional[int] = None,
        search_context_size: Optional[str] = None,
    ) -> dict:
        """Search the web and return a comprehensive answer with citations.

        Uses the configured language model with web search enabled to answer
        the query based on current information from the web. Automatically
        extracts citations from the response.

        Args:
            query: The search query or question to answer
            max_results: Override default max_results for this search
            search_context_size: Override default search_context_size ("low", "medium", "high")

        Returns:
            dict: Tool response with format:
                {
                    "success": True,
                    "data": "answer text",
                    "citations": [{"url": "...", "title": "..."}],  # If available
                    "tool_name": "web_search",
                    "query": "original query",
                    "model": "model used",
                    "provider": "openrouter" or "openai"
                }

        Example:
            ```python
            result = await toolkit.web_search(
                query="What are the latest developments in quantum computing?",
                max_results=10
            )

            if result["success"]:
                print(result["data"])  # Answer
                print(result.get("citations", []))  # Source URLs
            ```
        """
        # Generate cache key and check cache first
        cache_key = self._generate_cache_key(query, max_results, search_context_size)
        cached_result = self._get_cached_result(cache_key)
        if cached_result is not None:
            logger.info(f"Returning cached web search result for query: '{query[:100]}...'")
            # Update query field to match original (cache may have normalized it)
            cached_result["query"] = query
            cached_result["cached"] = True
            return cached_result
        
        # Limit query length to prevent token issues
        original_query = query
        if len(query) > self.max_query_length:
            logger.warning(
                f"Query too long ({len(query)} chars), truncating to {self.max_query_length} chars"
            )
            # Try to truncate at word boundary
            query = query[:self.max_query_length].rsplit(' ', 1)[0]
            logger.debug(f"Truncated query: '{query[:100]}...'")

        # Retry logic for handling max_tokens and other transient errors
        last_error = None
        current_max_results = max_results or self.max_results
        current_query = query

        for attempt in range(self.max_retries):
            try:
                logger.info(
                    f"Executing web search (attempt {attempt + 1}/{self.max_retries}): "
                    f"query='{current_query[:100]}...', "
                    f"max_results={current_max_results}"
                )

                # Build predictor kwargs
                kwargs = {}

                # If parameters override defaults, create new LM with updated config
                if current_max_results != self.max_results or search_context_size is not None:
                    lm = self._create_lm_with_overrides(current_max_results, search_context_size)
                    kwargs["lm"] = lm

                # For OpenAI provider, use direct LM calling to properly parse citations
                if self.provider == WebSearchProvider.OPENAI:
                    # Use the override LM if provided, otherwise use default
                    active_lm = kwargs.get("lm", self.lm)

                    # Call LM directly with messages
                    messages = [{"role": "user", "content": current_query}]
                    with dspy.context(lm=active_lm, adapter=self._chat_adapter):
                        await active_lm.acall(messages=messages)

                    # Parse raw response from history
                    if active_lm.history:
                        raw_response = active_lm.history[-1]["response"]
                        parsed_data = self._parse_openai_responses_output(raw_response)

                        # Create manual Prediction object
                        prediction = dspy.Prediction(
                            query=current_query,
                            retrieved_data=parsed_data["text"],
                            citations=[
                                c["url"] for c in parsed_data.get("citations", [])
                            ],  # Match signature format
                        )

                        # Use parsed citations (includes title)
                        citations = parsed_data.get("citations", [])
                        answer = parsed_data["text"]
                    else:
                        logger.warning("No history found in LM response")
                        answer = ""
                        citations = []

                else:
                    # OpenRouter: Use existing predictor approach
                    with dspy.context(lm=self.lm, adapter=self._chat_adapter):
                        prediction = await self.predictor.acall(query=current_query, **kwargs)

                    # Extract answer
                    answer = getattr(
                        prediction,
                        "retrieved_data",
                        getattr(prediction, "answer", ""),
                    )

                    # Extract citations from signature output (DSPy extracts as list[str])
                    citations_urls = getattr(prediction, "citations", [])

                    # Convert to citation dicts format
                    citations = (
                        [{"url": url} for url in citations_urls] if citations_urls else []
                    )

                logger.success(
                    f"Web search completed: {len(answer)} chars, {len(citations)} citations"
                )

                # Build response with citations
                response = await self._build_success_response(
                    data=answer,
                    tool_name="web_search",
                    query=original_query,
                    model=self.model,
                    provider=self.provider.value,
                )

                # Add citations if available
                if citations:
                    logger.debug(
                        f"Adding {len(citations)} citations to response: {citations}"
                    )
                    response["citations"] = citations
                else:
                    logger.warning("No citations to add to response")

                logger.debug(f"Final response keys: {list(response.keys())}")
                
                # Store result in cache (only if successful)
                if response.get("success"):
                    self._store_cached_result(cache_key, response)
                
                return response

            except Exception as e:
                error_str = str(e).lower()
                last_error = e

                # Check if it's a max_tokens error that we can retry
                # More comprehensive error detection
                is_max_tokens_error = (
                    "max_tokens" in error_str 
                    or "token" in error_str 
                    or "token limit" in error_str
                    or "token count" in error_str
                    or "exceeded" in error_str and "token" in error_str
                    or "maximum context length" in error_str
                )
                is_retryable = is_max_tokens_error and attempt < self.max_retries - 1

                if is_retryable:
                    # Strategy 1: Reduce max_results (most effective)
                    if current_max_results > 1:
                        current_max_results = max(1, current_max_results - 1)
                        logger.warning(
                            f"max_tokens error detected, reducing max_results to {current_max_results} "
                            f"for retry (attempt {attempt + 2}/{self.max_retries})"
                        )
                    # Strategy 2: Use lower context size if possible
                    elif search_context_size is None and self.search_context_size == "high":
                        search_context_size = "medium"
                        logger.warning(
                            f"max_tokens error detected, reducing search_context_size to 'medium' "
                            f"for retry (attempt {attempt + 2}/{self.max_retries})"
                        )
                    elif (search_context_size is None and self.search_context_size == "medium") or \
                         (search_context_size == "high"):
                        search_context_size = "low"
                        logger.warning(
                            f"max_tokens error detected, reducing search_context_size to 'low' "
                            f"for retry (attempt {attempt + 2}/{self.max_retries})"
                        )
                    # Strategy 3: Shorten query if we can't reduce results/context further
                    elif len(current_query) > 50:
                        # Shorten query more aggressively (50% instead of 70%)
                        current_query = current_query[:int(len(current_query) * 0.5)].rsplit(' ', 1)[0]
                        logger.warning(
                            f"max_tokens error detected, shortening query to {len(current_query)} chars "
                            f"for retry (attempt {attempt + 2}/{self.max_retries})"
                        )
                    else:
                        # Can't retry further
                        logger.error(
                            f"Web search failed for query '{current_query[:100]}...': {e}. "
                            f"Cannot retry further (query too short or max_results at minimum)"
                        )
                        break

                    # Exponential backoff before retry
                    if attempt < self.max_retries - 1:
                        delay = 1.0 * (2 ** attempt)
                        logger.debug(f"Retrying after {delay:.1f}s delay")
                        await asyncio.sleep(delay)
                    continue
                else:
                    # Non-retryable error or last attempt
                    logger.error(
                        f"Web search failed for query '{current_query[:100]}...': {e} "
                        f"(attempt {attempt + 1}/{self.max_retries})"
                    )
                    if attempt == self.max_retries - 1:
                        # Last attempt failed
                        break

        # All retries exhausted or non-retryable error
        return self._build_error_response(
            last_error or Exception("Web search failed after retries"),
            tool_name="web_search",
            query=original_query
        )

    def _extract_citations(self, prediction: dspy.Prediction) -> List[Dict[str, str]]:
        """Extract citations from DSPy Prediction object.

        DSPy automatically extracts citations from LiteLLM responses and stores
        them in the completions metadata.

        Args:
            prediction: DSPy Prediction object

        Returns:
            List of citation dicts with 'url' and optionally 'title'
        """
        citations = []

        try:
            # Access completions from prediction
            if hasattr(prediction, "completions") and prediction.completions:
                for completion in prediction.completions:
                    # Check if completion has citations
                    if isinstance(completion, dict) and "citations" in completion:
                        citations.extend(completion["citations"])

            # Deduplicate by URL
            seen_urls = set()
            unique_citations = []
            for citation in citations:
                url = citation.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_citations.append(citation)

            logger.debug(f"Extracted {len(unique_citations)} unique citations")
            return unique_citations

        except Exception as e:
            logger.warning(f"Failed to extract citations: {e}")
            return []

    def _create_lm_with_overrides(
        self,
        max_results: Optional[int] = None,
        search_context_size: Optional[str] = None,
    ) -> dspy.LM:
        """Create LM with parameter overrides.

        Args:
            max_results: Override max_results
            search_context_size: Override search_context_size

        Returns:
            New LM instance with updated configuration
        """
        effective_max_results = max_results or self.max_results
        effective_context_size = search_context_size or self.search_context_size

        if self.provider == WebSearchProvider.OPENROUTER:
            web_config = {
                "id": "web",
                "engine": self.search_engine,
                "max_results": effective_max_results,
            }

            if effective_context_size != "medium":
                web_config["search_context_size"] = effective_context_size

            return dspy.LM(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                extra_body={"plugins": [web_config]},
            )

        elif self.provider == WebSearchProvider.OPENAI:
            tool_config = {"type": "web_search"}

            if effective_context_size != "medium":
                tool_config["search_context_size"] = effective_context_size

            return dspy.LM(
                model=self.model,
                model_type="responses",
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                tools=[tool_config],
                tool_choice={"type": "web_search"},  # Force use of web search tool
            )

    def _parse_openai_responses_output(self, response) -> Dict[str, any]:
        """Parse raw OpenAI Responses API output and extract text + citations.

        The OpenAI Responses API returns a complex nested structure where:
        - output[-1] contains the final message (type='message')
        - output[-1]['content'][0]['text'] contains the actual answer text
        - output[-1]['content'][0]['annotations'] contains citations with url and title

        Args:
            response: Raw response object from lm.history[-1]['response']

        Returns:
            Dict with keys:
                - text (str): The answer text
                - citations (list[dict]): List of citation dicts with 'url' and 'title'
        """
        result = {"text": "", "citations": []}

        try:
            # Access the output array
            if not hasattr(response, "output") or not response.output:
                logger.warning("No output found in response")
                return result

            # Find the last message output (type='message')
            message_output = None
            for output_item in reversed(response.output):
                if hasattr(output_item, "type") and output_item.type == "message":
                    message_output = output_item
                    break

            if not message_output:
                logger.warning("No message output found in response")
                return result

            # Extract text from content
            if hasattr(message_output, "content") and message_output.content:
                for content_item in message_output.content:
                    if hasattr(content_item, "text") and content_item.text:
                        result["text"] += content_item.text

                    # Extract citations from annotations
                    if (
                        hasattr(content_item, "annotations")
                        and content_item.annotations
                    ):
                        logger.debug(
                            f"Found {len(content_item.annotations)} annotations to process"
                        )
                        for annotation in content_item.annotations:
                            logger.debug(
                                f"Processing annotation: type={getattr(annotation, 'type', 'NO_TYPE')}"
                            )
                            if (
                                hasattr(annotation, "type")
                                and annotation.type == "url_citation"
                            ):
                                citation = {}
                                if hasattr(annotation, "url"):
                                    citation["url"] = annotation.url
                                if hasattr(annotation, "title"):
                                    citation["title"] = annotation.title
                                logger.debug(f"Extracted citation: {citation}")
                                if citation:  # Only add if we got at least a URL
                                    result["citations"].append(citation)

            # Deduplicate citations by URL
            seen_urls = set()
            unique_citations = []
            for citation in result["citations"]:
                url = citation.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_citations.append(citation)
            result["citations"] = unique_citations

            logger.debug(
                f"Parsed OpenAI response: {len(result['text'])} chars, "
                f"{len(result['citations'])} citations"
            )

        except Exception as e:
            logger.error(f"Failed to parse OpenAI Responses API output: {e}")

        return result


__all__ = ["WebSearchToolkit", "WebSearchProvider"]
