"""
LLM Retrieval Evaluator for Adaptive Retrieval (Module 6.2)

Provides LLM-as-judge capabilities to evaluate the quality of retrieved contexts.
"""

import dspy
import re
from typing import List, Dict, Any, Tuple, Optional

class RetrievalJudgeSignature(dspy.Signature):
    """
    You are an expert search result evaluator.
    Evaluate whether the retrieved contexts provide sufficient information to answer the user's query.
    
    Consider:
    1. Relevance: Do the contexts directly address the query?
    2. Completeness: Is there enough information to form a comprehensive answer?
    3. Noise: Are there too many irrelevant documents?
    
    Output a confidence score between 0.0 and 1.0:
    - 0.0-0.3: Irrelevant or completely missing information. (Should trigger Web Search)
    - 0.4-0.6: Partially relevant but misses key details. (Should trigger Hybrid Search)
    - 0.7-1.0: Highly relevant and sufficient. (Internal knowledge is enough)
    """
    
    query: str = dspy.InputField(desc="The user's search query")
    contexts: str = dspy.InputField(desc="The retrieved content snippets to evaluate (Top 5)")
    
    reasoning: str = dspy.OutputField(desc="Brief explanation of the evaluation, highlighting missing info")
    confidence: float = dspy.OutputField(desc="A float score between 0.0 and 1.0")


class LLMRetrievalEvaluator:
    """
    Evaluates retrieval quality using an LLM (Module 6.2).
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.lm = None
        
        # Initialize custom LM if configured
        # config format matches 'llm' section in yaml profiles
        llm_config = self.config.get("llm")
        if llm_config:
            self.lm = self._init_lm(llm_config)
            
        # Allows for future configuration (e.g., specific model for evaluation)
        self.predictor = dspy.ChainOfThought(RetrievalJudgeSignature)

    def _init_lm(self, llm_config: Dict[str, Any]):
        """Initialize dspy.LM based on configuration."""
        model = llm_config.get("model", "")
        api_key = llm_config.get("api_key")
        base_url = llm_config.get("base_url")
        
        # dspy 3.0+ uses generic LM class with model string identifier
        return dspy.LM(
            model=model,
            api_key=api_key,
            api_base=base_url,
            temperature=llm_config.get("temperature", 0.1),
            max_tokens=llm_config.get("max_tokens", 1000)
        )
        
    def evaluate(self, query: str, chunks: List[Dict[str, Any]]) -> Tuple[float, str]:
        """
        Evaluate the quality of retrieved chunks for the given query.
        
        Args:
            query: The search query.
            chunks: List of retrieved chunks (dictionaries with 'text' and 'score').
            
        Returns:
            Tuple[float, str]: (confidence_score, reasoning)
        """
        if not chunks:
            return 0.0, "No contexts provided."
            
        # Prepare context string (Limit to top 5 to save tokens and focus on top results)
        context_str = ""
        for i, chunk in enumerate(chunks[:5]): 
            # Truncate text to avoid exceeding context window
            text = chunk.get("text", "")[:400].replace("\n", " ")
            score = chunk.get("score", 0.0)
            title = chunk.get("title", "Untitled")
            context_str += f"[{i+1}] {title} (Score: {score:.2f}): {text}...\n"
            
        try:
            # Use configured LM if available, otherwise fallback to global context
            ctx_manager = dspy.context(lm=self.lm) if self.lm else dspy.context()
            
            with ctx_manager:
                # Call DSPy predictor
                pred = self.predictor(query=query, contexts=context_str)
            
            # Robust parsing of confidence
            conf_raw = pred.confidence
            reasoning = getattr(pred, "reasoning", "No reasoning provided")
            
            return self._parse_confidence(conf_raw), reasoning
            
        except Exception as e:
            # Fail gracefully to heuristic or default
            return 0.5, f"LLM evaluation failed, defaulted to 0.5. Error: {str(e)}"

    def _parse_confidence(self, conf_raw: Any) -> float:
        """Parse confidence score from various LLM output formats."""
        try:
            if isinstance(conf_raw, (float, int)):
                return float(conf_raw)
            
            conf_str = str(conf_raw).strip()
            
            # Try direct float conversion
            try:
                return float(conf_str)
            except ValueError:
                pass
                
            # Extract number from string (e.g. "Confidence: 0.8")
            match = re.search(r"0\.\d+|1\.0|1|0", conf_str)
            if match:
                return float(match.group(0))
                
            return 0.5
        except Exception:
            return 0.5

