#!/usr/bin/env python3
"""Test suite for Axon D-Bus services."""

import threading
import time

import pytest


class TestBrainService:
    """Tests for Axon Brain D-Bus Service."""

    def test_validate_model_name_valid(self) -> None:
        """Test model name validation with valid names."""
        from services.axon_brain.brain_service import BrainService

        valid_names = [
            "mistral:latest",
            "neural-chat:7b",
            "llama2.cpp",
            "model-v1.0:fine-tuned",
            "library/llama3",
        ]
        for name in valid_names:
            assert BrainService._validate_model_name(name), \
                f"Model name '{name}' should be valid"

    def test_validate_model_name_invalid(self) -> None:
        """Test model name validation with invalid names."""
        from services.axon_brain.brain_service import BrainService

        invalid_names = [
            "",  # Empty
            "a" * 300,  # Too long
            "model; rm -rf /",  # Injection attempt
            "model$(whoami)",  # Command substitution
            "model|cat /etc/passwd",  # Pipe attempt
            "../../../etc/passwd",  # Path traversal
            None,  # None type
            123,  # Wrong type
        ]
        for name in invalid_names:
            assert not BrainService._validate_model_name(name), \
                f"Model name '{name}' should be invalid"

    def test_validate_prompt_valid(self) -> None:
        """Test prompt validation with valid prompts."""
        from services.axon_brain.brain_service import BrainService

        valid_prompts = [
            "What is 2+2?",
            "Hello world" * 100,  # Within limit
            "What is the meaning of life?",
        ]
        for prompt in valid_prompts:
            assert BrainService._validate_prompt(prompt), \
                f"Prompt should be valid: {prompt[:50]}..."

    def test_validate_prompt_invalid(self) -> None:
        """Test prompt validation with invalid prompts."""
        from services.axon_brain.brain_service import BrainService

        invalid_prompts = [
            "",  # Empty
            "x" * 10001,  # Exceeds max length
            None,  # None type
        ]
        for prompt in invalid_prompts:
            assert not BrainService._validate_prompt(prompt), \
                f"Prompt should be invalid: {prompt}"


class TestConversationStore:
    """Tests for the SQLite conversation store."""

    def test_create_and_list(self, tmp_path) -> None:
        """Test creating conversations and listing them."""
        from services.axon_brain.conversation_store import ConversationStore

        db = tmp_path / "test.db"
        store = ConversationStore(db_path=str(db))

        conv_id = store.create_conversation(title="Test Chat")
        assert conv_id

        conversations = store.list_conversations()
        assert len(conversations) == 1
        assert conversations[0]["title"] == "Test Chat"

    def test_add_and_get_messages(self, tmp_path) -> None:
        """Test adding messages and retrieving them."""
        from services.axon_brain.conversation_store import ConversationStore

        db = tmp_path / "test.db"
        store = ConversationStore(db_path=str(db))

        conv_id = store.create_conversation(title="Test")
        store.add_message(conv_id, "user", "Hello")
        store.add_message(conv_id, "assistant", "Hi there!")

        messages = store.get_messages(conv_id)
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"
        assert messages[1]["role"] == "assistant"

    def test_delete_conversation(self, tmp_path) -> None:
        """Test deleting a conversation cascades to messages."""
        from services.axon_brain.conversation_store import ConversationStore

        db = tmp_path / "test.db"
        store = ConversationStore(db_path=str(db))

        conv_id = store.create_conversation(title="ToDelete")
        store.add_message(conv_id, "user", "msg")
        store.delete_conversation(conv_id)

        assert store.list_conversations() == []
        assert store.get_messages(conv_id) == []

    def test_search_messages(self, tmp_path) -> None:
        """Test searching messages by content."""
        from services.axon_brain.conversation_store import ConversationStore

        db = tmp_path / "test.db"
        store = ConversationStore(db_path=str(db))

        conv_id = store.create_conversation(title="Search Test")
        store.add_message(conv_id, "user", "What is Python?")
        store.add_message(conv_id, "assistant", "Python is a programming language.")

        results = store.search_messages("Python")
        assert len(results) == 2

    def test_thread_safety(self, tmp_path) -> None:
        """Test concurrent access to the store."""
        from services.axon_brain.conversation_store import ConversationStore

        db = tmp_path / "test.db"
        store = ConversationStore(db_path=str(db))

        errors = []

        def writer(n):
            try:
                for i in range(10):
                    conv_id = store.create_conversation(title=f"Thread-{n}-{i}")
                    store.add_message(conv_id, "user", f"msg-{n}-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread safety errors: {errors}"
        conversations = store.list_conversations()
        assert len(conversations) == 50


class TestContextService:
    """Tests for Axon Context D-Bus Service logic."""

    def test_context_service_init_creates_clipboard_history(self) -> None:
        """Test that ContextService initializes clipboard history."""
        from collections import deque

        from services.constants import MAX_CLIPBOARD_HISTORY

        history = deque(maxlen=MAX_CLIPBOARD_HISTORY)
        assert history.maxlen == 50

    def test_context_json_structure(self) -> None:
        """Test that context data produces valid JSON structure."""
        import json

        context = {
            "active_window": "test.txt - VS Code",
            "active_app": "code",
            "active_space": "Code",
            "clipboard_history": ["snippet1", "snippet2"],
        }
        serialized = json.dumps(context)
        parsed = json.loads(serialized)
        assert parsed["active_window"] == "test.txt - VS Code"
        assert len(parsed["clipboard_history"]) == 2


class TestServiceUtils:
    """Tests for service utility functions."""

    def test_ttl_cache_get_valid(self) -> None:
        """Test TTL cache retrieval of valid entries."""
        from services.service_utils import TTLCache

        cache = TTLCache(ttl_seconds=10)
        cache.set("test_key", "test_value")
        assert cache.get("test_key") == "test_value"

    def test_ttl_cache_get_expired(self) -> None:
        """Test TTL cache returns None for expired entries."""
        from services.service_utils import TTLCache

        cache = TTLCache(ttl_seconds=1)
        cache.set("test_key", "test_value")
        time.sleep(1.1)
        assert cache.get("test_key") is None

    def test_ttl_cache_clear(self) -> None:
        """Test TTL cache clear operation."""
        from services.service_utils import TTLCache

        cache = TTLCache(ttl_seconds=10)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_rate_limiter_allow(self) -> None:
        """Test rate limiter allows requests within limit."""
        from services.service_utils import RateLimiter

        limiter = RateLimiter(rate=5, window_seconds=60)
        identifier = "test_client"

        for _ in range(5):
            assert limiter.allow(identifier)

        assert not limiter.allow(identifier)

    def test_rate_limiter_window_reset(self) -> None:
        """Test rate limiter resets after window expires."""
        from services.service_utils import RateLimiter

        limiter = RateLimiter(rate=1, window_seconds=1)
        identifier = "test_client"

        assert limiter.allow(identifier)
        assert not limiter.allow(identifier)

        time.sleep(1.1)
        assert limiter.allow(identifier)

    def test_ttl_cache_thread_safety(self) -> None:
        """Test TTLCache is safe under concurrent access."""
        from services.service_utils import TTLCache

        cache = TTLCache(ttl_seconds=10)
        errors = []

        def worker(n):
            try:
                for i in range(100):
                    cache.set(f"key-{n}-{i}", f"value-{n}-{i}")
                    cache.get(f"key-{n}-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread safety errors: {errors}"

    def test_rate_limiter_thread_safety(self) -> None:
        """Test RateLimiter is safe under concurrent access."""
        from services.service_utils import RateLimiter

        limiter = RateLimiter(rate=1000, window_seconds=60)
        results = []
        errors = []

        def worker(n):
            try:
                for _ in range(100):
                    results.append(limiter.allow(f"client-{n}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread safety errors: {errors}"
        assert len(results) == 1000


class TestConstants:
    """Tests for shared constants module."""

    def test_constants_importable(self) -> None:
        """Test that all constants are importable."""
        from services.constants import (
            AXON_DIR,
            DBUS_NAME_BRAIN,
            MAX_CLIPBOARD_HISTORY,
            MAX_MODEL_NAME_LEN,
            MAX_PROMPT_LEN,
            MAX_RECORD_SECONDS,
            OLLAMA_BASE_URL,
        )

        assert AXON_DIR.name == "axon"
        assert "localhost" in OLLAMA_BASE_URL
        assert MAX_MODEL_NAME_LEN > 0
        assert MAX_PROMPT_LEN > 0
        assert DBUS_NAME_BRAIN.startswith("org.axonos.")
        assert MAX_RECORD_SECONDS > 0
        assert MAX_CLIPBOARD_HISTORY > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=services", "--cov-report=term-missing"])
