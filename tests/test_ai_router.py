"""Tests for ai_router — task classification and model selection."""

from services.axon_brain.ai_router import AIRouter, get_router


class TestAIRouterClassification:
    def setup_method(self):
        self.router = AIRouter()

    def test_speed_pattern_yes(self):
        assert self.router.classify_task("yes") == "speed"

    def test_speed_pattern_cancel(self):
        assert self.router.classify_task("cancel") == "speed"

    def test_speed_pattern_open_app(self):
        assert self.router.classify_task("open firefox") == "speed"

    def test_speed_pattern_run(self):
        assert self.router.classify_task("run ls") == "speed"

    def test_speed_short_text(self):
        assert self.router.classify_task("hi") == "speed"

    def test_general_explain(self):
        assert self.router.classify_task("explain how photosynthesis works") == "general"

    def test_general_summarize(self):
        assert self.router.classify_task("summarize this article about AI") == "general"

    def test_general_help(self):
        assert self.router.classify_task("help me write a letter to my boss") == "general"

    def test_deep_code_generation(self):
        assert self.router.classify_task("write a function to sort a list") == "deep"

    def test_deep_code_fix(self):
        assert self.router.classify_task("fix this bug in my javascript code here") == "deep"

    def test_deep_long_text(self):
        long_text = "can you elaborate on the meaning of life " * 20
        assert self.router.classify_task(long_text) == "deep"

    def test_embedding_search(self):
        assert self.router.classify_task("search similar documents") == "embedding"

    def test_embedding_vector(self):
        assert self.router.classify_task("find vector embeddings") == "embedding"

    def test_general_fallback(self):
        assert self.router.classify_task("what is the weather like today in Paris") == "general"


class TestAIRouterSelectModel:
    def setup_method(self):
        self.router = AIRouter()

    def test_explicit_model_override(self):
        model, reason = self.router.select_model("anything", explicit_model="custom:7b")
        assert model == "custom:7b"
        assert reason == "user-selected"

    def test_speed_task_returns_speed_model(self):
        model, reason = self.router.select_model("yes")
        assert model == "llama3.2:3b"
        assert reason == "speed-optimal"

    def test_deep_task_returns_deep_model(self):
        model, reason = self.router.select_model("write a function to parse JSON data")
        assert model == "qwen2.5:7b"
        assert reason == "complex-reasoning"

    def test_embedding_task_returns_embed_model(self):
        model, reason = self.router.select_model("search similar vectors")
        assert model == "nomic-embed-text"
        assert reason == "embedding-task"

    def test_general_task_returns_general_model(self):
        model, reason = self.router.select_model("explain quantum computing to me")
        assert model == "mistral:7b"
        assert reason == "general-purpose"


class TestAIRouterHelpers:
    def setup_method(self):
        self.router = AIRouter()

    def test_get_model_for_classify_window(self):
        assert self.router.get_model_for_classify_window() == "llama3.2:3b"

    def test_get_model_for_intent(self):
        assert self.router.get_model_for_intent() == "llama3.2:3b"

    def test_get_model_for_chat_short(self):
        assert self.router.get_model_for_chat(100) == "mistral:7b"

    def test_get_model_for_chat_long(self):
        assert self.router.get_model_for_chat(600) == "qwen2.5:7b"

    def test_get_model_for_generate_speed(self):
        assert self.router.get_model_for_generate("yes") == "llama3.2:3b"

    def test_get_model_for_generate_deep(self):
        assert (
            self.router.get_model_for_generate("write a function for a linked list") == "qwen2.5:7b"
        )

    def test_get_routing_info(self):
        info = self.router.get_routing_info()
        assert "speed_model" in info
        assert "general_model" in info
        assert "deep_model" in info
        assert "embedding_model" in info

    def test_custom_config(self):
        router = AIRouter({"speed_model": "tiny:1b", "general_model": "medium:3b"})
        assert router._speed_model == "tiny:1b"
        assert router._general_model == "medium:3b"


class TestGetRouter:
    def test_returns_singleton(self):
        r1 = get_router()
        r2 = get_router()
        assert r1 is r2
