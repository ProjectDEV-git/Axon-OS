"""Tests for brain_service — sanitization, validation, config logic, and SendMessage."""

import inspect

from services.axon_brain.brain_service import (
    BrainService,
    _sanitize_context,
    _sanitize_output,
)


class TestSanitizeOutput:
    def test_strips_ansi_sequences(self):
        assert _sanitize_output("\x1b[31mred\x1b[0m") == "red"

    def test_strips_null_bytes(self):
        assert _sanitize_output("hello\x00world") == "helloworld"

    def test_clean_text_unchanged(self):
        assert _sanitize_output("normal text") == "normal text"

    def test_strips_complex_ansi(self):
        assert _sanitize_output("\x1b[1;32;40mbold green\x1b[0m") == "bold green"

    def test_empty_string(self):
        assert _sanitize_output("") == ""


class TestSanitizeContext:
    def test_removes_null_bytes(self):
        assert _sanitize_context("hello\x00world") == "helloworld"

    def test_truncates_long_context(self):
        long = "x" * 3000
        result = _sanitize_context(long)
        assert len(result) == 2000

    def test_short_context_unchanged(self):
        assert _sanitize_context("short") == "short"

    def test_empty_context(self):
        assert _sanitize_context("") == ""


class TestValidateModelName:
    def test_valid_names(self):
        assert BrainService._validate_model_name("llama3.2:3b") is True
        assert BrainService._validate_model_name("mistral:7b") is True
        assert BrainService._validate_model_name("nomic-embed-text") is True
        assert BrainService._validate_model_name("library/llama3") is True

    def test_invalid_names(self):
        assert BrainService._validate_model_name("") is False
        assert BrainService._validate_model_name(None) is False
        assert BrainService._validate_model_name(123) is False
        assert BrainService._validate_model_name("../etc/passwd") is False
        assert BrainService._validate_model_name("a" * 300) is False


class TestValidatePrompt:
    def test_valid_prompts(self):
        assert BrainService._validate_prompt("hello") is True
        assert BrainService._validate_prompt("a" * 1000) is True

    def test_invalid_prompts(self):
        assert BrainService._validate_prompt("") is False
        assert BrainService._validate_prompt(None) is False
        assert BrainService._validate_prompt(123) is False
        assert BrainService._validate_prompt("x" * 20000) is False


class TestSendMessageSignature:
    """Verify SendMessage has the correct 5-parameter signature after the context arg fix."""

    def test_send_message_has_context_param(self):
        """SendMessage should accept conversation_id, message, context, model, stream."""
        sig = inspect.signature(BrainService.SendMessage)
        params = list(sig.parameters.keys())
        # First param is 'self'
        assert params[0] == "self"
        assert "conversation_id" in params
        assert "message" in params
        assert "context" in params
        assert "model" in params
        assert "stream" in params

    def test_send_message_expects_five_params(self):
        """SendMessage should accept exactly 5 args (+ self)."""
        sig = inspect.signature(BrainService.SendMessage)
        # Subtract 1 for 'self'
        non_self = [p for p in sig.parameters if p != "self"]
        assert len(non_self) == 5, f"Expected 5 params, got {len(non_self)}: {non_self}"

    def test_send_message_context_before_model(self):
        """Context should come before model in the parameter list."""
        sig = inspect.signature(BrainService.SendMessage)
        params = [p for p in sig.parameters if p != "self"]
        assert params.index("context") < params.index("model"), (
            "context must come before model (D-Bus positional args)"
        )
