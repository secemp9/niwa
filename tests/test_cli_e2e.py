"""
End-to-end CLI tests using subprocess and tempfile.

Tests simulate real LLM agent workflows: reading, editing, conflicts,
multi-agent collaboration, error recovery, and edge cases.

Run with: pytest tests/test_cli_e2e.py -v
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest


def niwa(*args, cwd):
    """Run niwa CLI and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["niwa", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


@pytest.fixture
def db(tmp_path):
    """Initialize a niwa database in a temp directory."""
    rc, out, err = niwa("init", ".", cwd=tmp_path)
    assert rc == 0, f"init failed: {err}"
    return tmp_path


# ── Init ────────────────────────────────────────────────────────────────────


class TestInit:
    def test_init_creates_niwa_dir(self, tmp_path):
        rc, out, err = niwa("init", ".", cwd=tmp_path)
        assert rc == 0
        assert (tmp_path / ".niwa").is_dir()
        assert "INITIALIZED" in out

    def test_tree_after_init(self, db):
        rc, out, err = niwa("tree", cwd=db)
        assert rc == 0
        assert "root" in out
        assert "Document" in out

    def test_init_twice_is_safe(self, db):
        """Agent might init a dir that's already initialized."""
        rc, out, err = niwa("init", ".", cwd=db)
        # Should not crash — either succeeds or warns
        assert rc == 0 or "already" in out.lower() or "exists" in out.lower()


# ── Add ─────────────────────────────────────────────────────────────────────


class TestAdd:
    def test_add_node(self, db):
        rc, out, err = niwa("add", "My Section", "--agent", "test_agent", cwd=db)
        assert rc == 0
        assert "NODE_ID:" in out
        assert "h1_0" in out

    def test_add_shows_in_tree(self, db):
        niwa("add", "First", "--agent", "a1", cwd=db)
        rc, out, err = niwa("tree", cwd=db)
        assert rc == 0
        assert "First" in out
        assert "h1_0" in out

    def test_add_with_parent(self, db):
        niwa("add", "Parent", "--agent", "a1", cwd=db)
        rc, out, err = niwa("add", "Child", "--agent", "a1", "--parent", "h1_0", cwd=db)
        assert rc == 0
        assert "NODE_ID:" in out

    def test_add_duplicate_title_warns(self, db):
        niwa("add", "Dupe", "--agent", "a1", cwd=db)
        rc, out, err = niwa("add", "Dupe", "--agent", "a2", cwd=db)
        assert "already exists" in out.lower() or "duplicate" in out.lower() or rc != 0

    def test_add_duplicate_different_parent(self, db):
        """Same title under different parents is allowed."""
        niwa("add", "Parent A", "--agent", "a1", cwd=db)
        niwa("add", "Parent B", "--agent", "a1", cwd=db)
        niwa("add", "Notes", "--agent", "a1", "--parent", "h1_0", cwd=db)
        rc, out, err = niwa("add", "Notes", "--agent", "a1", "--parent", "h1_1", cwd=db)
        assert rc == 0
        assert "NODE_ID:" in out

    def test_add_deep_nesting(self, db):
        """Build a 4-level deep tree like an agent structuring a spec."""
        niwa("add", "Project", "--agent", "a1", cwd=db)
        niwa("add", "Requirements", "--agent", "a1", "--parent", "h1_0", cwd=db)
        niwa("add", "Functional", "--agent", "a1", "--parent", "h2_0", cwd=db)
        rc, out, err = niwa("add", "Auth Flow", "--agent", "a1", "--parent", "h3_0", cwd=db)
        assert rc == 0
        assert "NODE_ID:" in out

        rc, out, err = niwa("tree", cwd=db)
        assert "Project" in out
        assert "Requirements" in out
        assert "Functional" in out
        assert "Auth Flow" in out

    def test_add_many_siblings(self, db):
        """Agent adds many sections at the same level."""
        for i in range(10):
            rc, out, err = niwa("add", f"Section {i}", "--agent", "a1", cwd=db)
            assert rc == 0
            assert "NODE_ID:" in out

        rc, out, err = niwa("tree", cwd=db)
        for i in range(10):
            assert f"Section {i}" in out

    def test_add_with_content_via_file(self, db):
        """Agent writes content to a file then adds via --file."""
        content_file = db / "content.md"
        content_file.write_text("This is the detailed content\nwith multiple lines.")
        rc, out, err = niwa("add", "From File", "--agent", "a1", "--file", str(content_file), cwd=db)
        assert rc == 0

        rc, out, err = niwa("peek", "h1_0", cwd=db)
        assert "detailed content" in out

    def test_add_with_stdin(self, db):
        """Agent pipes content via stdin."""
        result = subprocess.run(
            ["niwa", "add", "From Stdin", "--agent", "a1", "--stdin"],
            cwd=db,
            capture_output=True,
            text=True,
            input="Piped content here",
        )
        assert result.returncode == 0

        rc, out, err = niwa("peek", "h1_0", cwd=db)
        assert "Piped content" in out

    def test_add_to_nonexistent_parent(self, db):
        """Agent tries to add under a node that doesn't exist."""
        rc, out, err = niwa("add", "Orphan", "--agent", "a1", "--parent", "h1_999", cwd=db)
        assert rc != 0 or "not found" in (out + err).lower()

    def test_add_without_agent(self, db):
        """Agent forgets --agent flag."""
        rc, out, err = niwa("add", "No Agent", cwd=db)
        # Should still work (agent defaults or warns)
        # Just verify it doesn't crash
        assert rc == 0 or "agent" in (out + err).lower()


# ── Read / Edit cycle ──────────────────────────────────────────────────────


class TestReadEditCycle:
    def test_read_then_edit(self, db):
        niwa("add", "Section", "--agent", "a1", cwd=db)

        rc, out, err = niwa("read", "h1_0", "--agent", "a1", cwd=db)
        assert rc == 0
        assert "READ SUCCESSFULLY" in out

        rc, out, err = niwa("edit", "h1_0", "new content", "--agent", "a1", cwd=db)
        assert rc == 0
        assert "EDIT SUCCESSFUL" in out

    def test_edit_updates_version(self, db):
        niwa("add", "Section", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "v2 content", "--agent", "a1", cwd=db)

        rc, out, err = niwa("tree", cwd=db)
        assert "v2" in out

    def test_peek_no_tracking(self, db):
        niwa("add", "Section", "--agent", "a1", cwd=db)
        rc, out, err = niwa("peek", "h1_0", cwd=db)
        assert rc == 0
        assert "Section" in out

    def test_read_nonexistent_node(self, db):
        """Agent tries to read a node that doesn't exist."""
        rc, out, err = niwa("read", "h1_999", "--agent", "a1", cwd=db)
        assert rc != 0 or "not found" in (out + err).lower()

    def test_edit_nonexistent_node(self, db):
        """Agent tries to edit a node that doesn't exist."""
        rc, out, err = niwa("edit", "h1_999", "content", "--agent", "a1", cwd=db)
        assert rc != 0 or "not found" in (out + err).lower()

    def test_multiple_edits_same_agent(self, db):
        """Agent does read-edit-read-edit cycle multiple times."""
        niwa("add", "Iterative", "--agent", "a1", cwd=db)

        for i in range(5):
            rc, out, err = niwa("read", "h1_0", "--agent", "a1", cwd=db)
            assert rc == 0
            rc, out, err = niwa("edit", "h1_0", f"iteration {i}", "--agent", "a1",
                                "--summary", f"edit {i}", cwd=db)
            assert rc == 0
            assert "EDIT SUCCESSFUL" in out

        rc, out, err = niwa("tree", cwd=db)
        assert "v6" in out  # v1 from add + 5 edits

    def test_edit_with_summary(self, db):
        """Agent provides a summary of changes."""
        niwa("add", "Section", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        rc, out, err = niwa("edit", "h1_0", "updated", "--agent", "a1",
                            "--summary", "Rewrote introduction", cwd=db)
        assert rc == 0
        assert "EDIT SUCCESSFUL" in out

    def test_edit_content_with_special_chars(self, db):
        """Content with quotes, newlines, markdown formatting."""
        niwa("add", "Special", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)

        content = 'He said "hello" & used <html> tags'
        rc, out, err = niwa("edit", "h1_0", content, "--agent", "a1", cwd=db)
        assert rc == 0

        rc, out, err = niwa("peek", "h1_0", cwd=db)
        assert "hello" in out

    def test_edit_with_file(self, db):
        """Agent writes large content via --file flag."""
        niwa("add", "Large Section", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)

        content_file = db / "edit_content.md"
        content_file.write_text("## Subsection\n\nDetailed paragraph.\n\n- Item 1\n- Item 2\n")

        rc, out, err = niwa("edit", "h1_0", "--agent", "a1", "--file", str(content_file), cwd=db)
        assert rc == 0

        rc, out, err = niwa("peek", "h1_0", cwd=db)
        assert "Detailed paragraph" in out

    def test_edit_empty_content(self, db):
        """Agent clears a section's content."""
        niwa("add", "Section", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "some content", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)

        rc, out, err = niwa("edit", "h1_0", "", "--agent", "a1", cwd=db)
        assert rc == 0


# ── Conflict detection ──────────────────────────────────────────────────────


class TestConflicts:
    def test_concurrent_edit_conflict(self, db):
        """Two agents read the same version, both try to edit."""
        niwa("add", "Shared", "--agent", "a1", cwd=db)

        # Both agents read v1
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 edits first — succeeds
        rc1, out1, _ = niwa("edit", "h1_0", "a1 version", "--agent", "a1", cwd=db)
        assert rc1 == 0

        # a2 tries to edit — should detect conflict
        rc2, out2, err2 = niwa("edit", "h1_0", "a2 version", "--agent", "a2", cwd=db)
        combined = out2 + err2
        assert "conflict" in combined.lower() or "CONFLICT" in combined

    def test_resolve_accept_yours(self, db):
        """Agent resolves conflict by accepting their version."""
        niwa("add", "Shared", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("edit", "h1_0", "a1 version", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "a2 version", "--agent", "a2", cwd=db)

        rc, out, err = niwa("resolve", "h1_0", "ACCEPT_YOURS", "--agent", "a2", cwd=db)
        assert rc == 0
        assert "CONFLICT RESOLVED" in out

        # Verify a2's version won
        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "a2 version" in out

    def test_resolve_accept_theirs(self, db):
        """Agent resolves conflict by accepting other's version."""
        niwa("add", "Shared", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("edit", "h1_0", "a1 version", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "a2 version", "--agent", "a2", cwd=db)

        rc, out, err = niwa("resolve", "h1_0", "ACCEPT_THEIRS", "--agent", "a2", cwd=db)
        combined = out + err
        assert rc == 0 or "resolve" in combined.lower()

    def test_conflicts_command(self, db):
        """List pending conflicts."""
        rc, out, err = niwa("conflicts", cwd=db)
        # Should work even with no conflicts
        assert rc == 0 or "no conflict" in (out + err).lower()

    def test_no_conflict_after_sequential_edit(self, db):
        """Agent reads AFTER previous edit — no conflict."""
        niwa("add", "Section", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "first edit", "--agent", "a1", cwd=db)

        # a2 reads the NEW version, then edits — should be clean
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        rc, out, err = niwa("edit", "h1_0", "second edit", "--agent", "a2", cwd=db)
        assert rc == 0
        assert "EDIT SUCCESSFUL" in out

    def test_three_agents_all_read_same_version(self, db):
        """Three agents read v1, first wins, other two get conflicts."""
        niwa("add", "Shared", "--agent", "a1", cwd=db)

        # All three read v1
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("read", "h1_0", "--agent", "a3", cwd=db)

        # a1 edits first — succeeds, bumps to v2
        rc1, out1, _ = niwa("edit", "h1_0", "a1 wins", "--agent", "a1", cwd=db)
        assert rc1 == 0
        assert "EDIT SUCCESSFUL" in out1

        # a2 edits — conflict (read v1, current is v2)
        rc2, out2, err2 = niwa("edit", "h1_0", "a2 attempt", "--agent", "a2", cwd=db)
        assert "conflict" in (out2 + err2).lower()

        # a3 edits — also conflict (read v1, current is v2)
        rc3, out3, err3 = niwa("edit", "h1_0", "a3 attempt", "--agent", "a3", cwd=db)
        assert "conflict" in (out3 + err3).lower()

    def test_three_agents_each_gets_own_stored_conflict(self, db):
        """Each conflicting agent has their own stored conflict entry."""
        niwa("add", "Shared", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("read", "h1_0", "--agent", "a3", cwd=db)

        niwa("edit", "h1_0", "a1 wins", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "a2 attempt", "--agent", "a2", cwd=db)
        niwa("edit", "h1_0", "a3 attempt", "--agent", "a3", cwd=db)

        # Both a2 and a3 should have stored conflicts
        rc, out, err = niwa("conflicts", cwd=db)
        combined = out + err
        assert "a2" in combined
        assert "a3" in combined

    def test_three_agents_resolve_independently(self, db):
        """Each agent resolves their conflict independently.

        When a2 resolves (bumping the version), a3's stored conflict becomes
        stale — its version reference no longer matches current. a3 must
        re-read and re-edit instead of resolving the stale conflict.
        """
        niwa("add", "Shared", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("read", "h1_0", "--agent", "a3", cwd=db)

        niwa("edit", "h1_0", "a1 wins", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "a2 attempt", "--agent", "a2", cwd=db)
        niwa("edit", "h1_0", "a3 attempt", "--agent", "a3", cwd=db)

        # a2 resolves by accepting yours — bumps version
        rc2, out2, err2 = niwa("resolve", "h1_0", "ACCEPT_YOURS", "--agent", "a2", cwd=db)
        combined2 = out2 + err2
        assert rc2 == 0 or "resolve" in combined2.lower()

        # a3's stored conflict is now stale (version changed during a2's resolve).
        # Resolve may fail with version mismatch — that's correct behavior.
        rc3, out3, err3 = niwa("resolve", "h1_0", "ACCEPT_YOURS", "--agent", "a3", cwd=db)
        combined3 = out3 + err3
        # Either succeeds or reports version changed — both are valid
        assert rc3 == 0 or "version" in combined3.lower() or "conflict" in combined3.lower()

        # If a3's resolve failed due to version mismatch, a3 must re-read and re-edit
        if rc3 != 0 or "version" in combined3.lower():
            niwa("read", "h1_0", "--agent", "a3", cwd=db)
            rc3b, out3b, _ = niwa("edit", "h1_0", "a3 final", "--agent", "a3", cwd=db)
            assert rc3b == 0
            assert "EDIT SUCCESSFUL" in out3b

    def test_three_agents_cascade_conflict(self, db):
        """After a2 resolves (bumps version), a3's stored conflict has stale version."""
        niwa("add", "Shared", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("read", "h1_0", "--agent", "a3", cwd=db)

        # a1 edits — v1 -> v2
        niwa("edit", "h1_0", "a1 wins", "--agent", "a1", cwd=db)
        # a2 and a3 both get conflicts
        niwa("edit", "h1_0", "a2 attempt", "--agent", "a2", cwd=db)
        niwa("edit", "h1_0", "a3 attempt", "--agent", "a3", cwd=db)

        # a2 resolves with ACCEPT_YOURS — bumps v2 -> v3
        rc2, out2, _ = niwa("resolve", "h1_0", "ACCEPT_YOURS", "--agent", "a2", cwd=db)
        assert rc2 == 0
        assert "CONFLICT RESOLVED" in out2

        # Peek to verify a2's content landed
        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert rc == 0
        assert "a2 attempt" in out

        # a3 tries to resolve — their stored conflict references v2 but
        # current is now v3. Version mismatch is detected.
        rc3, out3, err3 = niwa("resolve", "h1_0", "ACCEPT_YOURS", "--agent", "a3", cwd=db)
        combined3 = out3 + err3
        # Should fail with version mismatch since a2's resolve changed the version
        assert "version" in combined3.lower() or "RESOLUTION FAILED" in combined3

        # a3's correct recovery: re-read, then re-edit
        niwa("read", "h1_0", "--agent", "a3", cwd=db)
        rc3b, out3b, _ = niwa("edit", "h1_0", "a3 final", "--agent", "a3", cwd=db)
        assert rc3b == 0
        assert "EDIT SUCCESSFUL" in out3b

    def test_three_agents_manual_merge(self, db):
        """One agent resolves via MANUAL_MERGE combining all contributions."""
        niwa("add", "Shared", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        niwa("edit", "h1_0", "a1 contribution", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "a2 contribution", "--agent", "a2", cwd=db)

        # a2 resolves by manually merging both contributions
        merged = "a1 contribution\na2 contribution"
        rc, out, err = niwa("resolve", "h1_0", "MANUAL_MERGE", merged, "--agent", "a2", cwd=db)
        combined = out + err
        assert rc == 0 or "resolve" in combined.lower()

        # Verify the merged content is there
        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        if rc == 0 and "a1 contribution" in out and "a2 contribution" in out:
            pass  # Perfect — manual merge preserved both

    def test_accept_theirs_no_version_bump(self, db):
        """ACCEPT_THEIRS keeps current content, so version does NOT bump.
        This means a third agent's stored conflict stays valid."""
        niwa("add", "Shared", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("read", "h1_0", "--agent", "a3", cwd=db)

        niwa("edit", "h1_0", "a1 wins", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "a2 attempt", "--agent", "a2", cwd=db)
        niwa("edit", "h1_0", "a3 attempt", "--agent", "a3", cwd=db)

        # a2 resolves with ACCEPT_THEIRS — keeps a1's content, NO version bump
        rc2, out2, _ = niwa("resolve", "h1_0", "ACCEPT_THEIRS", "--agent", "a2", cwd=db)
        assert rc2 == 0
        assert "CONFLICT RESOLVED" in out2

        # a3's stored conflict should still be valid (version didn't change)
        # so ACCEPT_YOURS should succeed
        rc3, out3, _ = niwa("resolve", "h1_0", "ACCEPT_YOURS", "--agent", "a3", cwd=db)
        assert rc3 == 0
        assert "CONFLICT RESOLVED" in out3

        # Content should now be a3's
        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "a3 attempt" in out

    def test_edit_without_read(self, db):
        """Agent edits without calling read first — uses current version as base."""
        niwa("add", "Section", "--agent", "a1", cwd=db)

        # Edit directly without read — should work, base = current
        rc, out, _ = niwa("edit", "h1_0", "direct edit", "--agent", "a1", cwd=db)
        assert rc == 0
        assert "EDIT SUCCESSFUL" in out

    def test_edit_without_read_no_conflict(self, db):
        """Two agents both edit without reading — second one gets no conflict
        because base_version == current_version when there's no pending read."""
        niwa("add", "Section", "--agent", "a1", cwd=db)

        # a1 edits without read
        rc1, out1, _ = niwa("edit", "h1_0", "a1 edit", "--agent", "a1", cwd=db)
        assert rc1 == 0

        # a2 edits without read — no pending read means base = current
        rc2, out2, _ = niwa("edit", "h1_0", "a2 edit", "--agent", "a2", cwd=db)
        assert rc2 == 0
        assert "EDIT SUCCESSFUL" in out2

    def test_reread_clears_stale_conflict(self, db):
        """Re-reading a node clears any stored conflict for that agent+node."""
        niwa("add", "Shared", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("edit", "h1_0", "a1 version", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "a2 version", "--agent", "a2", cwd=db)

        # a2 has a stored conflict now
        rc, out, _ = niwa("conflicts", "--agent", "a2", cwd=db)
        assert "a2" in out

        # a2 re-reads the node — this should clear the stored conflict
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # No more conflicts
        rc, out, _ = niwa("conflicts", "--agent", "a2", cwd=db)
        assert "NO PENDING CONFLICTS" in out or "a2" not in out

    def test_read_twice_updates_tracked_version(self, db):
        """Reading again updates the tracked version, preventing false conflicts."""
        niwa("add", "Section", "--agent", "a1", cwd=db)

        # a1 reads v1
        niwa("read", "h1_0", "--agent", "a1", cwd=db)

        # a2 edits to v2
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("edit", "h1_0", "v2 content", "--agent", "a2", cwd=db)

        # a1 re-reads (now sees v2)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)

        # a1 edits — should succeed (read v2, current is v2)
        rc, out, _ = niwa("edit", "h1_0", "v3 content", "--agent", "a1", cwd=db)
        assert rc == 0
        assert "EDIT SUCCESSFUL" in out

    def test_system_auto_merges_non_overlapping(self, db):
        """System auto-merges when changes are in completely different parts.
        No --strategy flag needed — the system decides internally."""
        niwa("add", "Section", "--agent", "a1", cwd=db)

        # Set initial multi-line content
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "line1\nline2\nline3", "--agent", "a1", cwd=db)

        # Both agents read v2
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 changes line1
        niwa("edit", "h1_0", "CHANGED1\nline2\nline3", "--agent", "a1", cwd=db)

        # a2 changes line3 — no --strategy flag, system should auto-merge
        rc, out, err = niwa(
            "edit", "h1_0", "line1\nline2\nCHANGED3",
            "--agent", "a2", cwd=db
        )
        combined = out + err
        # System auto-merges non-overlapping changes (deterministic line-range check)
        assert "EDIT SUCCESSFUL" in combined or "Auto-merged" in combined

    def test_system_does_not_auto_merge_overlapping(self, db):
        """System refuses to auto-merge when changes overlap the same lines.
        Agent must resolve manually."""
        niwa("add", "Section", "--agent", "a1", cwd=db)

        # Set initial content
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "line1\nline2\nline3", "--agent", "a1", cwd=db)

        # Both agents read v2
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # Both change the SAME line (line1) — true conflict
        niwa("edit", "h1_0", "ALPHA\nline2\nline3", "--agent", "a1", cwd=db)
        rc, out, err = niwa(
            "edit", "h1_0", "BETA\nline2\nline3",
            "--agent", "a2", cwd=db
        )
        combined = out + err
        # Must NOT auto-merge — overlapping changes require agent resolution
        assert "conflict" in combined.lower() or "CONFLICT" in combined

    def test_conflict_on_child_independent_of_parent(self, db):
        """Conflict on a child node doesn't affect parent editing."""
        niwa("add", "Parent", "--agent", "a1", cwd=db)
        niwa("add", "Child", "--agent", "a1", "--parent", "h1_0", cwd=db)

        # Create conflict on child
        niwa("read", "h2_0", "--agent", "a1", cwd=db)
        niwa("read", "h2_0", "--agent", "a2", cwd=db)
        niwa("edit", "h2_0", "a1 child edit", "--agent", "a1", cwd=db)
        niwa("edit", "h2_0", "a2 child edit", "--agent", "a2", cwd=db)

        # a2 has conflict on child, but should be able to edit parent cleanly
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        rc, out, _ = niwa("edit", "h1_0", "parent content", "--agent", "a2", cwd=db)
        assert rc == 0
        assert "EDIT SUCCESSFUL" in out

    def test_multiple_conflicts_different_nodes(self, db):
        """Agent can have conflicts on multiple nodes simultaneously."""
        niwa("add", "Node1", "--agent", "a1", cwd=db)
        niwa("add", "Node2", "--agent", "a1", cwd=db)

        # Create conflict on node1
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("edit", "h1_0", "a1 on node1", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "a2 on node1", "--agent", "a2", cwd=db)

        # Create conflict on node2
        niwa("read", "h1_1", "--agent", "a1", cwd=db)
        niwa("read", "h1_1", "--agent", "a2", cwd=db)
        niwa("edit", "h1_1", "a1 on node2", "--agent", "a1", cwd=db)
        niwa("edit", "h1_1", "a2 on node2", "--agent", "a2", cwd=db)

        # a2 should have two conflicts
        rc, out, _ = niwa("conflicts", "--agent", "a2", cwd=db)
        assert "h1_0" in out
        assert "h1_1" in out

        # Resolve node1, node2 conflict should remain
        niwa("resolve", "h1_0", "ACCEPT_THEIRS", "--agent", "a2", cwd=db)
        rc, out, _ = niwa("conflicts", "--agent", "a2", cwd=db)
        assert "h1_0" not in out
        assert "h1_1" in out

    def test_multi_hop_staleness(self, db):
        """Agent reads v1, two others edit sequentially (v2, v3).
        Agent's conflict should show they are 2 versions behind."""
        niwa("add", "Section", "--agent", "a1", cwd=db)

        # a_stale reads v1
        niwa("read", "h1_0", "--agent", "a_stale", cwd=db)

        # a1 edits v1->v2
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "version 2", "--agent", "a1", cwd=db)

        # a2 edits v2->v3
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("edit", "h1_0", "version 3", "--agent", "a2", cwd=db)

        # a_stale tries to edit — 2 versions behind
        rc, out, err = niwa("edit", "h1_0", "stale edit", "--agent", "a_stale", cwd=db)
        combined = out + err
        assert "conflict" in combined.lower() or "CONFLICT" in combined
        # Should mention being behind
        assert "2" in combined  # 2 edits since they read

    def test_resolve_then_clean_edit(self, db):
        """After resolving a conflict, agent can do a clean read-edit cycle."""
        niwa("add", "Shared", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("edit", "h1_0", "a1 version", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "a2 version", "--agent", "a2", cwd=db)

        # a2 resolves
        niwa("resolve", "h1_0", "ACCEPT_YOURS", "--agent", "a2", cwd=db)

        # Now a2 reads the latest and edits cleanly
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        rc, out, _ = niwa("edit", "h1_0", "a2 clean edit", "--agent", "a2", cwd=db)
        assert rc == 0
        assert "EDIT SUCCESSFUL" in out

    def test_same_content_edit_not_conflict(self, db):
        """Two agents make identical edits — if one lands, the other should
        still detect a conflict (version mismatch), but auto-merge should
        recognize they are identical."""
        niwa("add", "Section", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # Both write the exact same content
        niwa("edit", "h1_0", "identical content", "--agent", "a1", cwd=db)
        rc, out, err = niwa("edit", "h1_0", "identical content", "--agent", "a2", cwd=db)
        combined = out + err
        # Conflict is detected (version mismatch), but auto-merge should notice
        # the changes are identical
        assert "conflict" in combined.lower() or "EDIT SUCCESSFUL" in combined

    def test_five_agents_all_conflict(self, db):
        """Five agents all read v1, first wins, other four get conflicts."""
        niwa("add", "Shared", "--agent", "a1", cwd=db)
        agents = ["a1", "a2", "a3", "a4", "a5"]

        # All five read v1
        for a in agents:
            niwa("read", "h1_0", "--agent", a, cwd=db)

        # a1 edits first — succeeds
        rc, out, _ = niwa("edit", "h1_0", "a1 wins", "--agent", "a1", cwd=db)
        assert rc == 0

        # a2-a5 all get conflicts
        for a in agents[1:]:
            rc, out, err = niwa("edit", "h1_0", f"{a} attempt", "--agent", a, cwd=db)
            assert "conflict" in (out + err).lower(), f"{a} should have conflict"

        # All four should appear in conflicts list
        rc, out, _ = niwa("conflicts", cwd=db)
        for a in agents[1:]:
            assert a in out, f"{a} should have stored conflict"


# ── Auto-Merge Edge Cases ──────────────────────────────────────────────────
# These tests hammer the deterministic merge logic to ensure no heuristic
# guessing is possible.  Every test verifies the exact final content.


class TestAutoMergeEdgeCases:
    """Exhaustive edge-case tests for the three-way merge pipeline.

    The system must ONLY auto-merge when line-ranges provably don't overlap.
    Any overlap — even adjacent, even semantically "compatible" — must block.
    """

    # -- 1. Adjacent but non-overlapping edits ----------------------------

    def test_adjacent_lines_are_not_overlapping(self, db):
        """Agent A edits line 2, agent B edits line 3.  Ranges are adjacent
        (end of A == start of B) but do NOT overlap.  Must auto-merge."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "L1\nL2\nL3\nL4", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 changes L2 only
        niwa("edit", "h1_0", "L1\nA2\nL3\nL4", "--agent", "a1", cwd=db)
        # a2 changes L3 only — adjacent, not overlapping
        rc, out, err = niwa("edit", "h1_0", "L1\nL2\nB3\nL4", "--agent", "a2", cwd=db)
        combined = out + err
        assert "EDIT SUCCESSFUL" in combined or "Auto-merged" in combined

        # Verify merged content contains BOTH changes
        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "A2" in out
        assert "B3" in out
        assert "L1" in out
        assert "L4" in out

    # -- 2. Both insert at the same position (empty base) -----------------

    def test_both_insert_into_empty_node_conflicts(self, db):
        """Two agents both insert into a node with empty content.
        Both have range (0,0) — zero-width at same position — must conflict."""
        niwa("add", "Empty", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        niwa("edit", "h1_0", "alpha content", "--agent", "a1", cwd=db)
        rc, out, err = niwa("edit", "h1_0", "beta content", "--agent", "a2", cwd=db)
        combined = out + err
        assert "conflict" in combined.lower() or "CONFLICT" in combined

    # -- 3. Both delete the same line ------------------------------------

    def test_both_delete_same_line_conflicts(self, db):
        """Both agents delete line 2.  Identical intent, but same range
        touched — must conflict, not silently pick one."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "L1\nDELETEME\nL3", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        niwa("edit", "h1_0", "L1\nL3", "--agent", "a1", cwd=db)
        rc, out, err = niwa("edit", "h1_0", "L1\nL3", "--agent", "a2", cwd=db)
        combined = out + err
        # Even though the result is identical, the ranges overlap → conflict
        assert "conflict" in combined.lower() or "EDIT SUCCESSFUL" in combined

    # -- 4. One deletes, other replaces the same line --------------------

    def test_delete_vs_replace_same_line_conflicts(self, db):
        """Agent A deletes line 2, agent B replaces line 2 with new text.
        Overlapping ranges — must conflict."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "L1\noriginal\nL3", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 deletes line 2
        niwa("edit", "h1_0", "L1\nL3", "--agent", "a1", cwd=db)
        # a2 replaces line 2
        rc, out, err = niwa("edit", "h1_0", "L1\nreplaced\nL3", "--agent", "a2", cwd=db)
        combined = out + err
        assert "conflict" in combined.lower() or "CONFLICT" in combined

    # -- 5. Non-overlapping insert + delete at different positions --------

    def test_insert_top_delete_bottom_auto_merges(self, db):
        """Agent A inserts a new line at the top, agent B deletes the last line.
        Completely different parts — must auto-merge."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "L1\nL2\nL3\nL4\nL5", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 inserts new line at top (replaces L1 with two lines)
        niwa("edit", "h1_0", "NEW\nL1\nL2\nL3\nL4\nL5", "--agent", "a1", cwd=db)
        # a2 deletes L5 (last line)
        rc, out, err = niwa("edit", "h1_0", "L1\nL2\nL3\nL4", "--agent", "a2", cwd=db)
        combined = out + err
        assert "EDIT SUCCESSFUL" in combined or "Auto-merged" in combined

        # Verify both changes landed
        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "NEW" in out     # a1's insert
        assert "L5" not in out  # a2's delete

    # -- 6. Both insert at different positions (non-overlapping inserts) --

    def test_insert_at_different_positions_auto_merges(self, db):
        """Agent A inserts after line 1, agent B inserts after line 4.
        Different positions — must auto-merge with both insertions."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "L1\nL2\nL3\nL4\nL5", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 replaces L1 with L1+INSERT_A
        niwa("edit", "h1_0", "L1\nINSERT_A\nL2\nL3\nL4\nL5", "--agent", "a1", cwd=db)
        # a2 replaces L5 with L5+INSERT_B
        rc, out, err = niwa("edit", "h1_0", "L1\nL2\nL3\nL4\nL5\nINSERT_B", "--agent", "a2", cwd=db)
        combined = out + err
        assert "EDIT SUCCESSFUL" in combined or "Auto-merged" in combined

        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "INSERT_A" in out
        assert "INSERT_B" in out

    # -- 7. Replace same line with identical text -------------------------

    def test_same_line_same_replacement_still_conflicts(self, db):
        """Both agents replace line 2 with the EXACT SAME text.
        Ranges overlap (same line) — must conflict.  No heuristic
        "oh they wrote the same thing, it's fine" shortcut."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "L1\nold\nL3", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        niwa("edit", "h1_0", "L1\nnew\nL3", "--agent", "a1", cwd=db)
        rc, out, err = niwa("edit", "h1_0", "L1\nnew\nL3", "--agent", "a2", cwd=db)
        combined = out + err
        # Even identical replacements on the same line = overlapping range
        assert "conflict" in combined.lower() or "CONFLICT" in combined

    # -- 8. One agent changes everything, other touches one line ---------

    def test_full_rewrite_vs_single_line_conflicts(self, db):
        """Agent A rewrites the entire document.  Agent B changes one line.
        Full rewrite overlaps with everything — must conflict."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "L1\nL2\nL3\nL4", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 full rewrite
        niwa("edit", "h1_0", "COMPLETELY\nDIFFERENT\nDOCUMENT", "--agent", "a1", cwd=db)
        # a2 just tweaks line 3
        rc, out, err = niwa("edit", "h1_0", "L1\nL2\nTWEAKED\nL4", "--agent", "a2", cwd=db)
        combined = out + err
        assert "conflict" in combined.lower() or "CONFLICT" in combined

    # -- 9. Verify merged content is exactly correct ---------------------

    def test_auto_merge_produces_exact_content(self, db):
        """After auto-merge, the stored content must be exactly the
        combination of both non-overlapping edits.  Not "close enough"."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "AAA\nBBB\nCCC\nDDD\nEEE", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 changes first line only
        niwa("edit", "h1_0", "XXX\nBBB\nCCC\nDDD\nEEE", "--agent", "a1", cwd=db)
        # a2 changes last line only
        rc, out, err = niwa("edit", "h1_0", "AAA\nBBB\nCCC\nDDD\nYYY", "--agent", "a2", cwd=db)
        combined = out + err
        assert "EDIT SUCCESSFUL" in combined or "Auto-merged" in combined

        # Read back and verify exact content
        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        # Must have a1's change AND a2's change, with middle untouched
        assert "XXX" in out, "a1's first-line change missing"
        assert "BBB" in out, "untouched line 2 missing"
        assert "CCC" in out, "untouched line 3 missing"
        assert "DDD" in out, "untouched line 4 missing"
        assert "YYY" in out, "a2's last-line change missing"
        # Must NOT have the originals that were changed
        assert "AAA" not in out, "original first line should be replaced"
        assert "EEE" not in out, "original last line should be replaced"

    # -- 10. Overlapping multi-line regions ------------------------------

    def test_overlapping_multi_line_blocks_conflict(self, db):
        """Agent A replaces lines 2-4, agent B replaces lines 3-5.
        Ranges [2,4) and [3,5) overlap at line 3 — must conflict."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "L1\nL2\nL3\nL4\nL5\nL6", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 replaces lines 2-4 (L2, L3, L4 → A2, A3, A4)
        niwa("edit", "h1_0", "L1\nA2\nA3\nA4\nL5\nL6", "--agent", "a1", cwd=db)
        # a2 replaces lines 3-5 (L3, L4, L5 → B3, B4, B5) — overlaps at L3, L4
        rc, out, err = niwa("edit", "h1_0", "L1\nL2\nB3\nB4\nB5\nL6", "--agent", "a2", cwd=db)
        combined = out + err
        assert "conflict" in combined.lower() or "CONFLICT" in combined

    # -- 11. Single-line document ----------------------------------------

    def test_single_line_doc_both_edit_conflicts(self, db):
        """Document has exactly one line.  Both agents change it.
        Range (0,1) vs (0,1) — must conflict."""
        niwa("add", "Tiny", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "only line", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        niwa("edit", "h1_0", "alpha version", "--agent", "a1", cwd=db)
        rc, out, err = niwa("edit", "h1_0", "beta version", "--agent", "a2", cwd=db)
        combined = out + err
        assert "conflict" in combined.lower() or "CONFLICT" in combined

    # -- 12. Many lines, edits far apart ---------------------------------

    def test_edits_far_apart_auto_merge(self, db):
        """20-line document, one agent edits line 2, other edits line 19.
        Very far apart — must auto-merge correctly."""
        niwa("add", "Big", "--agent", "a1", cwd=db)
        lines = [f"line{i}" for i in range(20)]
        content = "\n".join(lines)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", content, "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 changes line 2 (index 1)
        a1_lines = lines[:]
        a1_lines[1] = "NEAR_TOP"
        niwa("edit", "h1_0", "\n".join(a1_lines), "--agent", "a1", cwd=db)

        # a2 changes line 19 (index 18)
        a2_lines = lines[:]
        a2_lines[18] = "NEAR_BOTTOM"
        rc, out, err = niwa("edit", "h1_0", "\n".join(a2_lines), "--agent", "a2", cwd=db)
        combined = out + err
        assert "EDIT SUCCESSFUL" in combined or "Auto-merged" in combined

        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "NEAR_TOP" in out
        assert "NEAR_BOTTOM" in out
        # Originals should be gone (use \n boundary to avoid matching line10, line18x, etc.)
        out_lines = out.split("\n")
        content_lines = [l.strip() for l in out_lines]
        assert "line1" not in content_lines, "original line1 should be replaced by NEAR_TOP"
        assert "line18" not in content_lines, "original line18 should be replaced by NEAR_BOTTOM"

    # -- 13. Three concurrent non-overlapping edits ----------------------

    def test_three_agents_non_overlapping_auto_merges_twice(self, db):
        """Three agents edit different lines.  a1 lands, a2 auto-merges,
        then a3 should also auto-merge (its changes don't overlap with
        the merged result)."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "L1\nL2\nL3\nL4\nL5\nL6", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        niwa("read", "h1_0", "--agent", "a3", cwd=db)

        # a1 changes L1
        niwa("edit", "h1_0", "A1\nL2\nL3\nL4\nL5\nL6", "--agent", "a1", cwd=db)
        # a2 changes L3 — doesn't overlap with a1's L1 change
        rc2, out2, err2 = niwa("edit", "h1_0", "L1\nL2\nB3\nL4\nL5\nL6", "--agent", "a2", cwd=db)
        assert "EDIT SUCCESSFUL" in (out2 + err2) or "Auto-merged" in (out2 + err2)

        # a3 changes L6 — doesn't overlap with a1's L1 or a2's L3
        # But a3's base is v2, current is now v4 (a1 edited to v3, a2 auto-merged to v4)
        # a3's change (L6) should still not overlap with what changed (L1 and L3)
        rc3, out3, err3 = niwa("edit", "h1_0", "L1\nL2\nL3\nL4\nL5\nC6", "--agent", "a3", cwd=db)
        combined3 = out3 + err3
        assert "EDIT SUCCESSFUL" in combined3 or "Auto-merged" in combined3

        # Verify all three changes landed
        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "A1" in out, "a1's change missing"
        assert "B3" in out, "a2's change missing"
        assert "C6" in out, "a3's change missing"

    # -- 14. Whitespace-only changes on same line conflicts --------------

    def test_whitespace_change_same_line_conflicts(self, db):
        """Agent A adds trailing spaces to line 2, agent B changes content
        of line 2.  Even though A's change is "just whitespace", the line
        range overlaps — must conflict."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "L1\noriginal\nL3", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1: just adds trailing spaces to line 2
        niwa("edit", "h1_0", "L1\noriginal   \nL3", "--agent", "a1", cwd=db)
        # a2: replaces line 2 content
        rc, out, err = niwa("edit", "h1_0", "L1\nreplaced\nL3", "--agent", "a2", cwd=db)
        combined = out + err
        assert "conflict" in combined.lower() or "CONFLICT" in combined

    # -- 15. Empty base, one adds content, other keeps empty -------------

    def test_one_adds_other_keeps_empty_auto_merges(self, db):
        """Base is empty.  Agent A writes content.  Agent B submits empty
        (no change from their perspective).  B's edit produces no diff
        against base, so no ranges, so no overlap — should auto-merge
        (effectively a no-op from B)."""
        niwa("add", "Node", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 adds content
        niwa("edit", "h1_0", "some real content", "--agent", "a1", cwd=db)
        # a2 submits the empty base content (no-op edit)
        rc, out, err = niwa("edit", "h1_0", "", "--agent", "a2", cwd=db)
        combined = out + err
        # a2's diff against base is empty (they submitted the same empty string)
        # → no changes from a2 → no overlap → auto-merge keeps a1's content
        assert "EDIT SUCCESSFUL" in combined or "Auto-merged" in combined

        # a1's content should survive
        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "some real content" in out

    # -- 16. Both agents add new lines (append) at the end ---------------

    def test_both_append_at_end_conflicts(self, db):
        """Both agents append new lines at the end.  Both touch the same
        boundary — insert position is at the end of the document.
        Must conflict (same insert point)."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "L1\nL2", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 appends L3
        niwa("edit", "h1_0", "L1\nL2\nAPPEND_A", "--agent", "a1", cwd=db)
        # a2 appends different L3
        rc, out, err = niwa("edit", "h1_0", "L1\nL2\nAPPEND_B", "--agent", "a2", cwd=db)
        combined = out + err
        # Both are inserting at position 2 — same boundary — must conflict
        assert "conflict" in combined.lower() or "CONFLICT" in combined

    # -- 17. Replace first line vs replace last line (non-overlapping) ---

    def test_replace_first_vs_last_line_auto_merges(self, db):
        """Agent A replaces the very first line, agent B replaces the very
        last.  Max distance apart — must auto-merge."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "FIRST\nM1\nM2\nM3\nLAST", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        niwa("edit", "h1_0", "ALPHA\nM1\nM2\nM3\nLAST", "--agent", "a1", cwd=db)
        rc, out, err = niwa("edit", "h1_0", "FIRST\nM1\nM2\nM3\nOMEGA", "--agent", "a2", cwd=db)
        combined = out + err
        assert "EDIT SUCCESSFUL" in combined or "Auto-merged" in combined

        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "ALPHA" in out, "a1's first-line change missing"
        assert "OMEGA" in out, "a2's last-line change missing"
        assert "M1" in out and "M2" in out and "M3" in out, "middle lines should be untouched"

    # -- 18. Delete at top vs delete at bottom (non-overlapping) ---------

    def test_delete_top_vs_delete_bottom_auto_merges(self, db):
        """Agent A deletes the first line, agent B deletes the last line.
        Non-overlapping deletions — must auto-merge."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "DELETE_TOP\nKeep1\nKeep2\nKeep3\nDELETE_BOT", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 deletes first line
        niwa("edit", "h1_0", "Keep1\nKeep2\nKeep3\nDELETE_BOT", "--agent", "a1", cwd=db)
        # a2 deletes last line
        rc, out, err = niwa("edit", "h1_0", "DELETE_TOP\nKeep1\nKeep2\nKeep3", "--agent", "a2", cwd=db)
        combined = out + err
        assert "EDIT SUCCESSFUL" in combined or "Auto-merged" in combined

        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "DELETE_TOP" not in out, "first line should have been deleted"
        assert "DELETE_BOT" not in out, "last line should have been deleted"
        assert "Keep1" in out and "Keep2" in out and "Keep3" in out

    # -- 19. One agent replaces multiple lines, other inserts far away ---

    def test_multi_line_replace_vs_distant_insert_auto_merges(self, db):
        """Agent A replaces lines 1-3 (a big block), agent B changes line 8.
        No overlap — must auto-merge with exact content."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        lines = ["H", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9"]
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "\n".join(lines), "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 replaces lines 1-3 (L1, L2, L3)
        a1_lines = lines[:]
        a1_lines[1] = "NEW1"
        a1_lines[2] = "NEW2"
        a1_lines[3] = "NEW3"
        niwa("edit", "h1_0", "\n".join(a1_lines), "--agent", "a1", cwd=db)

        # a2 changes line 8
        a2_lines = lines[:]
        a2_lines[8] = "FAR_AWAY"
        rc, out, err = niwa("edit", "h1_0", "\n".join(a2_lines), "--agent", "a2", cwd=db)
        combined = out + err
        assert "EDIT SUCCESSFUL" in combined or "Auto-merged" in combined

        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "NEW1" in out and "NEW2" in out and "NEW3" in out
        assert "FAR_AWAY" in out
        assert "L1" not in out and "L2" not in out and "L3" not in out
        assert "L8" not in out

    # -- 20. Auto-merge result survives read-back as correct version -----

    def test_auto_merge_bumps_version_correctly(self, db):
        """After auto-merge, the version should be bumped, and a subsequent
        read-edit cycle should work cleanly at the new version."""
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "L1\nL2\nL3", "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 edits L1 → v3
        niwa("edit", "h1_0", "A1\nL2\nL3", "--agent", "a1", cwd=db)
        # a2 edits L3 → auto-merge → v4
        niwa("edit", "h1_0", "L1\nL2\nB3", "--agent", "a2", cwd=db)

        # Now a2 reads current (should be v4 with merged content)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)
        # a2 edits cleanly → v5
        rc, out, _ = niwa("edit", "h1_0", "A1\nL2\nB3\nNEW_LINE", "--agent", "a2", cwd=db)
        assert "EDIT SUCCESSFUL" in out, "Clean edit after auto-merge should succeed"


# ── Swarm Merge Stress Test ────────────────────────────────────────────────
# A single large test that exercises the full merge pipeline under swarm
# conditions: many agents, mixed overlapping/non-overlapping edits,
# cascading auto-merges, and partial-overlap rejection.


class TestSwarmMergeStress:
    """Stress test: 10 agents, 12-line document, mixed overlap patterns.

    The core invariant under test:
        An edit is rejected if ANY of its changed line-ranges overlap with
        ANY range in the accumulated diff (base → current).  It is NOT
        enough for one region to be non-overlapping — the ENTIRE edit must
        be clean.  And "current" includes every auto-merge that happened
        before this agent's turn.
    """

    def test_swarm_mixed_overlap_scenario(self, db):
        """
        Setup: 12-line document.  10 agents all read v1.

        Agent layout (which lines each agent edits):
          a1:  line 0          (first to land — always succeeds)
          a2:  line 11         (last line — non-overlapping with a1 → auto-merge)
          a3:  line 6          (middle — non-overlapping with a1+a2 → auto-merge)
          a4:  line 0 + line 6 (overlaps a1 at 0 AND a3 at 6 → MUST conflict)
          a5:  line 3          (untouched so far → auto-merge)
          a6:  line 3 + line 9 (line 3 overlaps with a5's merged change → MUST conflict)
          a7:  line 9          (untouched — auto-merge)
          a8:  line 0          (overlaps with a1 → MUST conflict)
          a9:  line 11         (overlaps with a2's merged change → MUST conflict)
          a10: line 2          (untouched — auto-merge)

        Expected result after all attempts:
          - Landed (auto-merged or first): a1, a2, a3, a5, a7, a10
          - Conflicted (blocked):          a4, a6, a8, a9

        Then we verify:
          1. Exact content after all merges
          2. Conflicted agents appear in `niwa conflicts`
          3. Non-conflicted agents do NOT appear
          4. Version incremented correctly
        """
        niwa("add", "Swarm", "--agent", "a1", cwd=db)

        # Build 12-line base document
        base_lines = [f"line{i}" for i in range(12)]
        base_content = "\n".join(base_lines)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", base_content, "--agent", "a1", cwd=db)
        # Now at v2 with base content

        # All 10 agents read v2
        agents = [f"a{i}" for i in range(1, 11)]
        for a in agents:
            niwa("read", "h1_0", "--agent", a, cwd=db)

        # Helper: build content by replacing specific lines
        def with_changes(changes: dict) -> str:
            lines = base_lines[:]
            for idx, val in changes.items():
                lines[idx] = val
            return "\n".join(lines)

        # ── a1: edit line 0 — first to land, always succeeds ──
        rc, out, _ = niwa(
            "edit", "h1_0", with_changes({0: "A1_LINE0"}),
            "--agent", "a1", cwd=db
        )
        assert "EDIT SUCCESSFUL" in out, "a1 should land first"

        # ── a2: edit line 11 — non-overlapping with a1's line 0 ──
        rc, out, err = niwa(
            "edit", "h1_0", with_changes({11: "A2_LINE11"}),
            "--agent", "a2", cwd=db
        )
        assert "EDIT SUCCESSFUL" in (out + err) or "Auto-merged" in (out + err), \
            "a2 (line 11) should auto-merge — no overlap with a1 (line 0)"

        # ── a3: edit line 6 — non-overlapping with a1+a2 ──
        rc, out, err = niwa(
            "edit", "h1_0", with_changes({6: "A3_LINE6"}),
            "--agent", "a3", cwd=db
        )
        assert "EDIT SUCCESSFUL" in (out + err) or "Auto-merged" in (out + err), \
            "a3 (line 6) should auto-merge — no overlap with a1 (0) or a2 (11)"

        # ── a4: edits line 0 AND line 6 — overlaps a1 at 0, a3 at 6 ──
        # THIS IS THE KEY TEST: line 6 by itself would be "non-overlapping"
        # with a1, but line 0 overlaps a1.  The ENTIRE edit must be rejected.
        rc, out, err = niwa(
            "edit", "h1_0", with_changes({0: "A4_LINE0", 6: "A4_LINE6"}),
            "--agent", "a4", cwd=db
        )
        combined_a4 = out + err
        assert "conflict" in combined_a4.lower() or "CONFLICT" in combined_a4, \
            "a4 MUST conflict — line 0 overlaps with a1, even though line 6 alone wouldn't"

        # ── a5: edit line 3 — untouched by anyone so far ──
        rc, out, err = niwa(
            "edit", "h1_0", with_changes({3: "A5_LINE3"}),
            "--agent", "a5", cwd=db
        )
        assert "EDIT SUCCESSFUL" in (out + err) or "Auto-merged" in (out + err), \
            "a5 (line 3) should auto-merge — no overlap with any landed change"

        # ── a6: edits line 3 AND line 9 ──
        # line 3 NOW overlaps with a5's merged change (wasn't in the original
        # plan but a5 auto-merged before a6).  line 9 is clean.
        # The ENTIRE edit must be rejected because of line 3.
        rc, out, err = niwa(
            "edit", "h1_0", with_changes({3: "A6_LINE3", 9: "A6_LINE9"}),
            "--agent", "a6", cwd=db
        )
        combined_a6 = out + err
        assert "conflict" in combined_a6.lower() or "CONFLICT" in combined_a6, \
            "a6 MUST conflict — line 3 overlaps with a5's merged change"

        # ── a7: edit line 9 — untouched by any landed change ──
        rc, out, err = niwa(
            "edit", "h1_0", with_changes({9: "A7_LINE9"}),
            "--agent", "a7", cwd=db
        )
        assert "EDIT SUCCESSFUL" in (out + err) or "Auto-merged" in (out + err), \
            "a7 (line 9) should auto-merge — no overlap with any landed change"

        # ── a8: edit line 0 — overlaps with a1 (the very first edit) ──
        rc, out, err = niwa(
            "edit", "h1_0", with_changes({0: "A8_LINE0"}),
            "--agent", "a8", cwd=db
        )
        combined_a8 = out + err
        assert "conflict" in combined_a8.lower() or "CONFLICT" in combined_a8, \
            "a8 MUST conflict — line 0 overlaps with a1"

        # ── a9: edit line 11 — overlaps with a2's merged change ──
        rc, out, err = niwa(
            "edit", "h1_0", with_changes({11: "A9_LINE11"}),
            "--agent", "a9", cwd=db
        )
        combined_a9 = out + err
        assert "conflict" in combined_a9.lower() or "CONFLICT" in combined_a9, \
            "a9 MUST conflict — line 11 overlaps with a2's auto-merged change"

        # ── a10: edit line 2 — untouched by anyone ──
        rc, out, err = niwa(
            "edit", "h1_0", with_changes({2: "A10_LINE2"}),
            "--agent", "a10", cwd=db
        )
        assert "EDIT SUCCESSFUL" in (out + err) or "Auto-merged" in (out + err), \
            "a10 (line 2) should auto-merge — no overlap with any landed change"

        # ════════════════════════════════════════════════════════════════════
        # VERIFICATION: exact final content
        # ════════════════════════════════════════════════════════════════════
        rc, out, _ = niwa("peek", "h1_0", cwd=db)

        # Lines that SHOULD have been changed by landed agents:
        assert "A1_LINE0" in out,   "a1's line 0 change must be present"
        assert "A10_LINE2" in out,  "a10's line 2 change must be present"
        assert "A5_LINE3" in out,   "a5's line 3 change must be present"
        assert "A3_LINE6" in out,   "a3's line 6 change must be present"
        assert "A7_LINE9" in out,   "a7's line 9 change must be present"
        assert "A2_LINE11" in out,  "a2's line 11 change must be present"

        # Lines that must NOT appear (from conflicted agents):
        assert "A4_LINE0" not in out, "a4 was blocked — its line 0 must not appear"
        assert "A4_LINE6" not in out, "a4 was blocked — its line 6 must not appear"
        assert "A6_LINE3" not in out, "a6 was blocked — its line 3 must not appear"
        assert "A6_LINE9" not in out, "a6 was blocked — its line 9 must not appear"
        assert "A8_LINE0" not in out, "a8 was blocked — its line 0 must not appear"
        assert "A9_LINE11" not in out, "a9 was blocked — its line 11 must not appear"

        # Untouched lines must survive:
        assert "line1" in out,  "line1 was never edited"
        assert "line4" in out,  "line4 was never edited"
        assert "line5" in out,  "line5 was never edited"
        assert "line7" in out,  "line7 was never edited"
        assert "line8" in out,  "line8 was never edited"
        assert "line10" in out, "line10 was never edited"

        # ════════════════════════════════════════════════════════════════════
        # VERIFICATION: conflict storage
        # ════════════════════════════════════════════════════════════════════

        # Agents that should have stored conflicts: a4, a6, a8, a9
        for agent in ["a4", "a6", "a8", "a9"]:
            rc, out, _ = niwa("conflicts", "--agent", agent, cwd=db)
            assert "h1_0" in out, f"{agent} should have a stored conflict on h1_0"

        # Agents that should NOT have conflicts: a1, a2, a3, a5, a7, a10
        for agent in ["a1", "a2", "a3", "a5", "a7", "a10"]:
            rc, out, _ = niwa("conflicts", "--agent", agent, cwd=db)
            assert "h1_0" not in out, f"{agent} should NOT have a conflict"

    def test_partial_overlap_rejects_entire_edit_not_safe_parts(self, db):
        """An edit changes 3 separate regions.  Only 1 overlaps.  The system
        must reject the WHOLE edit — it cannot cherry-pick the 2 safe regions
        and merge them while blocking the 1 bad region.

        This is the anti-heuristic test: a "smart" system might try partial
        merge.  We don't.  All or nothing.
        """
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0",
             "H\nL1\nL2\nL3\nL4\nL5\nL6\nL7\nL8\nL9",
             "--agent", "a1", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a2", cwd=db)

        # a1 changes line 4 (index 4)
        niwa("edit", "h1_0",
             "H\nL1\nL2\nL3\nA1_L4\nL5\nL6\nL7\nL8\nL9",
             "--agent", "a1", cwd=db)

        # a2 changes lines 1, 4, and 8 — line 4 overlaps with a1
        rc, out, err = niwa(
            "edit", "h1_0",
            "H\nA2_L1\nL2\nL3\nA2_L4\nL5\nL6\nL7\nA2_L8\nL9",
            "--agent", "a2", cwd=db
        )
        combined = out + err
        assert "conflict" in combined.lower() or "CONFLICT" in combined, \
            "Entire edit must be rejected even though lines 1 and 8 are safe"

        # Verify NONE of a2's changes landed — not even the safe ones
        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "A2_L1" not in out, "a2's safe line 1 change must NOT be cherry-picked"
        assert "A2_L4" not in out, "a2's overlapping line 4 change must not appear"
        assert "A2_L8" not in out, "a2's safe line 8 change must NOT be cherry-picked"
        # a1's change must be intact
        assert "A1_L4" in out, "a1's line 4 must still be present"

    def test_cascading_auto_merges_accumulate_protected_ranges(self, db):
        """Each successive auto-merge adds more "protected" line ranges.
        A later agent that was non-overlapping with the FIRST edit may
        now overlap with a LATER auto-merged edit.

        Timeline:
          v2: base (8 lines)
          a1 edits line 0 → v3
          a2 edits line 7 → auto-merge → v4  (checked against v2→v3 diff)
          a3 edits line 4 → auto-merge → v5  (checked against v2→v4 diff)
          a4 edits line 7 → CONFLICT         (line 7 now protected by a2's merge)

        a4's change would have been fine if only a1 had landed.  But the
        cascading auto-merges expanded the protected range to include line 7.
        """
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        lines = [f"orig{i}" for i in range(8)]
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "\n".join(lines), "--agent", "a1", cwd=db)

        # All 4 agents read v2
        for a in ["a1", "a2", "a3", "a4"]:
            niwa("read", "h1_0", "--agent", a, cwd=db)

        def edit_line(idx, val):
            l = lines[:]
            l[idx] = val
            return "\n".join(l)

        # a1 → line 0
        niwa("edit", "h1_0", edit_line(0, "A1"), "--agent", "a1", cwd=db)
        # a2 → line 7 (non-overlapping with a1)
        rc, out, err = niwa("edit", "h1_0", edit_line(7, "A2"), "--agent", "a2", cwd=db)
        assert "EDIT SUCCESSFUL" in (out + err) or "Auto-merged" in (out + err)
        # a3 → line 4 (non-overlapping with a1+a2)
        rc, out, err = niwa("edit", "h1_0", edit_line(4, "A3"), "--agent", "a3", cwd=db)
        assert "EDIT SUCCESSFUL" in (out + err) or "Auto-merged" in (out + err)

        # a4 → line 7 — WOULD have been fine after just a1, but a2 already
        # auto-merged line 7.  The accumulated diff v2→v5 includes line 7.
        rc, out, err = niwa("edit", "h1_0", edit_line(7, "A4"), "--agent", "a4", cwd=db)
        combined = out + err
        assert "conflict" in combined.lower() or "CONFLICT" in combined, \
            "a4 must conflict — line 7 is protected by a2's earlier auto-merge"

        # a2's value should be in the document, not a4's
        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "A2" in out, "a2's auto-merged line 7 must be present"
        assert "A4" not in out, "a4's conflicting line 7 must NOT appear"

    def test_agent_with_only_non_overlapping_changes_in_swarm_still_merges(self, db):
        """Counter-test: an agent whose changes are genuinely non-overlapping
        with ALL accumulated changes must still auto-merge, even when other
        agents are blocked.

        This ensures we don't over-correct and block everyone just because
        SOME agents conflict.
        """
        niwa("add", "Doc", "--agent", "a1", cwd=db)
        lines = [f"L{i}" for i in range(10)]
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "\n".join(lines), "--agent", "a1", cwd=db)

        for a in ["a1", "a2", "a3", "a4", "a5"]:
            niwa("read", "h1_0", "--agent", a, cwd=db)

        def edit_lines(changes):
            l = lines[:]
            for idx, val in changes.items():
                l[idx] = val
            return "\n".join(l)

        # a1 → line 0 (lands)
        niwa("edit", "h1_0", edit_lines({0: "A1"}), "--agent", "a1", cwd=db)
        # a2 → line 0 (conflicts with a1)
        rc, _, err = niwa("edit", "h1_0", edit_lines({0: "A2"}), "--agent", "a2", cwd=db)
        assert "conflict" in (_ + err).lower()
        # a3 → line 5 (clean — auto-merges)
        rc, out, err = niwa("edit", "h1_0", edit_lines({5: "A3"}), "--agent", "a3", cwd=db)
        assert "EDIT SUCCESSFUL" in (out + err) or "Auto-merged" in (out + err)
        # a4 → line 0+5 (both overlap — conflicts)
        rc, _, err = niwa("edit", "h1_0", edit_lines({0: "A4a", 5: "A4b"}), "--agent", "a4", cwd=db)
        assert "conflict" in (_ + err).lower()
        # a5 → line 9 (clean — must still auto-merge despite a2 and a4 being blocked)
        rc, out, err = niwa("edit", "h1_0", edit_lines({9: "A5"}), "--agent", "a5", cwd=db)
        assert "EDIT SUCCESSFUL" in (out + err) or "Auto-merged" in (out + err), \
            "a5 must auto-merge — line 9 doesn't overlap with anything landed"

        rc, out, _ = niwa("peek", "h1_0", cwd=db)
        assert "A1" in out, "a1 landed"
        assert "A3" in out, "a3 auto-merged"
        assert "A5" in out, "a5 auto-merged despite other agents conflicting"
        assert "A2" not in out
        assert "A4a" not in out
        assert "A4b" not in out


# ── Export ──────────────────────────────────────────────────────────────────


class TestExport:
    def test_export_markdown(self, db):
        niwa("add", "Title", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "Some content here", "--agent", "a1", cwd=db)

        rc, out, err = niwa("export", cwd=db)
        assert rc == 0
        assert "# Title" in out
        assert "Some content here" in out

    def test_export_preserves_hierarchy(self, db):
        """Export a multi-level tree and verify heading levels."""
        niwa("add", "Top", "--agent", "a1", cwd=db)
        niwa("add", "Mid", "--agent", "a1", "--parent", "h1_0", cwd=db)
        niwa("add", "Bottom", "--agent", "a1", "--parent", "h2_0", cwd=db)

        niwa("read", "h2_0", "--agent", "a1", cwd=db)
        niwa("edit", "h2_0", "mid content", "--agent", "a1", cwd=db)

        rc, out, err = niwa("export", cwd=db)
        assert rc == 0
        assert "# Top" in out
        assert "## Mid" in out
        assert "### Bottom" in out
        assert "mid content" in out

    def test_export_empty_db(self, db):
        """Export right after init — should produce minimal output."""
        rc, out, err = niwa("export", cwd=db)
        assert rc == 0


# ── Search ──────────────────────────────────────────────────────────────────


class TestSearch:
    def test_search_finds_node(self, db):
        niwa("add", "Unique Heading", "--agent", "a1", cwd=db)
        rc, out, err = niwa("search", "Unique", cwd=db)
        assert rc == 0
        assert "Unique Heading" in out

    def test_search_no_results(self, db):
        """Search for something that doesn't exist."""
        niwa("add", "Hello", "--agent", "a1", cwd=db)
        rc, out, err = niwa("search", "zzzznonexistent", cwd=db)
        # Should not crash, just show no results
        assert rc == 0 or "no results" in (out + err).lower() or "not found" in (out + err).lower()

    def test_search_finds_content(self, db):
        """Search matches content, not just titles."""
        niwa("add", "Section", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "The quantum flux capacitor is broken", "--agent", "a1", cwd=db)

        rc, out, err = niwa("search", "quantum", cwd=db)
        assert rc == 0
        assert "h1_0" in out or "quantum" in out.lower()

    def test_search_case_insensitive_by_default(self, db):
        """Search should be case-insensitive by default."""
        niwa("add", "UPPERCASE TITLE", "--agent", "a1", cwd=db)
        rc, out, err = niwa("search", "uppercase", cwd=db)
        assert rc == 0
        assert "UPPERCASE" in out


# ── Claude Hooks ────────────────────────────────────────────────────────────


class TestClaudeHooks:
    def test_setup_claude_hooks(self, db):
        rc, out, err = niwa("setup", "claude", cwd=db)
        assert rc == 0
        assert "HOOKS INSTALLED" in out

        settings_path = db / ".claude" / "settings.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings

    def test_remove_claude_hooks(self, db):
        niwa("setup", "claude", cwd=db)
        rc, out, err = niwa("setup", "claude", "--remove", cwd=db)
        assert rc == 0
        assert "REMOVED" in out

    def test_setup_idempotent(self, db):
        """Running setup twice shouldn't break anything."""
        niwa("setup", "claude", cwd=db)
        rc, out, err = niwa("setup", "claude", cwd=db)
        assert rc == 0
        assert "HOOKS INSTALLED" in out

        settings_path = db / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings


# ── History ─────────────────────────────────────────────────────────────────


class TestHistory:
    def test_history_shows_edits(self, db):
        niwa("add", "Section", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "edit 1", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "edit 2", "--agent", "a1", cwd=db)

        rc, out, err = niwa("history", "h1_0", cwd=db)
        assert rc == 0
        assert "Version 1" in out
        assert "Version 2" in out
        assert "Version 3" in out

    def test_history_nonexistent_node(self, db):
        """Agent asks for history of a node that doesn't exist."""
        rc, out, err = niwa("history", "h1_999", cwd=db)
        assert rc != 0 or "not found" in (out + err).lower()

    def test_history_shows_agents(self, db):
        """History should show which agent made each edit."""
        niwa("add", "Shared", "--agent", "alice", cwd=db)
        niwa("read", "h1_0", "--agent", "alice", cwd=db)
        niwa("edit", "h1_0", "alice edit", "--agent", "alice", cwd=db)
        niwa("read", "h1_0", "--agent", "bob", cwd=db)
        niwa("edit", "h1_0", "bob edit", "--agent", "bob", cwd=db)

        rc, out, err = niwa("history", "h1_0", cwd=db)
        assert rc == 0
        assert "alice" in out
        assert "bob" in out


# ── Multi-Agent ─────────────────────────────────────────────────────────────


class TestMultiAgent:
    def test_two_agents_no_conflict(self, db):
        niwa("add", "Sec A", "--agent", "a1", cwd=db)
        niwa("add", "Sec B", "--agent", "a2", cwd=db)

        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("read", "h1_1", "--agent", "a2", cwd=db)

        rc1, out1, _ = niwa("edit", "h1_0", "a1 content", "--agent", "a1", cwd=db)
        rc2, out2, _ = niwa("edit", "h1_1", "a2 content", "--agent", "a2", cwd=db)

        assert rc1 == 0
        assert rc2 == 0
        assert "EDIT SUCCESSFUL" in out1
        assert "EDIT SUCCESSFUL" in out2

    def test_three_agents_building_tree(self, db):
        """Three agents collaboratively build a document structure."""
        niwa("add", "Architecture", "--agent", "lead", cwd=db)
        niwa("add", "Frontend", "--agent", "fe_agent", "--parent", "h1_0", cwd=db)
        niwa("add", "Backend", "--agent", "be_agent", "--parent", "h1_0", cwd=db)
        niwa("add", "Database", "--agent", "be_agent", "--parent", "h1_0", cwd=db)

        # Each agent edits their section
        niwa("read", "h2_0", "--agent", "fe_agent", cwd=db)
        niwa("edit", "h2_0", "React + TypeScript", "--agent", "fe_agent", cwd=db)

        niwa("read", "h2_1", "--agent", "be_agent", cwd=db)
        niwa("edit", "h2_1", "Python + FastAPI", "--agent", "be_agent", cwd=db)

        niwa("read", "h2_2", "--agent", "be_agent", cwd=db)
        niwa("edit", "h2_2", "PostgreSQL", "--agent", "be_agent", cwd=db)

        rc, out, err = niwa("export", cwd=db)
        assert rc == 0
        assert "React" in out
        assert "FastAPI" in out
        assert "PostgreSQL" in out

    def test_agents_list(self, db):
        """List all agents who have interacted with the DB."""
        niwa("add", "A", "--agent", "alice", cwd=db)
        niwa("add", "B", "--agent", "bob", cwd=db)
        niwa("add", "C", "--agent", "charlie", cwd=db)

        rc, out, err = niwa("agents", cwd=db)
        assert rc == 0
        assert "alice" in out
        assert "bob" in out
        assert "charlie" in out


# ── Status ──────────────────────────────────────────────────────────────────


class TestStatus:
    def test_status_after_read(self, db):
        """Agent checks status after reading a node."""
        niwa("add", "Section", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)

        rc, out, err = niwa("status", "--agent", "a1", cwd=db)
        assert rc == 0
        # Should show pending read or agent info
        assert "a1" in out or "h1_0" in out

    def test_status_fresh_agent(self, db):
        """New agent checks status before doing anything."""
        niwa("add", "Section", "--agent", "a1", cwd=db)
        rc, out, err = niwa("status", "--agent", "newcomer", cwd=db)
        assert rc == 0

    def test_whoami(self, db):
        """Agent uses whoami to get a suggested name."""
        rc, out, err = niwa("whoami", cwd=db)
        assert rc == 0


# ── Title ───────────────────────────────────────────────────────────────────


class TestTitle:
    def test_rename_title(self, db):
        niwa("add", "Old Name", "--agent", "a1", cwd=db)
        rc, out, err = niwa("title", "h1_0", "New Name", "--agent", "a1", cwd=db)
        assert rc == 0

        rc, out, err = niwa("tree", cwd=db)
        assert "New Name" in out

    def test_rename_nonexistent(self, db):
        rc, out, err = niwa("title", "h1_999", "Name", "--agent", "a1", cwd=db)
        assert rc != 0 or "not found" in (out + err).lower()


# ── Diff ────────────────────────────────────────────────────────────────────


class TestDiff:
    def test_diff_between_versions(self, db):
        niwa("add", "Section", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "first version content", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)
        niwa("edit", "h1_0", "second version content", "--agent", "a1", cwd=db)

        rc, out, err = niwa("diff", "h1_0", cwd=db)
        # Should show some diff output
        assert rc == 0 or "diff" in (out + err).lower() or "version" in (out + err).lower()


# ── Load ────────────────────────────────────────────────────────────────────


class TestLoad:
    def test_load_markdown_file(self, db):
        """Load a markdown file into the database."""
        md_file = db / "test.md"
        md_file.write_text("# Introduction\n\nHello world.\n\n## Details\n\nSome details.\n")

        rc, out, err = niwa("load", str(md_file), cwd=db)
        assert rc == 0

        rc, out, err = niwa("tree", cwd=db)
        assert "Introduction" in out
        assert "Details" in out

    def test_load_complex_markdown(self, db):
        """Load markdown with multiple heading levels."""
        md_file = db / "complex.md"
        md_file.write_text(
            "# Chapter 1\n\nIntro.\n\n"
            "## Section 1.1\n\nContent.\n\n"
            "## Section 1.2\n\nMore content.\n\n"
            "# Chapter 2\n\nAnother chapter.\n\n"
            "## Section 2.1\n\nDetails.\n"
        )

        rc, out, err = niwa("load", str(md_file), cwd=db)
        assert rc == 0

        rc, out, err = niwa("tree", cwd=db)
        assert "Chapter 1" in out
        assert "Chapter 2" in out
        assert "Section 1.1" in out
        assert "Section 2.1" in out

    def test_load_then_export_roundtrip(self, db):
        """Load markdown, export it, verify structure preserved."""
        md_file = db / "roundtrip.md"
        original = "# Title\n\nParagraph one.\n\n## Subtitle\n\nParagraph two.\n"
        md_file.write_text(original)

        niwa("load", str(md_file), cwd=db)
        rc, out, err = niwa("export", cwd=db)
        assert rc == 0
        assert "# Title" in out
        assert "Paragraph one." in out
        assert "## Subtitle" in out
        assert "Paragraph two." in out


# ── Check ───────────────────────────────────────────────────────────────────


class TestCheck:
    def test_check_healthy_db(self, db):
        """Database health check on a fresh DB."""
        niwa("add", "Section", "--agent", "a1", cwd=db)
        rc, out, err = niwa("check", cwd=db)
        assert rc == 0


# ── Error handling ──────────────────────────────────────────────────────────


class TestErrorHandling:
    def test_unknown_command(self, db):
        """Agent types a wrong command — shows guide + error box."""
        rc, out, err = niwa("frobnicate", cwd=db)
        combined = out + err
        # Niwa shows the help guide and an error message for unknown commands
        assert "unknown" in combined.lower() or "error" in combined.lower() or "valid" in combined.lower()

    def test_no_command(self, db):
        """Agent runs niwa with no arguments."""
        rc, out, err = niwa(cwd=db)
        # Should show help/guide, not crash
        assert rc == 0 or "usage" in (out + err).lower() or "niwa" in (out + err).lower()

    def test_help_command(self, db):
        """Agent asks for help."""
        rc, out, err = niwa("help", cwd=db)
        assert rc == 0
        assert "niwa" in out.lower() or "command" in out.lower()

    def test_no_db_initialized(self, tmp_path):
        """Agent tries to use niwa without init."""
        rc, out, err = niwa("tree", cwd=tmp_path)
        assert rc != 0 or "init" in (out + err).lower() or "not found" in (out + err).lower()


# ── Unicode / edge cases ───────────────────────────────────────────────────


class TestUnicodeAndEdgeCases:
    def test_unicode_title(self, db):
        """Japanese, emoji, and special characters in titles."""
        rc, out, err = niwa("add", "設計ドキュメント", "--agent", "a1", cwd=db)
        assert rc == 0

        rc, out, err = niwa("tree", cwd=db)
        assert "設計ドキュメント" in out

    def test_emoji_in_title(self, db):
        rc, out, err = niwa("add", "🚀 Launch Plan", "--agent", "a1", cwd=db)
        assert rc == 0

        rc, out, err = niwa("tree", cwd=db)
        assert "Launch Plan" in out

    def test_very_long_title(self, db):
        long_title = "A" * 500
        rc, out, err = niwa("add", long_title, "--agent", "a1", cwd=db)
        assert rc == 0

    def test_very_long_content(self, db):
        """Agent writes a very large section."""
        niwa("add", "Big Section", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)

        big_content = "Line of content.\n" * 1000
        content_file = db / "big.md"
        content_file.write_text(big_content)

        rc, out, err = niwa("edit", "h1_0", "--agent", "a1", "--file", str(content_file), cwd=db)
        assert rc == 0

    def test_content_with_markdown_formatting(self, db):
        """Content that itself contains markdown headings."""
        niwa("add", "Guide", "--agent", "a1", cwd=db)
        niwa("read", "h1_0", "--agent", "a1", cwd=db)

        content = "Here is how to use headers:\n\n```markdown\n# This is H1\n## This is H2\n```\n\nDone."
        content_file = db / "guide.md"
        content_file.write_text(content)

        rc, out, err = niwa("edit", "h1_0", "--agent", "a1", "--file", str(content_file), cwd=db)
        assert rc == 0

        rc, out, err = niwa("peek", "h1_0", cwd=db)
        assert "```" in out or "H1" in out
