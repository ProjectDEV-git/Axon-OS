"""Tests for secret redaction and prompt-context clipping."""

from services.context_settings import REDACTED, clip_context, redact_secrets


class TestRedact:
    def test_tokens(self):
        samples = [
            "sk-abcdefghijklmnopqrstuvwx",
            "sk-ant-api03-abcdefghijklmnopqrstuv",
            "ghp_abcdefghijklmnopqrstuvwxyz0123",
            "AKIAABCDEFGHIJKLMNOP",
            "xoxb-1234567890-abcdef",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
        ]
        for s in samples:
            out = redact_secrets(f"copied {s} here")
            assert s not in out and REDACTED in out, s

    def test_key_value(self):
        out = redact_secrets("export API_KEY=hunter2\nmysql --password hunter2")
        assert "hunter2" not in out
        assert "API_KEY=[redacted]" in out

    def test_private_key(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
        assert "MIIEow" not in redact_secrets(pem)

    def test_plain_text_kept(self):
        text = "Active app: Firefox — reading about skiing"
        assert redact_secrets(text) == text


class TestClip:
    def test_short_unchanged(self):
        assert clip_context("abc", 100) == "abc"

    def test_cuts_at_line(self):
        text = "\n".join(f"line {i}" for i in range(100))
        out = clip_context(text, 100)
        assert out.endswith("[context truncated]")
        assert len(out) < 130
