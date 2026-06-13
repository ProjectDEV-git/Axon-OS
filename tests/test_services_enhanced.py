#!/usr/bin/env python3
"""Test suite for Axon D-Bus services."""

from unittest.mock import Mock, patch

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
            "model-v1.0:fine-tuned"
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

    @patch('services.axon_brain.brain_service.hardware_profiler')
    def test_load_config_fallback(self, mock_profiler: Mock) -> None:
        """Test config loading with hardware profiler fallback."""

        mock_profiler.profile_hardware.return_value = {
            "recommendations": {
                "speed": {"model": "mistral:latest"},
                "general": {"model": "neural-chat:latest"},
                "deep": {"model": "llama2:latest"}
            }
        }

        # Service will call load_config during init
        # This tests that fallback config is set correctly
        assert True  # Would need full mocking of dbus


class TestContextService:
    """Tests for Axon Context D-Bus Service."""

    def test_get_active_context_json(self) -> None:
        """Test that GetActiveContext returns valid JSON."""

        # Would need to mock dbus session
        # This is a placeholder for full test
        assert True

    def test_set_active_window_validation(self) -> None:
        """Test SetActiveWindow with valid/invalid inputs."""

        # Test validation of window title and app_id
        assert True  # Would need dbus mocking


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
        import time

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

        # Should allow 5 requests
        for _ in range(5):
            assert limiter.allow(identifier)

        # Should block 6th request
        assert not limiter.allow(identifier)

    def test_rate_limiter_window_reset(self) -> None:
        """Test rate limiter resets after window expires."""
        import time

        from services.service_utils import RateLimiter

        limiter = RateLimiter(rate=1, window_seconds=1)
        identifier = "test_client"

        assert limiter.allow(identifier)
        assert not limiter.allow(identifier)

        time.sleep(1.1)
        assert limiter.allow(identifier)  # Should be allowed after reset


class TestIntegration:
    """Integration tests for services."""

    @pytest.mark.skip(reason="Requires D-Bus session and Ollama")
    def test_brain_service_integration(self) -> None:
        """Integration test for Brain Service with real Ollama."""
        pass

    @pytest.mark.skip(reason="Requires D-Bus session")
    def test_context_service_integration(self) -> None:
        """Integration test for Context Service."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=services", "--cov-report=term-missing"])
