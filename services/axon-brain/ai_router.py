"""Context-Aware AI Routing — selects optimal model based on task type and context.

Analyzes incoming requests and routes them to the best model:
  - Speed model for classification, short queries
  - General model for conversation, Q&A
  - Deep model for complex reasoning, code generation
  - Embedding model for vector operations

Integrates with hardware_profiler for resource-aware decisions.
"""

import re
import sys
from pathlib import Path

try:
    from axon_logger import configure_app_logger
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from axon_logger import configure_app_logger
    except ImportError:
        import logging as _logging

        def configure_app_logger(name, level=_logging.INFO, log_file=None):
            _logging.basicConfig(level=level)
            return _logging.getLogger(name)

log = configure_app_logger("ai-router")


# Task classification patterns
_SPEED_PATTERNS = [
    r"^(yes|no|ok|sure|cancel|stop|next|back|close|open)\s*$",
    r"^(switch to|go to|open)\s+\w+",
    r"^(run|execute)\s+",
    r"^(what is|what's)\s+\d+[\+\-\*\/]\d+",
    r"^classif",
    r"^(short|brief|quick|one word)",
]

_GENERAL_PATTERNS = [
    r"(explain|describe|tell me about|what is|how does|why does|when did)",
    r"(summarize|summarise|review|compare|contrast)",
    r"(help|assist|suggest|recommend|advice)",
    r"(write|draft|compose|create)\s+(a |an |the )?(email|letter|message|note|document)",
]

_CODE_PATTERNS = [
    r"(write|create|generate|implement|build)\s+(a |an )?(function|class|script|program|code|module|file)",
    r"(fix|debug|debug|patch|refactor|optimize|rewrite)\s+(this|the|my)?\s*(code|bug|error|function|class)",
    r"(python|javascript|rust|golang|java|c\+\+|bash|shell|html|css|sql|typescript)\s+(code|function|script)",
    r"(how to|how do i|how can i)\s+(implement|write|create|build|make)\s",
    r"(refactor|clean up|restructure|reorganize)\s",
    r"```",  # Code blocks in prompt
]

_EMBEDDING_KEYWORDS = {
    "search", "find", "locate", "index", "embed", "vector", "similar",
    "semantic", "relevant", "matching",
}


class AIRouter:
    """Routes requests to optimal model based on task analysis.

    Usage::

        router = AIRouter(config)
        model = router.select_model(prompt, context, explicit_model=None)
    """

    # Model tiers
    SPEED = "speed"
    GENERAL = "general"
    DEEP = "deep"
    EMBEDDING = "embedding"

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._speed_model = self._config.get("speed_model", "llama3.2:3b")
        self._general_model = self._config.get("general_model", "mistral:7b")
        self._deep_model = self._config.get("deep_model", "qwen2.5:7b")
        self._embed_model = "nomic-embed-text"

    def select_model(
        self,
        prompt: str,
        context: str = "",
        explicit_model: str | None = None,
    ) -> tuple[str, str]:
        """Select the best model for a request.

        Args:
            prompt: The user's input text.
            context: Optional desktop context string.
            explicit_model: If provided, use this model directly.

        Returns:
            Tuple of (model_name, reason).
        """
        if explicit_model:
            return explicit_model, "user-selected"

        task_type = self.classify_task(prompt, context)

        if task_type == self.EMBEDDING:
            return self._embed_model, "embedding-task"
        elif task_type == self.SPEED:
            return self._speed_model, "speed-optimal"
        elif task_type == self.DEEP:
            return self._deep_model, "complex-reasoning"
        else:
            return self._general_model, "general-purpose"

    def classify_task(self, prompt: str, context: str = "") -> str:
        """Classify a prompt into a task type.

        Returns one of: speed, general, deep, embedding.
        """
        text = prompt.lower().strip()

        # Check for embedding/search tasks
        words = set(text.split())
        if words & _EMBEDDING_KEYWORDS and len(text.split()) < 10:
            return self.EMBEDDING

        # Check for code patterns (before speed to avoid false positives)
        code_score = sum(
            1 for p in _CODE_PATTERNS if re.search(p, text, re.IGNORECASE)
        )
        if code_score >= 1 and len(text.split()) > 5:
            return self.DEEP

        # Check for speed patterns (short, simple)
        for pattern in _SPEED_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return self.SPEED

        # Check for general patterns
        general_score = sum(
            1 for p in _GENERAL_PATTERNS if re.search(p, text, re.IGNORECASE)
        )
        if general_score >= 1:
            return self.GENERAL

        # Length-based fallback
        if len(text) < 15:
            return self.SPEED
        if len(text) > 200 or text.count("?") > 2:
            return self.DEEP

        return self.GENERAL

    def get_model_for_classify_window(self) -> str:
        """Model for window classification (always fast)."""
        return self._speed_model

    def get_model_for_intent(self) -> str:
        """Model for intent classification (always fast)."""
        return self._speed_model

    def get_model_for_chat(self, message_length: int = 0) -> str:
        """Model for chat based on expected complexity."""
        if message_length > 500:
            return self._deep_model
        return self._general_model

    def get_model_for_generate(self, prompt: str) -> str:
        """Model for text generation."""
        task_type = self.classify_task(prompt)
        if task_type == self.DEEP:
            return self._deep_model
        elif task_type == self.SPEED:
            return self._speed_model
        return self._general_model

    def get_routing_info(self) -> dict:
        """Return current routing configuration."""
        return {
            "speed_model": self._speed_model,
            "general_model": self._general_model,
            "deep_model": self._deep_model,
            "embedding_model": self._embed_model,
        }


# Singleton
_router: AIRouter | None = None


def get_router(config: dict | None = None) -> AIRouter:
    """Get or create the singleton AIRouter."""
    global _router
    if _router is None:
        _router = AIRouter(config)
    return _router
