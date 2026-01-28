"""
Niwa 庭 (Garden) - Collaborative Markdown Database for LLM Agents

A zen garden for your plans and specs. Multiple LLM agents can collaboratively
edit markdown documents with automatic conflict detection and resolution.

Uses LMDB for high-performance concurrent access.
Install: pip install niwa
"""

import json
import time
import difflib
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum

try:
    import lmdb
except ImportError:
    print("Please install lmdb: pip install lmdb")
    raise

try:
    from markdown_it import MarkdownIt
    from mdit_py_plugins.front_matter import front_matter_plugin
    from mdit_py_plugins.footnote import footnote_plugin
    from mdit_py_plugins.deflist import deflist_plugin
    from mdit_py_plugins.tasklists import tasklists_plugin
except ImportError:
    print("Please install markdown-it-py and mdit-py-plugins: pip install markdown-it-py mdit-py-plugins")
    raise

# =============================================================================
# CLAUDE CODE HOOK INTEGRATION
# =============================================================================

def generate_claude_hooks_config() -> dict:
    """Generate Claude Code hooks configuration for niwa integration."""
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"niwa hook --hook-event PreToolUse",
                            "timeout": 10
                        }
                    ]
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"niwa hook --hook-event PostToolUse",
                            "timeout": 10
                        }
                    ]
                }
            ],
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"niwa hook --hook-event Stop",
                            "timeout": 5
                        }
                    ]
                }
            ],
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"niwa hook --hook-event SessionStart",
                            "timeout": 5
                        }
                    ]
                }
            ],
            "PreCompact": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"niwa hook --hook-event PreCompact",
                            "timeout": 5
                        }
                    ]
                }
            ]
        }
    }


def get_niwa_usage_guide() -> str:
    """
    Generate a concise usage guide for Claude to remember after compaction.
    This is injected on SessionStart and PreCompact.
    """
    return """[Niwa 庭 - Collaborative Markdown Database]

This project uses Niwa for collaborative markdown editing with conflict detection.

QUICK REFERENCE (prefix all with 'niwa'):
  niwa tree                              # View document structure with node IDs
  niwa read <node_id> --agent <name>     # Read node for editing (tracks version)
  niwa edit <node_id> "content" --agent <name>  # Edit node
  niwa edit <node_id> --file <path> --agent <name>  # Edit from file (recommended)
  niwa peek <node_id>                    # Quick view without tracking
  niwa search <query>                    # Find content by keyword
  niwa status --agent <name>             # Check your pending reads/conflicts
  niwa conflicts --agent <name>          # List unresolved conflicts
  niwa resolve <node_id> <RESOLUTION> --agent <name>  # Resolve conflict
  niwa export                            # Export full markdown from database

CONFLICT RESOLUTIONS: ACCEPT_YOURS | ACCEPT_THEIRS | ACCEPT_AUTO_MERGE | MANUAL_MERGE "content"

WORKFLOW:
  1. Use `niwa tree` to find node IDs
  2. Use `niwa read` before editing (registers your base version)
  3. Use `niwa edit` with same --agent name
  4. If CONFLICT: use `niwa resolve` with one of the resolution options
  5. Use `niwa export` to get the final markdown

IMPORTANT:
  - Always use consistent --agent name (e.g., --agent claude_main)
  - Read before edit to enable conflict detection
  - Use --file for complex content with quotes/newlines"""


def handle_hook_event(event_name: str, hook_input: Optional[dict] = None) -> int:
    """
    Handle a Claude Code hook event.

    Returns exit code:
        0 = success (allow tool call)
        2 = block (with stderr message)
    """
    import sys

    # Read hook input from stdin if not provided
    if hook_input is None:
        try:
            hook_input = json.load(sys.stdin)
        except (json.JSONDecodeError, EOFError):
            hook_input = {}

    # Check if database exists
    db_exists = Path(".niwa/data.lmdb").exists()

    if event_name == "SessionStart":
        # On session start, provide full context about niwa + usage guide
        usage_guide = get_niwa_usage_guide()

        if db_exists:
            try:
                db = Niwa()
                health = db.get_db_health()
                db.close()

                status_info = (
                    f"\n\n[Niwa Status] Database: {health['node_count']} nodes, "
                    f"{health['pending_conflict_count']} pending conflicts, "
                    f"{health['pending_edit_count']} pending edits."
                )
                if health['pending_conflict_count'] > 0:
                    status_info += "\n⚠️  Run `niwa conflicts --agent <name>` to review conflicts."

                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": usage_guide + status_info
                    }
                }
                print(json.dumps(output))
            except Exception as e:
                # Still provide usage guide even if status check fails
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": usage_guide + f"\n\n[Niwa Status] Could not check: {e}"
                    }
                }
                print(json.dumps(output))
        else:
            # No database yet - still provide usage guide
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": usage_guide + "\n\n[Niwa Status] No database initialized. Run `niwa init` then `load <file.md>` to start."
                }
            }
            print(json.dumps(output))
        return 0

    elif event_name == "PreCompact":
        # Before compaction, inject usage guide so Claude remembers after context is pruned
        usage_guide = get_niwa_usage_guide()

        status_info = ""
        if db_exists:
            try:
                db = Niwa()
                health = db.get_db_health()
                db.close()

                status_info = (
                    f"\n\n[Niwa Status at Compaction] {health['node_count']} nodes, "
                    f"{health['pending_conflict_count']} conflicts, "
                    f"{health['pending_edit_count']} pending edits."
                )
                if health['pending_conflict_count'] > 0:
                    status_info += "\n⚠️  IMPORTANT: You have unresolved conflicts to handle after compaction!"
            except Exception:
                status_info = "\n\n[Niwa Status] Database exists but could not check status."

        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreCompact",
                "additionalContext": (
                    "[PRESERVING NIWA CONTEXT FOR POST-COMPACTION]\n\n"
                    + usage_guide
                    + status_info
                    + "\n\nAfter compaction, use `niwa tree` to see current structure."
                )
            }
        }
        print(json.dumps(output))
        return 0

    elif event_name == "PreToolUse":
        # Before Write/Edit, check if file is tracked and has conflicts
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})
        file_path = tool_input.get("file_path", "")

        if not db_exists or not file_path:
            return 0  # No database or no file path, allow

        # Check if this file is being tracked by niwa
        # For now, just provide context - don't block
        try:
            db = Niwa()
            health = db.get_db_health()

            if health['pending_conflict_count'] > 0:
                # Provide warning context
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": f"[Niwa Warning] There are {health['pending_conflict_count']} unresolved conflict(s) in the markdown database. "
                                           f"Run 'niwa conflicts --agent <name>' to review."
                    }
                }
                print(json.dumps(output))

            db.close()
        except Exception:
            pass  # Non-blocking

        return 0

    elif event_name == "PostToolUse":
        # After Write/Edit, could sync to database
        # For now just acknowledge
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})
        file_path = tool_input.get("file_path", "")

        if db_exists and file_path and file_path.endswith('.md'):
            # Provide hint about syncing
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": f"[Niwa] Markdown file modified: {file_path}. "
                                       f"Consider running 'niwa load {file_path}' to sync changes to database."
                }
            }
            print(json.dumps(output))

        return 0

    elif event_name == "Stop":
        # Before stopping, check for unresolved conflicts
        if not db_exists:
            return 0

        try:
            db = Niwa()
            health = db.get_db_health()
            db.close()

            if health['pending_conflict_count'] > 0:
                # Provide warning (but don't block)
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "Stop",
                        "additionalContext": f"[Niwa Reminder] There are {health['pending_conflict_count']} unresolved conflict(s). "
                                           f"Run 'niwa conflicts --agent <name>' before ending session."
                    }
                }
                print(json.dumps(output))
        except Exception:
            pass

        return 0

    return 0  # Unknown event, allow


def setup_claude_hooks(project_dir: str, remove: bool = False) -> Tuple[bool, str]:
    """
    Set up or remove Claude Code hooks configuration.

    Args:
        project_dir: Project directory path
        remove: If True, remove the hooks instead of adding them

    Returns:
        (success, message) tuple
    """
    claude_dir = Path(project_dir) / ".claude"
    settings_file = claude_dir / "settings.json"

    if remove:
        if not settings_file.exists():
            return True, "No Claude Code settings found - nothing to remove."

        try:
            with open(settings_file, 'r') as f:
                config = json.load(f)
        except json.JSONDecodeError:
            return False, f"Could not parse {settings_file}"

        if 'hooks' in config:
            # Check if these are our hooks (check for 'niwa' command or 'niwa.cli' module)
            hooks = config.get('hooks', {})
            our_hooks = False
            for event_hooks in hooks.values():
                for matcher_config in event_hooks:
                    for hook in matcher_config.get('hooks', []):
                        cmd = hook.get('command', '')
                        if 'niwa hook' in cmd or 'niwa.cli hook' in cmd:
                            our_hooks = True
                            break

            if our_hooks:
                del config['hooks']

                if config:  # Other settings exist
                    with open(settings_file, 'w') as f:
                        json.dump(config, f, indent=2)
                    return True, f"Removed niwa hooks from {settings_file}"
                else:  # No other settings, remove file
                    settings_file.unlink()
                    if not any(claude_dir.iterdir()):
                        claude_dir.rmdir()
                    return True, f"Removed {settings_file} (no other settings)"
            else:
                return True, "No niwa hooks found in settings - nothing to remove."
        else:
            return True, "No hooks in settings - nothing to remove."

    # Setup hooks
    hooks_config = generate_claude_hooks_config()

    # Create .claude directory if needed
    claude_dir.mkdir(exist_ok=True)

    # Merge with existing config if present
    if settings_file.exists():
        try:
            with open(settings_file, 'r') as f:
                existing = json.load(f)
        except json.JSONDecodeError:
            existing = {}

        # Check for existing hooks
        if 'hooks' in existing:
            # Merge our hooks with existing
            for event, event_hooks in hooks_config['hooks'].items():
                if event in existing['hooks']:
                    # Check if we already have niwa hooks for this event
                    existing_commands = []
                    for matcher_config in existing['hooks'][event]:
                        for hook in matcher_config.get('hooks', []):
                            existing_commands.append(hook.get('command', ''))

                    # Only add if not already present
                    for new_matcher_config in event_hooks:
                        for hook in new_matcher_config.get('hooks', []):
                            if hook.get('command', '') not in existing_commands:
                                existing['hooks'][event].append(new_matcher_config)
                else:
                    existing['hooks'][event] = event_hooks
        else:
            existing['hooks'] = hooks_config['hooks']

        config = existing
    else:
        config = hooks_config

    # Write config
    with open(settings_file, 'w') as f:
        json.dump(config, f, indent=2)

    return True, f"Created {settings_file} with niwa hooks"


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class ConflictType(Enum):
    NONE = "none"                           # No conflict
    COMPATIBLE = "compatible"               # Different parts edited - can auto-merge
    SEMANTIC_OVERLAP = "semantic_overlap"   # Same area, maybe compatible intent
    TRUE_CONFLICT = "true_conflict"         # Incompatible changes - LLM must decide


@dataclass
class Edit:
    """Represents an edit operation with full context."""
    node_id: str
    agent_id: str
    timestamp: float
    base_version: int
    base_content: str
    new_content: str
    edit_summary: Optional[str] = None  # Agent can describe their intent


@dataclass
class ConflictAnalysis:
    """Detailed analysis of a conflict for LLM resolution."""
    conflict_type: ConflictType
    node_id: str
    node_title: str

    # Version info
    your_base_version: int
    current_version: int
    concurrent_edits_count: int

    # Content
    original_content: str      # What you based your edit on
    your_content: str          # What you tried to write
    current_content: str       # What's there now

    # Diff analysis
    your_changes: List[Dict]   # What you changed from original
    their_changes: List[Dict]  # What they changed from original
    overlapping_regions: List[Dict]  # Where changes overlap

    # Agent info
    your_agent_id: str
    other_agents: List[str]    # Who else edited
    their_edit_summaries: List[str]  # Their stated intents

    # Auto-merge attempt (if compatible)
    auto_merge_possible: bool
    auto_merged_content: Optional[str] = None
    auto_merge_confidence: float = 0.0

    def to_llm_prompt(self) -> str:
        """Generate a structured prompt for LLM to resolve the conflict."""

        # Build diff visualization
        your_diff = self._format_diff(self.original_content, self.your_content, "YOUR CHANGES")
        their_diff = self._format_diff(self.original_content, self.current_content, "THEIR CHANGES")

        prompt = f"""## CONFLICT DETECTED - Resolution Required

### Context
- **Node**: `{self.node_id}` - "{self.node_title}"
- **Conflict Type**: {self.conflict_type.value}
- **Your base version**: {self.your_base_version}
- **Current version**: {self.current_version} ({self.concurrent_edits_count} edit(s) since you read)
- **Other editors**: {', '.join(self.other_agents) or 'Unknown'}

### Their Intent
{chr(10).join(f'- {s}' for s in self.their_edit_summaries) if self.their_edit_summaries else '- (No edit summary provided)'}

---

### ORIGINAL CONTENT (version {self.your_base_version})
```
{self.original_content}
```

---

### YOUR CHANGES
{your_diff}

**Your new content:**
```
{self.your_content}
```

---

### THEIR CHANGES (current version {self.current_version})
{their_diff}

**Current content:**
```
{self.current_content}
```

---

### OVERLAP ANALYSIS
{self._format_overlaps()}

---

"""

        if self.auto_merge_possible:
            prompt += f"""### AUTO-MERGE SUGGESTION (confidence: {self.auto_merge_confidence:.0%})
```
{self.auto_merged_content}
```

"""

        prompt += """### RESOLUTION OPTIONS

1. **ACCEPT_YOURS**: Overwrite with your version (discards their changes)
2. **ACCEPT_THEIRS**: Keep current version (discards your changes)
3. **ACCEPT_AUTO_MERGE**: Use the auto-merged version (if available)
4. **MANUAL_MERGE**: Provide your own merged content

### Your Response

Please respond with ONE of:
- `ACCEPT_YOURS` - if your changes should take precedence
- `ACCEPT_THEIRS` - if their changes should take precedence
- `ACCEPT_AUTO_MERGE` - if the auto-merge looks correct
- `MANUAL_MERGE` followed by your merged content in a code block

Consider:
- What was the intent of each edit?
- Are the changes complementary or contradictory?
- Which version is more complete/correct?
- Can both changes be meaningfully combined?
"""
        return prompt

    def _format_diff(self, old: str, new: str, label: str) -> str:
        """Format a unified diff."""
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)

        diff = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile='original', tofile=label,
            lineterm=''
        ))

        if not diff:
            return "(No changes)"

        # Color-code for readability
        formatted = []
        for line in diff:
            if line.startswith('+') and not line.startswith('+++'):
                formatted.append(f"+ {line[1:]}")  # Addition
            elif line.startswith('-') and not line.startswith('---'):
                formatted.append(f"- {line[1:]}")  # Deletion
            elif line.startswith('@@'):
                formatted.append(f"\n{line}")  # Hunk header
            else:
                formatted.append(f"  {line}")  # Context

        return "```diff\n" + "".join(formatted) + "\n```"

    def _format_overlaps(self) -> str:
        """Format overlapping regions."""
        if not self.overlapping_regions:
            return "No direct overlaps detected - changes are in different parts."

        output = []
        for i, region in enumerate(self.overlapping_regions):
            output.append(f"""
**Overlap {i+1}** (lines {region['start']}-{region['end']}):
- Original: `{region['original'][:100]}{'...' if len(region['original']) > 100 else ''}`
- Yours: `{region['yours'][:100]}{'...' if len(region['yours']) > 100 else ''}`
- Theirs: `{region['theirs'][:100]}{'...' if len(region['theirs']) > 100 else ''}`
""")
        return "\n".join(output)


@dataclass
class EditResult:
    """Result of an edit operation."""
    success: bool
    node_id: str
    new_version: Optional[int] = None
    message: str = ""
    conflict: Optional[ConflictAnalysis] = None

    def needs_resolution(self) -> bool:
        return self.conflict is not None and not self.success


# =============================================================================
# MAIN DATABASE CLASS
# =============================================================================

class Niwa:
    """
    Intelligent Markdown Database with semantic conflict detection.

    Uses LMDB for:
    - Fast concurrent reads (multiple readers)
    - Atomic writes (single writer, but very fast)
    - Memory-mapped I/O (blazing fast)
    - ACID transactions
    """

    def __init__(self, db_path: str = ".niwa"):
        self.db_path = Path(db_path)
        self.db_path.mkdir(exist_ok=True)

        # LMDB environment - 1GB max size, adjust as needed
        self.env = lmdb.open(
            str(self.db_path / "data.lmdb"),
            map_size=1024 * 1024 * 1024,  # 1GB
            max_dbs=5,
            writemap=True,
            metasync=False,  # Faster, slightly less durable
            sync=False,
        )

        # Sub-databases
        with self.env.begin(write=True) as txn:
            self.nodes_db = self.env.open_db(b'nodes', txn=txn)
            self.history_db = self.env.open_db(b'history', txn=txn)
            self.pending_db = self.env.open_db(b'pending', txn=txn)  # Pending edits
            self.meta_db = self.env.open_db(b'meta', txn=txn)

    def _serialize(self, obj: Any) -> bytes:
        return json.dumps(obj, default=str).encode('utf-8')

    def _deserialize(self, data: bytes) -> Any:
        return json.loads(data.decode('utf-8'))

    # =========================================================================
    # NODE OPERATIONS
    # =========================================================================

    def create_node(
        self,
        node_id: str,
        node_type: str,
        title: str = "",
        content: str = "",
        level: int = 0,
        parent_id: Optional[str] = None,
        agent_id: str = "system",
    ) -> bool:
        """Create a new node."""
        with self.env.begin(write=True) as txn:
            key = node_id.encode()

            if txn.get(key, db=self.nodes_db):
                return False  # Already exists

            node = {
                'id': node_id,
                'type': node_type,
                'title': title,
                'content': content,
                'level': level,
                'parent_id': parent_id,
                'children': [],
                'summary': None,
                'version': 1,
                'created_at': time.time(),
                'updated_at': time.time(),
                'last_agent': agent_id,
                'edit_history': [{
                    'version': 1,
                    'agent': agent_id,
                    'timestamp': time.time(),
                    'summary': 'Created',
                }],
            }

            txn.put(key, self._serialize(node), db=self.nodes_db)

            # Update parent's children list
            if parent_id:
                parent_key = parent_id.encode()
                parent_data = txn.get(parent_key, db=self.nodes_db)
                if parent_data:
                    parent = self._deserialize(parent_data)
                    if node_id not in parent['children']:
                        parent['children'].append(node_id)
                        txn.put(parent_key, self._serialize(parent), db=self.nodes_db)

            return True

    def read_node(self, node_id: str) -> Optional[Dict]:
        """Read a node (lock-free, concurrent safe)."""
        with self.env.begin() as txn:
            data = txn.get(node_id.encode(), db=self.nodes_db)
            return self._deserialize(data) if data else None

    def next_node_id(self, level: int) -> str:
        """Generate the next sequential node ID for a given level (e.g. h1_0, h1_1, h2_5)."""
        import re
        pattern = re.compile(rf'^h{level}_(\d+)$')
        max_idx = -1
        with self.env.begin() as txn:
            cursor = txn.cursor(db=self.nodes_db)
            for key, _ in cursor:
                m = pattern.match(key.decode())
                if m:
                    max_idx = max(max_idx, int(m.group(1)))
        return f"h{level}_{max_idx + 1}"

    def find_child_by_title(self, parent_id: str, title: str) -> Optional[Dict]:
        """Find a child node under parent_id with matching title (case-insensitive)."""
        parent = self.read_node(parent_id)
        if not parent:
            return None
        for child_id in parent.get('children', []):
            child = self.read_node(child_id)
            if child and child.get('title', '').lower() == title.lower():
                return child
        return None

    def read_for_edit(self, node_id: str, agent_id: str) -> Optional[Dict]:
        """
        Read a node with intent to edit.
        Returns node data + registers the agent's read version.
        Also clears any stored conflict for this agent+node (re-reading = fresh start).
        """
        with self.env.begin(write=True) as txn:
            data = txn.get(node_id.encode(), db=self.nodes_db)
            if not data:
                return None

            node = self._deserialize(data)

            # Record this agent's read in pending edits
            pending_key = f"{node_id}:{agent_id}".encode()
            pending = {
                'agent_id': agent_id,
                'node_id': node_id,
                'read_version': node['version'],
                'read_at': time.time(),
                'base_content': node['content'],
            }
            txn.put(pending_key, self._serialize(pending), db=self.pending_db)

            # Clear any stored conflict for this agent+node (re-reading = fresh start)
            conflict_key = f"conflicts:{agent_id}".encode()
            conflict_data = txn.get(conflict_key, db=self.meta_db)
            if conflict_data:
                conflicts = self._deserialize(conflict_data)
                conflicts = [c for c in conflicts if c.get('node_id') != node_id]
                if conflicts:
                    txn.put(conflict_key, self._serialize(conflicts), db=self.meta_db)
                else:
                    txn.delete(conflict_key, db=self.meta_db)

            return node

    def list_nodes(self) -> List[Dict]:
        """List all nodes."""
        nodes = []
        with self.env.begin() as txn:
            cursor = txn.cursor(db=self.nodes_db)
            for key, value in cursor:
                nodes.append(self._deserialize(value))
        return nodes

    # =========================================================================
    # INTELLIGENT EDIT WITH CONFLICT DETECTION
    # =========================================================================

    def edit_node(
        self,
        node_id: str,
        new_content: str,
        agent_id: str,
        edit_summary: Optional[str] = None,
        resolution_strategy: str = "prompt",  # prompt, auto, force
    ) -> EditResult:
        """
        Edit a node with intelligent conflict detection.

        Args:
            node_id: Node to edit
            new_content: New content
            agent_id: Who is editing
            edit_summary: Brief description of what this edit does (helps with conflict resolution)
            resolution_strategy:
                - "prompt": Return conflict for LLM resolution
                - "auto": Auto-merge if possible, else return conflict
                - "force": Overwrite regardless of conflicts
        """
        with self.env.begin(write=True) as txn:
            # Get current node state
            node_data = txn.get(node_id.encode(), db=self.nodes_db)
            if not node_data:
                return EditResult(
                    success=False,
                    node_id=node_id,
                    message=f"Node {node_id} not found"
                )

            node = self._deserialize(node_data)
            current_version = node['version']
            current_content = node['content']

            # Get agent's pending edit info (what version they read)
            pending_key = f"{node_id}:{agent_id}".encode()
            pending_data = txn.get(pending_key, db=self.pending_db)

            if not pending_data:
                # Agent didn't call read_for_edit - use current as base
                base_version = current_version
                base_content = current_content
            else:
                pending = self._deserialize(pending_data)
                base_version = pending['read_version']
                base_content = pending['base_content']
                # Clean up pending
                txn.delete(pending_key, db=self.pending_db)

            # Check for conflicts
            if base_version == current_version:
                # No conflict - apply edit directly
                return self._apply_edit(
                    txn, node, new_content, agent_id, edit_summary
                )

            # CONFLICT DETECTED - analyze it
            conflict = self._analyze_conflict(
                txn=txn,
                node=node,
                base_version=base_version,
                base_content=base_content,
                your_content=new_content,
                your_agent_id=agent_id,
            )

            # Handle based on strategy
            if resolution_strategy == "force":
                return self._apply_edit(
                    txn, node, new_content, agent_id,
                    f"{edit_summary} [FORCED - overwrote v{current_version}]"
                )

            elif resolution_strategy == "auto" and conflict.auto_merge_possible:
                return self._apply_edit(
                    txn, node, conflict.auto_merged_content, agent_id,
                    f"Auto-merged: {edit_summary}"
                )

            else:  # "prompt" or auto-merge not possible
                return EditResult(
                    success=False,
                    node_id=node_id,
                    message="Conflict detected - resolution required",
                    conflict=conflict,
                )

    def _apply_edit(
        self,
        txn,
        node: Dict,
        new_content: str,
        agent_id: str,
        edit_summary: Optional[str],
    ) -> EditResult:
        """Apply an edit to a node."""
        old_version = node['version']
        node['content'] = new_content
        node['version'] += 1
        node['updated_at'] = time.time()
        node['last_agent'] = agent_id

        # Add to edit history
        node['edit_history'].append({
            'version': node['version'],
            'agent': agent_id,
            'timestamp': time.time(),
            'summary': edit_summary,
            'prev_version': old_version,
        })

        # Keep last 20 history entries
        node['edit_history'] = node['edit_history'][-20:]

        # Save
        txn.put(node['id'].encode(), self._serialize(node), db=self.nodes_db)

        # Save full content to history for conflict resolution
        history_key = f"{node['id']}:v{node['version']}".encode()
        history_entry = {
            'content': new_content,
            'agent': agent_id,
            'timestamp': time.time(),
            'summary': edit_summary,
        }
        txn.put(history_key, self._serialize(history_entry), db=self.history_db)

        return EditResult(
            success=True,
            node_id=node['id'],
            new_version=node['version'],
            message=f"Edit applied. Version: {old_version} -> {node['version']}"
        )

    def _analyze_conflict(
        self,
        txn,
        node: Dict,
        base_version: int,
        base_content: str,
        your_content: str,
        your_agent_id: str,
    ) -> ConflictAnalysis:
        """Analyze a conflict in detail."""

        current_version = node['version']
        current_content = node['content']

        # Get info about edits that happened since base_version
        other_agents = []
        their_summaries = []
        for entry in node['edit_history']:
            if entry['version'] > base_version:
                if entry['agent'] != your_agent_id:
                    other_agents.append(entry['agent'])
                    if entry.get('summary'):
                        their_summaries.append(f"{entry['agent']}: {entry['summary']}")

        concurrent_count = current_version - base_version

        # Analyze the changes
        your_changes = self._extract_changes(base_content, your_content)
        their_changes = self._extract_changes(base_content, current_content)

        # Find overlapping regions
        overlaps = self._find_overlaps(your_changes, their_changes, base_content)

        # Determine conflict type and try auto-merge
        conflict_type, auto_merged, confidence = self._classify_and_merge(
            base_content, your_content, current_content,
            your_changes, their_changes, overlaps
        )

        return ConflictAnalysis(
            conflict_type=conflict_type,
            node_id=node['id'],
            node_title=node.get('title', '(untitled)'),
            your_base_version=base_version,
            current_version=current_version,
            concurrent_edits_count=concurrent_count,
            original_content=base_content,
            your_content=your_content,
            current_content=current_content,
            your_changes=your_changes,
            their_changes=their_changes,
            overlapping_regions=overlaps,
            your_agent_id=your_agent_id,
            other_agents=list(set(other_agents)),
            their_edit_summaries=their_summaries,
            auto_merge_possible=auto_merged is not None,
            auto_merged_content=auto_merged,
            auto_merge_confidence=confidence,
        )

    def _extract_changes(self, old: str, new: str) -> List[Dict]:
        """Extract structured change information."""
        changes = []

        old_lines = old.splitlines()
        new_lines = new.splitlines()

        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                continue

            changes.append({
                'type': tag,  # 'replace', 'insert', 'delete'
                'old_start': i1,
                'old_end': i2,
                'new_start': j1,
                'new_end': j2,
                'old_lines': old_lines[i1:i2],
                'new_lines': new_lines[j1:j2],
            })

        return changes

    def _find_overlaps(
        self,
        your_changes: List[Dict],
        their_changes: List[Dict],
        base_content: str,
    ) -> List[Dict]:
        """Find regions where both edits touch the same lines."""
        overlaps = []

        for yc in your_changes:
            for tc in their_changes:
                # Check if ranges overlap
                y_range = (yc['old_start'], yc['old_end'])
                t_range = (tc['old_start'], tc['old_end'])

                if self._ranges_overlap(y_range, t_range):
                    base_lines = base_content.splitlines()
                    start = min(yc['old_start'], tc['old_start'])
                    end = max(yc['old_end'], tc['old_end'])

                    overlaps.append({
                        'start': start,
                        'end': end,
                        'original': '\n'.join(base_lines[start:end]) if start < len(base_lines) else '',
                        'yours': '\n'.join(yc['new_lines']),
                        'theirs': '\n'.join(tc['new_lines']),
                        'your_change_type': yc['type'],
                        'their_change_type': tc['type'],
                    })

        return overlaps

    def _ranges_overlap(self, r1: Tuple[int, int], r2: Tuple[int, int]) -> bool:
        """Check if two line ranges overlap."""
        return r1[0] < r2[1] and r2[0] < r1[1]

    def _classify_and_merge(
        self,
        base: str,
        yours: str,
        theirs: str,
        your_changes: List[Dict],
        their_changes: List[Dict],
        overlaps: List[Dict],
    ) -> Tuple[ConflictType, Optional[str], float]:
        """
        Classify conflict type and attempt auto-merge.

        Returns: (conflict_type, auto_merged_content, confidence)
        """

        # No overlaps = compatible changes, can auto-merge
        if not overlaps:
            merged = self._three_way_merge(base, yours, theirs)
            return (ConflictType.COMPATIBLE, merged, 0.95)

        # Check if overlapping changes are identical (same edit by both)
        identical_overlaps = all(
            o['yours'].strip() == o['theirs'].strip()
            for o in overlaps
        )
        if identical_overlaps:
            return (ConflictType.COMPATIBLE, theirs, 1.0)

        # Check if one is subset of the other
        if yours.strip() in theirs.strip():
            return (ConflictType.SEMANTIC_OVERLAP, theirs, 0.8)
        if theirs.strip() in yours.strip():
            return (ConflictType.SEMANTIC_OVERLAP, yours, 0.8)

        # Try heuristic merge for simple cases
        merged = self._attempt_heuristic_merge(base, yours, theirs, overlaps)
        if merged:
            return (ConflictType.SEMANTIC_OVERLAP, merged, 0.6)

        # True conflict - cannot auto-resolve
        return (ConflictType.TRUE_CONFLICT, None, 0.0)

    def _three_way_merge(self, base: str, yours: str, theirs: str) -> str:
        """
        Perform three-way merge when changes don't overlap.
        """
        base_lines = base.splitlines(keepends=True)
        your_lines = yours.splitlines(keepends=True)
        their_lines = theirs.splitlines(keepends=True)

        # Start with base
        result = list(base_lines)

        # Get changes from both
        your_matcher = difflib.SequenceMatcher(None, base_lines, your_lines)
        their_matcher = difflib.SequenceMatcher(None, base_lines, their_lines)

        your_ops = [(op, i1, i2, j1, j2) for op, i1, i2, j1, j2 in your_matcher.get_opcodes() if op != 'equal']
        their_ops = [(op, i1, i2, j1, j2) for op, i1, i2, j1, j2 in their_matcher.get_opcodes() if op != 'equal']

        # Apply changes in reverse order to maintain indices
        all_ops = []
        for op, i1, i2, j1, j2 in your_ops:
            all_ops.append(('yours', op, i1, i2, your_lines[j1:j2]))
        for op, i1, i2, j1, j2 in their_ops:
            all_ops.append(('theirs', op, i1, i2, their_lines[j1:j2]))

        # Sort by position (descending) to apply from end
        all_ops.sort(key=lambda x: x[2], reverse=True)

        # Apply non-overlapping ops
        applied_ranges = []
        for source, op, i1, i2, new_lines in all_ops:
            # Check if this overlaps with already applied
            overlaps_applied = any(
                i1 < ar[1] and ar[0] < i2
                for ar in applied_ranges
            )
            if overlaps_applied:
                continue

            # Apply the change
            if op == 'replace' or op == 'delete':
                result[i1:i2] = new_lines
            elif op == 'insert':
                result[i1:i1] = new_lines

            applied_ranges.append((i1, i2))

        return ''.join(result)

    def _attempt_heuristic_merge(
        self,
        base: str,
        yours: str,
        theirs: str,
        overlaps: List[Dict],
    ) -> Optional[str]:
        """
        Try heuristic merges for common patterns.
        Returns merged content if successful, None if can't merge.
        """
        # Heuristic 1: Both are appending to the same location
        all_inserts = all(
            o['your_change_type'] == 'insert' and o['their_change_type'] == 'insert'
            for o in overlaps
        )
        if all_inserts:
            # Combine both insertions
            # This is a simplified heuristic - could be smarter
            return theirs + "\n" + yours.replace(base, "").strip()

        return None

    # =========================================================================
    # CONFLICT RESOLUTION
    # =========================================================================

    def resolve_conflict(
        self,
        node_id: str,
        resolution: str,
        agent_id: str,
        manual_content: Optional[str] = None,
        conflict: Optional[ConflictAnalysis] = None,
    ) -> EditResult:
        """
        Resolve a conflict with the chosen resolution.

        Args:
            node_id: Node with conflict
            resolution: One of "ACCEPT_YOURS", "ACCEPT_THEIRS", "ACCEPT_AUTO_MERGE", "MANUAL_MERGE"
            agent_id: Resolving agent
            manual_content: Required if resolution is "MANUAL_MERGE"
            conflict: The ConflictAnalysis object (for verification)
        """
        with self.env.begin(write=True) as txn:
            node_data = txn.get(node_id.encode(), db=self.nodes_db)
            if not node_data:
                return EditResult(success=False, node_id=node_id, message="Node not found")

            node = self._deserialize(node_data)

            # Verify we're resolving the right conflict
            if conflict and node['version'] != conflict.current_version:
                return EditResult(
                    success=False,
                    node_id=node_id,
                    message=f"Version changed during resolution. Expected {conflict.current_version}, got {node['version']}. Please retry."
                )

            # Determine final content
            if resolution == "ACCEPT_YOURS":
                if not conflict:
                    return EditResult(success=False, node_id=node_id, message="Need conflict object for ACCEPT_YOURS")
                final_content = conflict.your_content
                summary = f"Conflict resolved: accepted {agent_id}'s version"

            elif resolution == "ACCEPT_THEIRS":
                # No change needed - current content stays
                return EditResult(
                    success=True,
                    node_id=node_id,
                    new_version=node['version'],
                    message="Conflict resolved: kept current version"
                )

            elif resolution == "ACCEPT_AUTO_MERGE":
                if not conflict or not conflict.auto_merged_content:
                    return EditResult(success=False, node_id=node_id, message="No auto-merge available")
                final_content = conflict.auto_merged_content
                summary = f"Conflict resolved: auto-merged (confidence: {conflict.auto_merge_confidence:.0%})"

            elif resolution == "MANUAL_MERGE":
                if not manual_content:
                    return EditResult(success=False, node_id=node_id, message="Manual content required for MANUAL_MERGE")
                final_content = manual_content
                summary = f"Conflict resolved: manual merge by {agent_id}"

            else:
                return EditResult(success=False, node_id=node_id, message=f"Unknown resolution: {resolution}")

            # Apply the resolution
            return self._apply_edit(txn, node, final_content, agent_id, summary)

    # =========================================================================
    # TITLE AND SUMMARY UPDATES
    # =========================================================================

    def update_title(self, node_id: str, new_title: str, agent_id: str = "system") -> EditResult:
        """Update a node's title (no conflict detection - titles are simple)."""
        with self.env.begin(write=True) as txn:
            node_data = txn.get(node_id.encode(), db=self.nodes_db)
            if not node_data:
                return EditResult(success=False, node_id=node_id, message="Node not found")

            node = self._deserialize(node_data)
            node['title'] = new_title
            node['updated_at'] = time.time()
            txn.put(node_id.encode(), self._serialize(node), db=self.nodes_db)

            return EditResult(success=True, node_id=node_id, message="Title updated")

    def update_summary(self, node_id: str, summary: str, agent_id: str = "system") -> EditResult:
        """Update a node's summary."""
        with self.env.begin(write=True) as txn:
            node_data = txn.get(node_id.encode(), db=self.nodes_db)
            if not node_data:
                return EditResult(success=False, node_id=node_id, message="Node not found")

            node = self._deserialize(node_data)
            node['summary'] = summary
            node['updated_at'] = time.time()
            txn.put(node_id.encode(), self._serialize(node), db=self.nodes_db)

            return EditResult(success=True, node_id=node_id, message="Summary updated")

    # =========================================================================
    # MARKDOWN IMPORT/EXPORT
    # =========================================================================

    def load_markdown(self, md_path: str) -> str:
        """Parse markdown file into LMDB structure using markdown-it-py AST.

        Uses token line mapping to extract original source text, preserving
        exact formatting without any string reconstruction.
        """
        with open(md_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()

        # Normalize line endings and split into lines
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        lines = content.split('\n')

        # Initialize markdown-it parser with plugins for full markdown support
        # Using "gfm-like" preset for GitHub Flavored Markdown compatibility
        md = MarkdownIt("gfm-like")
        md.use(front_matter_plugin)  # Handle YAML/TOML frontmatter
        md.use(footnote_plugin)      # Handle [^1] footnotes
        md.use(deflist_plugin)       # Handle definition lists
        md.use(tasklists_plugin)     # Handle - [ ] task lists
        tokens = md.parse(content)

        # Create root node
        root_id = "root"
        self.create_node(root_id, "root", "Document", "", 0, None, "system")

        # Extract headings with their line positions from token.map
        # token.map = [start_line, end_line] (0-indexed, end exclusive)
        headings = []
        for token in tokens:
            if token.type == 'heading_open' and token.map:
                level = int(token.tag[1])
                # Find the inline token that follows (contains heading text)
                idx = tokens.index(token)
                title = ""
                if idx + 1 < len(tokens) and tokens[idx + 1].type == 'inline':
                    title = tokens[idx + 1].content

                headings.append({
                    'level': level,
                    'title': title,
                    'start_line': token.map[0],  # Line where heading starts
                    'end_line': token.map[1],    # Line after heading ends
                })

        if not headings:
            # No headings - single content node with all content
            node_id = "content_0"
            self.create_node(node_id, "paragraph", "Content", content.strip(), 0, root_id, "system")
            return root_id

        # Track parent stack by level
        parent_stack = [(0, root_id)]  # (level, node_id)

        for idx, heading in enumerate(headings):
            level = heading['level']
            title = heading['title']

            # Content starts after heading line, ends at next heading or EOF
            content_start_line = heading['end_line']
            if idx + 1 < len(headings):
                content_end_line = headings[idx + 1]['start_line']
            else:
                content_end_line = len(lines)

            # Extract original source text for content (preserves exact formatting)
            node_content = '\n'.join(lines[content_start_line:content_end_line]).strip()

            # Find parent (first item in stack with lower level)
            while parent_stack and parent_stack[-1][0] >= level:
                parent_stack.pop()

            parent_id = parent_stack[-1][1] if parent_stack else root_id

            # Create node
            node_id = f"h{level}_{idx}"
            self.create_node(node_id, "heading", title, node_content, level, parent_id, "system")

            # Push onto stack
            parent_stack.append((level, node_id))

        return root_id

    def export_markdown(self) -> str:
        """Export LMDB structure back to markdown."""
        output = []

        nodes = {n['id']: n for n in self.list_nodes()}

        def export_node(node_id: str):
            if node_id not in nodes:
                return
            node = nodes[node_id]

            if node['type'] == "heading":
                prefix = "#" * node['level']
                output.append(f"\n{prefix} {node['title']}\n")
                if node['content']:
                    output.append(node['content'])
                    output.append("")
            elif node['type'] == "paragraph":
                output.append(node['content'])
                output.append("")

            for child_id in node.get('children', []):
                export_node(child_id)

        # Find root
        root = None
        for node in nodes.values():
            if node['type'] == 'root':
                root = node
                break

        if root:
            for child_id in root.get('children', []):
                export_node(child_id)

        return "\n".join(output).strip() + "\n"

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def get_tree(self) -> str:
        """Get document tree structure."""
        output = ["# Document Structure", ""]

        nodes = {n['id']: n for n in self.list_nodes()}

        # Find roots
        roots = [n for n in nodes.values() if not n.get('parent_id')]

        def render(node_id: str, depth: int = 0):
            if node_id not in nodes:
                return
            node = nodes[node_id]
            indent = "  " * depth
            title = node.get('title', '(untitled)')[:40]
            version = node['version']
            agent = node.get('last_agent', '?')
            summary_flag = "[S]" if node.get('summary') else ""
            output.append(f"{indent}[{node_id}] v{version} \"{title}\" (by {agent}) {summary_flag}")

            for child_id in node.get('children', []):
                render(child_id, depth + 1)

        for root in roots:
            render(root['id'])

        return "\n".join(output)

    def peek(self, node_id: str) -> str:
        """Quick view of a node."""
        node = self.read_node(node_id)
        if not node:
            return f"Node {node_id} not found"

        output = [
            f"# Node: {node_id}",
            f"**Title**: {node.get('title', '(none)')}",
            f"**Type**: {node['type']}, **Level**: {node['level']}",
            f"**Version**: {node['version']} (by {node.get('last_agent', '?')})",
            f"**Summary**: {node.get('summary') or '(none)'}",
            "",
            "## Content Preview",
            "```",
            node['content'][:500] + ('...' if len(node['content']) > 500 else ''),
            "```",
            "",
            f"## Children ({len(node.get('children', []))})",
        ]

        for child_id in node.get('children', []):
            child = self.read_node(child_id)
            if child:
                output.append(f"  - [{child_id}] {child.get('title', '?')}")

        return "\n".join(output)

    def close(self):
        """Close the database."""
        self.env.close()

    # =========================================================================
    # AGENT STATUS & CONFLICT TRACKING (for sub-agents/fresh context)
    # =========================================================================

    def get_agent_status(self, agent_id: str) -> Dict:
        """
        Get comprehensive status for an agent.
        Critical for sub-agents with fresh context to understand their state.
        """
        status = {
            'agent_id': agent_id,
            'pending_reads': [],      # Nodes read but not yet edited
            'pending_conflicts': [],  # Unresolved conflicts
            'recent_edits': [],       # Recent successful edits by this agent
            'nodes_touched': set(),   # All nodes this agent has interacted with
        }

        with self.env.begin() as txn:
            # Check pending reads (read_for_edit but not yet edited)
            cursor = txn.cursor(db=self.pending_db)
            for key, value in cursor:
                key_str = key.decode()
                if f":{agent_id}" in key_str:
                    pending = self._deserialize(value)
                    node_id = key_str.split(':')[0]
                    # Check if node version changed since read
                    node_data = txn.get(node_id.encode(), db=self.nodes_db)
                    if node_data:
                        node = self._deserialize(node_data)
                        current_version = node['version']
                        read_version = pending['read_version']
                        status['pending_reads'].append({
                            'node_id': node_id,
                            'read_version': read_version,
                            'current_version': current_version,
                            'read_at': pending['read_at'],
                            'stale': current_version > read_version,
                            'stale_by': current_version - read_version,
                        })

            # Check for conflicts in meta db
            conflict_key = f"conflicts:{agent_id}".encode()
            conflict_data = txn.get(conflict_key, db=self.meta_db)
            if conflict_data:
                status['pending_conflicts'] = self._deserialize(conflict_data)

            # Get recent edits by this agent from history
            cursor = txn.cursor(db=self.nodes_db)
            for key, value in cursor:
                node = self._deserialize(value)
                for edit in node.get('edit_history', []):
                    if edit.get('agent') == agent_id:
                        status['nodes_touched'].add(node['id'])
                        if edit.get('timestamp', 0) > time.time() - 3600:  # Last hour
                            status['recent_edits'].append({
                                'node_id': node['id'],
                                'version': edit['version'],
                                'timestamp': edit['timestamp'],
                                'summary': edit.get('summary'),
                            })

        status['nodes_touched'] = list(status['nodes_touched'])
        status['recent_edits'].sort(key=lambda x: x['timestamp'], reverse=True)
        return status

    def store_conflict(self, agent_id: str, conflict: ConflictAnalysis):
        """Store a conflict for later resolution (survives context switches)."""
        with self.env.begin(write=True) as txn:
            conflict_key = f"conflicts:{agent_id}".encode()
            existing = txn.get(conflict_key, db=self.meta_db)
            conflicts = self._deserialize(existing) if existing else []

            # Add new conflict (replace if same node)
            conflicts = [c for c in conflicts if c.get('node_id') != conflict.node_id]
            conflicts.append({
                'node_id': conflict.node_id,
                'node_title': conflict.node_title,
                'your_base_version': conflict.your_base_version,
                'current_version': conflict.current_version,
                'your_content': conflict.your_content,
                'current_content': conflict.current_content,
                'auto_merge_possible': conflict.auto_merge_possible,
                'auto_merged_content': conflict.auto_merged_content,
                'stored_at': time.time(),
            })

            txn.put(conflict_key, self._serialize(conflicts), db=self.meta_db)

    def get_pending_conflicts(self, agent_id: str = None) -> List[Dict]:
        """Get all pending conflicts, optionally filtered by agent."""
        conflicts = []
        with self.env.begin() as txn:
            cursor = txn.cursor(db=self.meta_db)
            for key, value in cursor:
                key_str = key.decode()
                if key_str.startswith('conflicts:'):
                    agent = key_str.split(':', 1)[1]
                    if agent_id is None or agent == agent_id:
                        agent_conflicts = self._deserialize(value)
                        for c in agent_conflicts:
                            c['agent_id'] = agent
                            conflicts.append(c)
        return conflicts

    def clear_conflict(self, agent_id: str, node_id: str):
        """Clear a resolved conflict."""
        with self.env.begin(write=True) as txn:
            conflict_key = f"conflicts:{agent_id}".encode()
            existing = txn.get(conflict_key, db=self.meta_db)
            if existing:
                conflicts = self._deserialize(existing)
                conflicts = [c for c in conflicts if c.get('node_id') != node_id]
                if conflicts:
                    txn.put(conflict_key, self._serialize(conflicts), db=self.meta_db)
                else:
                    txn.delete(conflict_key, db=self.meta_db)

    def get_db_health(self) -> Dict:
        """
        Get database health status - useful for sub-agents to verify state.
        """
        health = {
            'initialized': False,
            'node_count': 0,
            'has_root': False,
            'pending_edit_count': 0,
            'pending_conflict_count': 0,
            'total_versions': 0,
            'active_agents': set(),
            'last_edit_time': None,
            'db_path': str(self.db_path),
        }

        try:
            with self.env.begin() as txn:
                # Count nodes
                cursor = txn.cursor(db=self.nodes_db)
                for key, value in cursor:
                    health['node_count'] += 1
                    node = self._deserialize(value)
                    health['total_versions'] += node.get('version', 1)

                    if node['id'] == 'root':
                        health['has_root'] = True

                    for edit in node.get('edit_history', []):
                        health['active_agents'].add(edit.get('agent', 'unknown'))
                        ts = edit.get('timestamp')
                        if ts and (health['last_edit_time'] is None or ts > health['last_edit_time']):
                            health['last_edit_time'] = ts

                # Count pending edits
                cursor = txn.cursor(db=self.pending_db)
                for _ in cursor:
                    health['pending_edit_count'] += 1

                # Count conflicts
                cursor = txn.cursor(db=self.meta_db)
                for key, value in cursor:
                    if key.decode().startswith('conflicts:'):
                        conflicts = self._deserialize(value)
                        health['pending_conflict_count'] += len(conflicts)

                health['initialized'] = health['node_count'] > 0

            health['active_agents'] = list(health['active_agents'])

        except Exception as e:
            health['error'] = str(e)

        return health

    def list_all_agents(self) -> List[Dict]:
        """List all agents that have interacted with this database."""
        agents = {}

        with self.env.begin() as txn:
            cursor = txn.cursor(db=self.nodes_db)
            for key, value in cursor:
                node = self._deserialize(value)
                for edit in node.get('edit_history', []):
                    agent = edit.get('agent', 'unknown')
                    if agent not in agents:
                        agents[agent] = {
                            'agent_id': agent,
                            'edit_count': 0,
                            'nodes_edited': set(),
                            'first_seen': edit.get('timestamp'),
                            'last_seen': edit.get('timestamp'),
                        }
                    agents[agent]['edit_count'] += 1
                    agents[agent]['nodes_edited'].add(node['id'])
                    ts = edit.get('timestamp')
                    if ts:
                        if ts < agents[agent]['first_seen']:
                            agents[agent]['first_seen'] = ts
                        if ts > agents[agent]['last_seen']:
                            agents[agent]['last_seen'] = ts

        # Convert sets to lists for serialization
        for agent in agents.values():
            agent['nodes_edited'] = list(agent['nodes_edited'])

        return list(agents.values())

    def suggest_agent_name(self) -> str:
        """Suggest a unique agent name for a new sub-agent."""
        import uuid
        existing_agents = {a['agent_id'] for a in self.list_all_agents()}

        # Try simple names first
        for i in range(1, 100):
            name = f"agent_{i}"
            if name not in existing_agents:
                return name

        # Fallback to UUID-based
        return f"agent_{uuid.uuid4().hex[:8]}"

    # =========================================================================
    # ADDITIONAL EDGE CASE HANDLING
    # =========================================================================

    def validate_agent_name(self, agent_id: str) -> Tuple[bool, str]:
        """Validate agent name - no special chars that could cause issues."""
        if not agent_id:
            return False, "Agent name cannot be empty"
        if len(agent_id) > 50:
            return False, "Agent name too long (max 50 chars)"
        if agent_id == "default_agent":
            return False, "Please specify a unique agent name with --agent"
        # Allow alphanumeric, underscore, hyphen
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', agent_id):
            return False, "Agent name can only contain letters, numbers, underscore, hyphen"
        return True, "OK"

    def search_content(self, query: str, case_sensitive: bool = False) -> List[Dict]:
        """Search for content across all nodes."""
        results = []
        if not case_sensitive:
            query = query.lower()

        with self.env.begin() as txn:
            cursor = txn.cursor(db=self.nodes_db)
            for key, value in cursor:
                node = self._deserialize(value)
                content = node.get('content', '')
                title = node.get('title', '')

                search_content = content if case_sensitive else content.lower()
                search_title = title if case_sensitive else title.lower()

                if query in search_content or query in search_title:
                    # Find line numbers with matches
                    lines = content.split('\n')
                    matching_lines = []
                    for i, line in enumerate(lines):
                        search_line = line if case_sensitive else line.lower()
                        if query in search_line:
                            matching_lines.append((i + 1, line[:100]))

                    results.append({
                        'node_id': node['id'],
                        'title': title,
                        'version': node['version'],
                        'match_in_title': query in search_title,
                        'matching_lines': matching_lines[:5],  # Limit to 5 matches per node
                        'total_matches': len(matching_lines),
                    })

        return results

    def get_node_history(self, node_id: str) -> List[Dict]:
        """Get version history for a node."""
        history = []
        with self.env.begin() as txn:
            node_data = txn.get(node_id.encode(), db=self.nodes_db)
            if not node_data:
                return []

            node = self._deserialize(node_data)

            # Get edit history from node
            for entry in node.get('edit_history', []):
                history.append({
                    'version': entry.get('version'),
                    'agent': entry.get('agent'),
                    'timestamp': entry.get('timestamp'),
                    'summary': entry.get('summary'),
                })

            # Also check history DB for full content
            for entry in history:
                version = entry.get('version')
                history_key = f"{node_id}:v{version}".encode()
                history_data = txn.get(history_key, db=self.history_db)
                if history_data:
                    h = self._deserialize(history_data)
                    entry['has_content'] = True
                    entry['content_preview'] = h.get('content', '')[:200]
                else:
                    entry['has_content'] = False

        return sorted(history, key=lambda x: x.get('version', 0), reverse=True)

    def get_version_content(self, node_id: str, version: int) -> Optional[str]:
        """Get content for a specific version (for rollback)."""
        with self.env.begin() as txn:
            history_key = f"{node_id}:v{version}".encode()
            history_data = txn.get(history_key, db=self.history_db)
            if history_data:
                h = self._deserialize(history_data)
                return h.get('content')
            return None

    def dry_run_edit(
        self,
        node_id: str,
        new_content: str,
        agent_id: str,
    ) -> Dict:
        """
        Simulate an edit without applying it.
        Returns what would happen: success, conflict details, or error.
        """
        with self.env.begin() as txn:  # Read-only transaction
            node_data = txn.get(node_id.encode(), db=self.nodes_db)
            if not node_data:
                return {
                    'would_succeed': False,
                    'reason': 'node_not_found',
                    'message': f"Node {node_id} not found"
                }

            node = self._deserialize(node_data)
            current_version = node['version']
            current_content = node['content']

            # Check for pending read
            pending_key = f"{node_id}:{agent_id}".encode()
            pending_data = txn.get(pending_key, db=self.pending_db)

            if not pending_data:
                return {
                    'would_succeed': True,
                    'reason': 'no_prior_read',
                    'message': f"Would edit from current v{current_version} (no prior read registered)",
                    'current_version': current_version,
                    'new_version': current_version + 1,
                    'content_changed': current_content != new_content,
                }

            pending = self._deserialize(pending_data)
            base_version = pending['read_version']

            if base_version == current_version:
                return {
                    'would_succeed': True,
                    'reason': 'no_conflict',
                    'message': f"Would edit v{current_version} -> v{current_version + 1}",
                    'current_version': current_version,
                    'new_version': current_version + 1,
                    'content_changed': current_content != new_content,
                }
            else:
                return {
                    'would_succeed': False,
                    'reason': 'conflict',
                    'message': f"CONFLICT: You read v{base_version}, current is v{current_version}",
                    'your_base_version': base_version,
                    'current_version': current_version,
                    'versions_behind': current_version - base_version,
                }

    def cleanup_stale_reads(self, max_age_seconds: int = 3600) -> int:
        """
        Clean up pending reads older than max_age_seconds.
        Returns number of cleaned up entries.
        """
        cleaned = 0
        cutoff = time.time() - max_age_seconds

        with self.env.begin(write=True) as txn:
            cursor = txn.cursor(db=self.pending_db)
            keys_to_delete = []

            for key, value in cursor:
                pending = self._deserialize(value)
                if pending.get('read_at', 0) < cutoff:
                    keys_to_delete.append(key)

            for key in keys_to_delete:
                txn.delete(key, db=self.pending_db)
                cleaned += 1

        return cleaned

    def cleanup_stale_conflicts(self, max_age_seconds: int = 86400) -> int:
        """
        Clean up stored conflicts older than max_age_seconds (default 24h).
        Returns number of cleaned up conflicts.
        """
        cleaned = 0
        cutoff = time.time() - max_age_seconds

        with self.env.begin(write=True) as txn:
            cursor = txn.cursor(db=self.meta_db)
            updates = []

            for key, value in cursor:
                key_str = key.decode()
                if key_str.startswith('conflicts:'):
                    conflicts = self._deserialize(value)
                    new_conflicts = [c for c in conflicts if c.get('stored_at', 0) >= cutoff]
                    removed = len(conflicts) - len(new_conflicts)
                    if removed > 0:
                        cleaned += removed
                        updates.append((key, new_conflicts))

            for key, new_conflicts in updates:
                if new_conflicts:
                    txn.put(key, self._serialize(new_conflicts), db=self.meta_db)
                else:
                    txn.delete(key, db=self.meta_db)

        return cleaned


# =============================================================================
# CLI INTERFACE WITH LLM-FRIENDLY PROMPTS
# =============================================================================

LLM_SYSTEM_PROMPT = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                 NIWA 庭 (GARDEN) - LLM AGENT INSTRUCTION GUIDE               ║
╚══════════════════════════════════════════════════════════════════════════════╝

You are using Niwa to edit markdown documents with CONFLICT-AWARE
concurrent editing. This tool tracks versions and detects when multiple agents
try to edit the same section simultaneously.

## CRITICAL WORKFLOW - ALWAYS FOLLOW THIS PATTERN:

┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 1: READ BEFORE EDIT (registers your read version)                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ niwa read <node_id> --agent <your_agent_name>               │
│                                                                             │
│ This outputs the current version number. REMEMBER IT!                       │
│ The system tracks that you read version N.                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 2: EDIT WITH YOUR CHANGES                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│ niwa edit <node_id> "<new_content>" \\                       │
│     --agent <your_agent_name> \\                                             │
│     --summary "Brief description of what you changed"                       │
│                                                                             │
│ If no one else edited since you read → SUCCESS                              │
│ If someone else edited → CONFLICT (you'll get a resolution prompt)          │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                           ┌────────┴────────┐
                           │                 │
                     (no conflict)     (conflict!)
                           │                 │
                           ▼                 ▼
┌──────────────────────────────┐  ┌──────────────────────────────────────────┐
│ SUCCESS: Version N → N+1     │  │ CONFLICT DETECTED!                       │
│ You're done.                 │  │ - Shows what they changed                │
└──────────────────────────────┘  │ - Shows what you changed                 │
                                  │ - Suggests auto-merge if possible        │
                                  │ - Asks you to resolve                    │
                                  └──────────────────────────────────────────┘
                                                 │
                                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 3 (only if conflict): RESOLVE THE CONFLICT                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ Choose ONE of these resolutions:                                            │
│                                                                             │
│ • ACCEPT_YOURS - Overwrite with your version (discards their changes)       │
│   niwa resolve <node_id> ACCEPT_YOURS --agent <you>         │
│                                                                             │
│ • ACCEPT_THEIRS - Keep their version (discards your changes)                │
│   niwa resolve <node_id> ACCEPT_THEIRS --agent <you>        │
│                                                                             │
│ • ACCEPT_AUTO_MERGE - Use the system's suggested merge (if available)       │
│   niwa resolve <node_id> ACCEPT_AUTO_MERGE --agent <you>    │
│                                                                             │
│ • MANUAL_MERGE - Provide your own carefully merged content                  │
│   niwa resolve <node_id> MANUAL_MERGE "<merged>" --agent me │
│                                                                             │
│ MANUAL_MERGE TIP: Combine BOTH changes intelligently. Don't lose work!      │
└─────────────────────────────────────────────────────────────────────────────┘

## ALL AVAILABLE COMMANDS:

╔═══════════════╦══════════════════════════════════════════════════════════════╗
║ COMMAND       ║ PURPOSE                                                      ║
╠═══════════════╬══════════════════════════════════════════════════════════════╣
║ SETUP:        ║                                                              ║
║ init          ║ Initialize a new database (run once)                         ║
║ add <title>   ║ Add a node directly (preferred way to build the tree)        ║
║ load <file>   ║ ⚠️ Load markdown file (ONLY if user explicitly requests it!)  ║
║ check         ║ Verify database health and state                             ║
╠═══════════════╬══════════════════════════════════════════════════════════════╣
║ BROWSE:       ║                                                              ║
║ tree          ║ Show document structure with all node IDs                    ║
║ peek <id>     ║ Quick view of a node (doesn't track read version)            ║
╠═══════════════╬══════════════════════════════════════════════════════════════╣
║ EDIT:         ║                                                              ║
║ read <id>     ║ Read for editing (TRACKS your read version for conflicts)    ║
║ edit <id> ... ║ Edit a node (use --file for complex content!)                ║
║ resolve ...   ║ Resolve a conflict after edit fails                          ║
║ title <id>    ║ Update a node's title                                        ║
║ summarize     ║ Add a summary to a node                                      ║
╠═══════════════╬══════════════════════════════════════════════════════════════╣
║ AGENT STATUS: ║ ⚠️  CRITICAL FOR SUB-AGENTS WITH FRESH CONTEXT!               ║
║ status        ║ Check your pending reads, conflicts, recent edits            ║
║ conflicts     ║ List all unresolved conflicts                                ║
║ agents        ║ List all agents who've used this database                    ║
║ whoami        ║ Quick state check + get suggested unique agent name          ║
╠═══════════════╬══════════════════════════════════════════════════════════════╣
║ SEARCH/UNDO:  ║                                                              ║
║ search        ║ Find content by keyword (when you don't know node ID)        ║
║ history       ║ View version history for a node                              ║
║ rollback      ║ Restore a node to a previous version                         ║
║ dry-run       ║ Preview edit without applying (test for conflicts)           ║
║ cleanup       ║ Remove stale pending reads and old conflicts                 ║
╠═══════════════╬══════════════════════════════════════════════════════════════╣
║ OUTPUT:       ║                                                              ║
║ export        ║ Export document back to markdown format                      ║
║ help          ║ Show this guide                                              ║
╚═══════════════╩══════════════════════════════════════════════════════════════╝

## FINDING NODE IDs:

Run `niwa tree` to see all nodes:

```
[root] v1 "Document" (by system)
  [h1_0] v1 "Main Title" (by system)
    [h2_1] v3 "Section 1" (by agent_A)      ← node_id is "h2_1", version is 3
    [h2_2] v1 "Section 2" (by system)
      [h3_3] v2 "Subsection 2.1" (by agent_B)
```

Node IDs follow the pattern: h{level}_{index}
- h1_0 = first level-1 heading
- h2_5 = sixth level-2 heading
- etc.

## EXAMPLE COMPLETE WORKFLOW:

```bash
# 1. Initialize (only once)
niwa init

# 2. Build the tree by adding nodes
niwa add "Requirements" --agent claude_1
# Output: NODE_ID: h1_0
niwa add "Auth Flow" --parent h1_0 --agent claude_1
# Output: NODE_ID: h2_0

# 3. See the structure
niwa tree

# 4. Read a section (as agent "claude_1")
niwa read h2_0 --agent claude_1
# Output: Version: 1 ... content here ...

# 5. Edit with your changes
niwa edit h2_0 "My new content for this section" \\
    --agent claude_1 \\
    --summary "Added implementation details"

# 6. If SUCCESS → done!
# If CONFLICT → read the diff, then resolve:
niwa resolve h2_0 MANUAL_MERGE "Combined content here" --agent claude_1

# 7. Export when done
niwa export > updated_document.md
```

## MULTI-AGENT PARALLEL EDITING:

This tool is DESIGNED for multiple LLM agents working simultaneously:

```
Agent A: reads section 1 (sees v1)     Agent B: reads section 1 (sees v1)
    │                                      │
    ▼                                      ▼
Agent A: edits section 1               Agent B: edits section 1
    │                                      │
    ▼                                      ▼
SUCCESS (v1→v2)                        CONFLICT! (expected v1, found v2)
                                           │
                                           ▼
                                       Agent B sees diff, resolves with MANUAL_MERGE
                                           │
                                           ▼
                                       SUCCESS (v2→v3) - both changes preserved!
```

## ⚠️  FOR SUB-AGENTS / NEW CONTEXT - READ THIS FIRST!

If you are a SUB-AGENT spawned by another agent, or if you have FRESH CONTEXT
and don't remember previous interactions, DO THIS IMMEDIATELY:

```bash
# 1. Check if DB exists and get your suggested unique name
niwa whoami

# 2. If you have a name, check your state
niwa status --agent <your_name>

# 3. Check for any pending conflicts you need to resolve
niwa conflicts --agent <your_name>
```

KEY RULES FOR SUB-AGENTS:
- Use a UNIQUE agent name (run `whoami` to get a suggested one)
- Use the SAME name consistently for ALL your reads/edits
- Check `status` first if you have fresh context
- Stored conflicts survive context switches - check for them!

## COMMON MISTAKES AND HOW TO FIX THEM:

╔════════════════════════════════════╦═════════════════════════════════════════╗
║ MISTAKE                            ║ FIX                                     ║
╠════════════════════════════════════╬═════════════════════════════════════════╣
║ Using `load` without being asked   ║ Use `add` to build the tree. NEVER use  ║
║                                    ║ `load` unless the user explicitly asks!  ║
║ Editing without reading first      ║ Always `read` before `edit`             ║
║ Forgetting --agent flag            ║ Add --agent <unique_name>               ║
║ Using same name as another agent   ║ Run `agents` to see used names          ║
║ Losing other agent's changes       ║ Use MANUAL_MERGE, combine both          ║
║ Wrong node_id                      ║ Run `tree` to find correct ID           ║
║ Node not found                     ║ Check `tree`, IDs are case-sensitive    ║
║ Content has quotes/newlines        ║ Use --file flag to read from file!      ║
║ Fresh context, don't know state    ║ Run `status --agent <name>` first       ║
║ Don't know if conflicts pending    ║ Run `conflicts --agent <name>`          ║
╚════════════════════════════════════╩═════════════════════════════════════════╝

## CONFLICT RESOLUTION DECISION GUIDE:

When you see a CONFLICT, ask yourself:

1. "Are our changes in DIFFERENT parts of the content?"
   → YES: Use MANUAL_MERGE to combine both
   → The auto-merge might work, check ACCEPT_AUTO_MERGE suggestion

2. "Did we both change the SAME lines?"
   → Read both versions carefully
   → Decide which is better, or combine the best of both
   → Usually MANUAL_MERGE is safest

3. "Is their change more important/correct than mine?"
   → YES: Use ACCEPT_THEIRS
   → NO: Use ACCEPT_YOURS (but you'll lose their work!)

4. "Can I combine both changes meaningfully?"
   → YES: Use MANUAL_MERGE with combined content
   → This is almost always the best choice!

Remember: MANUAL_MERGE preserves everyone's work. When in doubt, merge manually!
"""

COMMAND_HELP = {
    'init': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: init                                                                ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Initialize a new markdown database                                  ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa init                                                  ║
║                                                                              ║
║ WHAT IT DOES:                                                                ║
║   - Creates .niwa/ directory                                                 ║
║   - Initializes LMDB database                                                ║
║   - Creates root document node                                               ║
║                                                                              ║
║ NEXT STEP:                                                                   ║
║   niwa add "Section Title" --agent <name>                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'load': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: load                                                                ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ ⚠️ DO NOT USE unless the user explicitly asks you to load a file!             ║
║ Use `niwa add` to build the tree incrementally instead.                     ║
║                                                                              ║
║ PURPOSE: Load a markdown file into the database                              ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa load <file.md>                                        ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa load main_plan.md                                     ║
║   niwa load /path/to/document.md                             ║
║                                                                              ║
║ WHAT IT DOES:                                                                ║
║   - Parses markdown headings into a tree structure                           ║
║   - Each heading becomes a node with: id, title, content, version            ║
║   - Shows the resulting tree structure                                       ║
║                                                                              ║
║ NEXT STEP:                                                                   ║
║   niwa tree    # See the structure                           ║
║   niwa read <node_id> --agent <name>   # Read a section      ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'add': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: add                                                                 ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Add a new node directly without loading a markdown file            ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa add <title> [content]                                                ║
║                                                                              ║
║ OPTIONS:                                                                     ║
║   --parent <id>   Parent node ID (default: root)                            ║
║   --file <path>   Read content from file                                    ║
║   --stdin          Read content from stdin                                   ║
║   --agent <name>  Agent performing the add                                  ║
║                                                                              ║
║ EXAMPLES:                                                                    ║
║   niwa add "Requirements"                           # Under root            ║
║   niwa add "Auth Flow" --parent h1_0                # Under h1_0            ║
║   niwa add "Design" "Some body text"                # With content          ║
║   niwa add "Spec" --file spec_content.md            # From file             ║
║   echo "piped" | niwa add "Notes" --stdin           # From stdin            ║
║                                                                              ║
║ CONFLICT DETECTION:                                                          ║
║   If a node with the same title already exists under the same parent,       ║
║   the command blocks and shows the existing node. Edit it instead.          ║
║   Output includes NODE_ID: <id> for machine parsing.                        ║
║                                                                              ║
║ NOTES:                                                                       ║
║   - Node ID is auto-generated (e.g. h1_a3f2)                               ║
║   - Level is inferred from parent (parent level + 1)                        ║
║   - Node type is always 'heading'                                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'tree': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: tree                                                                ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Display document structure with all node IDs                        ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa tree                                                  ║
║                                                                              ║
║ OUTPUT FORMAT:                                                               ║
║   [node_id] vN "Title" (by agent_name)                                       ║
║                                                                              ║
║   - node_id: Use this for read/edit/peek commands                            ║
║   - vN: Current version number                                               ║
║   - Title: First 40 chars of heading                                         ║
║   - by agent_name: Who last edited this node                                 ║
║                                                                              ║
║ EXAMPLE OUTPUT:                                                              ║
║   [root] v1 "Document" (by system)                                           ║
║     [h1_0] v1 "Introduction" (by system)                                     ║
║     [h2_1] v3 "Chapter 1" (by agent_A)     ← edited 3 times                  ║
║       [h3_2] v1 "Section 1.1" (by system)                                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'peek': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: peek                                                                ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Quick view of a node (does NOT track read version)                  ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa peek <node_id>                                        ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa peek h2_5                                             ║
║                                                                              ║
║ ⚠️  WARNING:                                                                  ║
║   This does NOT register your read for conflict detection!                   ║
║   Use `read` instead if you plan to edit.                                    ║
║                                                                              ║
║ USE peek FOR:                                                                ║
║   - Browsing without intent to edit                                          ║
║   - Checking node metadata (title, children, etc.)                           ║
║   - Quick content preview                                                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'read': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: read                                                                ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Read a node FOR EDITING (tracks your read version)                  ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa read <node_id> --agent <your_agent_name>              ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa read h2_3 --agent claude_researcher                   ║
║                                                                              ║
║ ⚠️  IMPORTANT:                                                                ║
║   - ALWAYS use --agent with a unique name for your agent                     ║
║   - The system records that you saw version N                                ║
║   - When you edit, if version changed → CONFLICT detected                    ║
║                                                                              ║
║ OUTPUT:                                                                      ║
║   # Node: h2_3                                                               ║
║   Version: 2 (REMEMBER THIS FOR EDIT)    ← System tracks this!               ║
║   Title: My Section Title                                                    ║
║   Last edited by: agent_A                                                    ║
║   ---                                                                        ║
║   [content here]                                                             ║
║                                                                              ║
║ NEXT STEP:                                                                   ║
║   niwa edit h2_3 "<new content>" --agent claude_researcher   ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'edit': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: edit                                                                ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Edit a node's content (with conflict detection)                     ║
║                                                                              ║
║ USAGE (3 ways to provide content):                                           ║
║                                                                              ║
║ 1. INLINE (simple content):                                                  ║
║    niwa edit <node_id> "<content>" --agent <name>            ║
║                                                                              ║
║ 2. FROM FILE (recommended for complex content!):                             ║
║    niwa edit <node_id> --file <path> --agent <name>          ║
║                                                                              ║
║ 3. FROM STDIN (for piping):                                                  ║
║    cat content.txt | niwa edit <node_id> --stdin --agent me  ║
║                                                                              ║
║ EXAMPLES:                                                                    ║
║   # Simple inline edit                                                       ║
║   niwa edit h2_3 "New content" --agent claude_1              ║
║                                                                              ║
║   # From file (avoids shell escaping issues!)                                ║
║   niwa edit h2_3 --file /tmp/content.txt --agent claude_1    ║
║                                                                              ║
║   # With edit summary                                                        ║
║   niwa edit h2_3 --file content.md --agent me --summary "x"  ║
║                                                                              ║
║ ⚠️  PREREQUISITES:                                                            ║
║   - Run `read` first to register your base version                           ║
║   - Use the SAME --agent name as your read command                           ║
║                                                                              ║
║ OPTIONS:                                                                     ║
║   --file <path>     Read content from file (avoids escaping!)                ║
║   --stdin           Read content from stdin (for piping)                     ║
║   --summary "..."   Brief description (helps with conflict resolution)       ║
║   --strategy prompt (default) Return conflict for you to resolve             ║
║   --strategy auto   Auto-merge if possible, else return conflict             ║
║   --strategy force  Overwrite regardless of conflicts (DANGEROUS!)           ║
║                                                                              ║
║ OUTCOMES:                                                                    ║
║   SUCCESS: "Edit applied. Version: N -> N+1"                                 ║
║   CONFLICT: Shows detailed diff and resolution options                       ║
║                                                                              ║
║ 💡 TIP: Use --file for content with quotes, newlines, or special chars!      ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'resolve': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: resolve                                                             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Resolve a conflict after an edit attempt failed                     ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa resolve <node_id> <RESOLUTION> --agent <you>          ║
║                                                                              ║
║ RESOLUTION OPTIONS:                                                          ║
║                                                                              ║
║   ACCEPT_YOURS      - Use your version, discard theirs                       ║
║   ACCEPT_THEIRS     - Use their version, discard yours                       ║
║   ACCEPT_AUTO_MERGE - Use system's suggested merge (if available)            ║
║   MANUAL_MERGE      - Provide your own merged content (RECOMMENDED!)         ║
║                                                                              ║
║ MANUAL_MERGE CONTENT (3 ways):                                               ║
║                                                                              ║
║   1. INLINE:                                                                 ║
║      niwa resolve h2_3 MANUAL_MERGE "<content>" --agent me   ║
║                                                                              ║
║   2. FROM FILE (recommended for complex merges!):                            ║
║      niwa resolve h2_3 MANUAL_MERGE --file merged.md --agent ║
║                                                                              ║
║   3. FROM STDIN:                                                             ║
║      cat merged.txt | niwa resolve h2_3 MANUAL_MERGE --stdin ║
║                                                                              ║
║ EXAMPLES:                                                                    ║
║   niwa resolve h2_3 ACCEPT_YOURS --agent claude_1            ║
║   niwa resolve h2_3 ACCEPT_AUTO_MERGE --agent claude_1       ║
║   niwa resolve h2_3 MANUAL_MERGE --file /tmp/m.md --agent me ║
║                                                                              ║
║ 💡 TIP: Use --file for merged content with quotes, newlines, special chars!  ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'export': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: export                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Export the database back to markdown format                         ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa export                   # Print to stdout            ║
║   niwa export > output.md      # Save to file                ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa export > main_plan.md                                 ║
║                                                                              ║
║ WHAT IT DOES:                                                                ║
║   - Traverses the document tree                                              ║
║   - Reconstructs markdown with headings and content                          ║
║   - Preserves all edits made through the database                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'title': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: title                                                               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Update a node's title (heading text)                                ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa title <node_id> "<new_title>" --agent <you>           ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa title h2_3 "Updated Section Name" --agent claude_1    ║
║                                                                              ║
║ NOTE: Title updates don't use conflict detection (titles are simple)         ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'summarize': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: summarize                                                           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Add a summary/description to a node                                 ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa summarize <node_id> "<summary>" --agent <you>         ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa summarize h2_3 "Covers API integration" --agent me    ║
║                                                                              ║
║ NOTE: Summaries help other agents understand sections without reading fully  ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'status': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: status                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Check your agent's current state (CRITICAL for sub-agents!)         ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa status --agent <your_agent_name>                      ║
║                                                                              ║
║ WHAT IT SHOWS:                                                               ║
║   - Pending reads: nodes you read but haven't edited yet                     ║
║   - Stale reads: your read is outdated (someone else edited since)           ║
║   - Pending conflicts: unresolved conflicts waiting for you                  ║
║   - Recent edits: your successful edits in the last hour                     ║
║                                                                              ║
║ ⚠️  CRITICAL FOR SUB-AGENTS:                                                  ║
║   If you're a NEW agent or have FRESH CONTEXT, run this FIRST!               ║
║   It tells you what state you're in before doing anything.                   ║
║                                                                              ║
║ EXAMPLE OUTPUT:                                                              ║
║   Agent: claude_sub_1                                                        ║
║   Pending Reads: 2                                                           ║
║     - h2_3: read v2, now at v4 (STALE by 2 versions!)                        ║
║     - h2_5: read v1, still at v1 (ok)                                        ║
║   Pending Conflicts: 1                                                       ║
║     - h2_3: conflict stored at 14:32, needs resolution                       ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'conflicts': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: conflicts                                                           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: List all pending conflicts (optionally filter by agent)             ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa conflicts                      # All conflicts        ║
║   niwa conflicts --agent <name>       # Your conflicts only  ║
║                                                                              ║
║ WHAT IT SHOWS:                                                               ║
║   - Node ID with conflict                                                    ║
║   - Which agent has the conflict                                             ║
║   - When the conflict was stored                                             ║
║   - Whether auto-merge is possible                                           ║
║                                                                              ║
║ TO RESOLVE A CONFLICT:                                                       ║
║   niwa resolve <node_id> <RESOLUTION> --agent <you>          ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'check': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: check                                                               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Verify database health (run this if things seem broken)             ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa check                                                 ║
║                                                                              ║
║ WHAT IT CHECKS:                                                              ║
║   - Database initialized?                                                    ║
║   - Root node exists?                                                        ║
║   - Node count and total versions                                            ║
║   - Pending edits and conflicts                                              ║
║   - Active agents list                                                       ║
║                                                                              ║
║ ⚠️  IF NOT INITIALIZED:                                                       ║
║   niwa init                                                  ║
║   niwa add "Section Title" --agent <name>                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'agents': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: agents                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: List all agents that have used this database                        ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa agents                                                ║
║                                                                              ║
║ WHAT IT SHOWS:                                                               ║
║   - Agent names and their edit counts                                        ║
║   - First and last seen timestamps                                           ║
║   - Which nodes each agent has touched                                       ║
║                                                                              ║
║ ⚠️  FOR SUB-AGENTS:                                                           ║
║   Check if your name is already in use! Pick a UNIQUE name.                  ║
║   Suggested format: <purpose>_<number> e.g. "researcher_2", "editor_3"       ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'whoami': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: whoami                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Quick state check + suggest unique agent name if needed             ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa whoami --agent <name>   # Check your state            ║
║   niwa whoami                  # Get suggested agent name    ║
║                                                                              ║
║ USE THIS WHEN:                                                               ║
║   - You're a NEW sub-agent and need a unique name                            ║
║   - You have FRESH CONTEXT and don't know your state                         ║
║   - You want to quickly check if you have pending work                       ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   $ niwa whoami                                              ║
║   Suggested agent name: agent_4                                              ║
║   (Use: --agent agent_4)                                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'search': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: search                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Find content by keyword (when you don't know the node ID)           ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa search "<query>"                                      ║
║   niwa search "<query>" --case-sensitive                     ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa search "attention"                                    ║
║   niwa search "TODO"                                         ║
║   niwa search "API" --case-sensitive                         ║
║                                                                              ║
║ OUTPUT:                                                                      ║
║   Shows matching nodes with:                                                 ║
║   - Node ID (for use with read/edit)                                         ║
║   - Title                                                                    ║
║   - Matching lines with line numbers                                         ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'history': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: history                                                             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: View version history for a node (for rollback/undo)                 ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa history <node_id>                                     ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa history h2_3                                          ║
║                                                                              ║
║ OUTPUT:                                                                      ║
║   Lists all versions with:                                                   ║
║   - Version number                                                           ║
║   - Who edited (agent)                                                       ║
║   - When (timestamp)                                                         ║
║   - Edit summary                                                             ║
║   - Content preview (if available)                                           ║
║                                                                              ║
║ TO ROLLBACK:                                                                 ║
║   niwa rollback <node_id> <version> --agent <you>            ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'rollback': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: rollback                                                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Restore a node to a previous version                                ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa rollback <node_id> <version> --agent <you>            ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   # First check history                                                      ║
║   niwa history h2_3                                          ║
║                                                                              ║
║   # Then rollback to version 2                                               ║
║   niwa rollback h2_3 2 --agent claude_1                      ║
║                                                                              ║
║ NOTE:                                                                        ║
║   - Creates a NEW version with the old content (doesn't delete history)      ║
║   - You can always rollback the rollback!                                    ║
║   - Only versions with stored content can be rolled back                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'dry-run': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: dry-run                                                             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Preview what would happen if you edited (without actually editing)  ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa dry-run <node_id> "<content>" --agent <you>           ║
║   niwa dry-run <node_id> --file <path> --agent <you>         ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa dry-run h2_3 "New content" --agent claude_1           ║
║                                                                              ║
║ OUTPUT:                                                                      ║
║   Would succeed: Yes/No                                                      ║
║   - If yes: shows version change (v3 -> v4)                                  ║
║   - If no: shows why (conflict, node not found, etc.)                        ║
║                                                                              ║
║ USE THIS TO:                                                                 ║
║   - Check if your edit would conflict before trying                          ║
║   - Verify you have the right node                                           ║
║   - Test without risk                                                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'cleanup': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: cleanup                                                             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Clean up stale pending reads and old conflicts                      ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa cleanup                                               ║
║   niwa cleanup --max-age 7200   # 2 hours                    ║
║                                                                              ║
║ WHAT IT CLEANS:                                                              ║
║   - Pending reads older than 1 hour (default)                                ║
║   - Stored conflicts older than 24 hours                                     ║
║                                                                              ║
║ WHEN TO USE:                                                                 ║
║   - After many agents have come and gone                                     ║
║   - If pending_edit_count is high in `check`                                 ║
║   - Periodic maintenance                                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'setup': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ COMMAND: setup                                                               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ PURPOSE: Set up hook integration with LLM tools (Claude Code, etc.)          ║
║                                                                              ║
║ USAGE:                                                                       ║
║   niwa setup claude     # Set up Claude Code hooks           ║
║   niwa setup --remove   # Remove hook configuration          ║
║                                                                              ║
║ SUPPORTED INTEGRATIONS:                                                      ║
║   claude  - Claude Code (creates .claude/settings.json)                      ║
║                                                                              ║
║ WHAT IT DOES:                                                                ║
║   - Injects Niwa usage guide so Claude knows how to use the tool             ║
║   - Preserves context before compaction (Claude remembers after /compact)    ║
║   - Warns about conflicts before file edits                                  ║
║   - Reminds about unresolved conflicts when session ends                     ║
║                                                                              ║
║ HOOKS INSTALLED:                                                             ║
║   SessionStart - Injects usage guide + database status                       ║
║   PreCompact   - Preserves Niwa context before context compaction            ║
║   PreToolUse   - Warns about conflicts before Write/Edit operations          ║
║   PostToolUse  - Hints to sync markdown file changes to database             ║
║   Stop         - Reminds about unresolved conflicts                          ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
}

ERROR_PROMPTS = {
    'no_node_id': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ ERROR: Missing node_id                                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ You need to specify which node to operate on.                                ║
║                                                                              ║
║ HOW TO FIND NODE IDs:                                                        ║
║   niwa tree                                                  ║
║                                                                              ║
║ This shows all nodes like:                                                   ║
║   [h2_3] v2 "Section Title" (by agent_A)                                     ║
║    ^^^^                                                                      ║
║    This is the node_id!                                                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'no_content': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ ERROR: Missing content                                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ You need to provide the new content for the edit.                            ║
║                                                                              ║
║ CORRECT USAGE:                                                               ║
║   niwa edit <node_id> "<your new content here>" --agent <me> ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa edit h2_3 "# My Section\\n\\nNew paragraph here." \\    ║
║       --agent claude_1 --summary "Updated section"                           ║
║                                                                              ║
║ TIP: For multi-line content, use \\n for newlines or single quotes            ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'no_file': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ ERROR: Missing file path                                                  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ You need to specify which markdown file to load.                             ║
║                                                                              ║
║ CORRECT USAGE:                                                               ║
║   niwa load <path/to/file.md>                                ║
║                                                                              ║
║ EXAMPLES:                                                                    ║
║   niwa load main_plan.md                                     ║
║   niwa load ./docs/specification.md                          ║
║   niwa load /home/user/project/README.md                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'node_not_found': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ ERROR: Node not found                                                     ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ The node_id you specified doesn't exist in the database.                     ║
║                                                                              ║
║ COMMON CAUSES:                                                               ║
║   - Typo in node_id (they're case-sensitive!)                                ║
║   - Database not initialized or file not loaded                              ║
║   - Node was in a different document                                         ║
║                                                                              ║
║ HOW TO FIX:                                                                  ║
║   1. List all nodes: niwa tree                               ║
║   2. Find the correct node_id                                                ║
║   3. Try again with the correct ID                                           ║
║                                                                              ║
║ If tree shows nothing, you may need to:                                      ║
║   niwa init                                                  ║
║   niwa add "Section Title" --agent <name>                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'no_resolution': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ ERROR: Missing resolution type                                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ You need to specify how to resolve the conflict.                             ║
║                                                                              ║
║ VALID RESOLUTIONS:                                                           ║
║                                                                              ║
║   ACCEPT_YOURS      - Use your version, discard theirs                       ║
║   ACCEPT_THEIRS     - Use their version, discard yours                       ║
║   ACCEPT_AUTO_MERGE - Use system's suggested merge                           ║
║   MANUAL_MERGE      - Provide your own merged content                        ║
║                                                                              ║
║ EXAMPLES:                                                                    ║
║   niwa resolve h2_3 ACCEPT_YOURS --agent me                  ║
║   niwa resolve h2_3 MANUAL_MERGE "merged content" --agent me ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
    'unknown_command': """
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ ERROR: Unknown command                                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ VALID COMMANDS:                                                              ║
║                                                                              ║
║ SETUP:                                                                       ║
║   init      - Initialize database                                            ║
║   load      - Load markdown file                                             ║
║   check     - Verify database health                                         ║
║                                                                              ║
║ BROWSE:                                                                      ║
║   tree      - Show document structure                                        ║
║   peek      - Quick view (no edit tracking)                                  ║
║                                                                              ║
║ EDIT:                                                                        ║
║   read      - Read for editing (tracks version)                              ║
║   edit      - Edit a node                                                    ║
║   resolve   - Resolve a conflict                                             ║
║   title     - Update node title                                              ║
║   summarize - Add node summary                                               ║
║                                                                              ║
║ AGENT STATUS (critical for sub-agents!):                                     ║
║   status    - Check your state, pending reads, conflicts                     ║
║   conflicts - List all pending conflicts                                     ║
║   agents    - List all agents who've used this DB                            ║
║   whoami    - Quick state check + suggest unique name                        ║
║                                                                              ║
║ OUTPUT:                                                                      ║
║   export    - Export to markdown                                             ║
║   help      - Show full guide                                                ║
║                                                                              ║
║ Get help for any command:                                                    ║
║   niwa help                                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
""",
}


def print_error(error_type: str, context: dict = None, show_full_guide: bool = True):
    """Print an LLM-friendly error message with guidance."""
    # ALWAYS show the full system prompt on errors - this teaches the LLM the correct usage
    if show_full_guide:
        print(LLM_SYSTEM_PROMPT)
        print("\n" + "=" * 80)
        print("❌ ERROR OCCURRED - SEE DETAILS BELOW")
        print("=" * 80 + "\n")

    if error_type in ERROR_PROMPTS:
        print(ERROR_PROMPTS[error_type])
    else:
        print(f"Error: {error_type}")

    if context:
        print("\nContext:")
        for k, v in context.items():
            print(f"  {k}: {v}")


def print_command_help(command: str):
    """Print detailed help for a specific command."""
    if command in COMMAND_HELP:
        print(COMMAND_HELP[command])
    else:
        print(f"No detailed help for '{command}'. Run 'niwa help' for full guide.")


def main():
    import sys
    import argparse

    COMMANDS_HELP = """
commands:
  init                  Initialize a new database
  load <file>           Load markdown file into database
  add <title> [content] Add a node directly (--parent, --file, --stdin)
  tree                  Show document structure with node IDs
  peek <id>             Quick view (no edit tracking)
  read <id>             Read for editing (tracks version for conflict detection)
  edit <id> <content>   Edit a node's content
  resolve <id> <type>   Resolve a conflict (ACCEPT_YOURS|ACCEPT_THEIRS|ACCEPT_AUTO_MERGE|MANUAL_MERGE)
  search <query>        Search content by keyword
  history <id>          View version history of a node
  rollback <id> <ver>   Restore node to previous version
  export                Export database back to markdown
  status                Check agent's pending reads/conflicts
  conflicts             List unresolved conflicts for agent
  agents                List all agents who've used this database
  whoami                Get suggested unique agent name
  check                 Verify database health
  cleanup               Remove stale pending reads/conflicts
  setup <target>        Set up integrations (e.g., 'setup claude')
  help [command]        Show help (optionally for specific command)

examples:
  niwa init                              # Initialize database
  niwa add "Requirements" --agent claude  # Add a node directly
  niwa tree                              # See structure
  niwa read h2_3 --agent claude_1        # Read node for editing
  niwa edit h2_3 "new content" --agent claude_1  # Edit node
  niwa export > output.md                # Export to markdown
"""

    parser = argparse.ArgumentParser(
        description="Niwa 庭 - Collaborative Markdown Database for LLM Agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=COMMANDS_HELP
    )
    parser.add_argument('command', nargs='?', default='help', metavar='COMMAND',
                       help='Command to run (see commands below)')
    parser.add_argument('args', nargs='*', help='Command arguments')
    parser.add_argument('--agent', default='default_agent', help='Agent ID (use a unique name!)')
    parser.add_argument('--summary', default=None, help='Edit summary (helps with conflict resolution)')
    parser.add_argument('--strategy', default='prompt', choices=['prompt', 'auto', 'force'],
                       help='Conflict resolution strategy: prompt (default), auto, force')
    parser.add_argument('--file', default=None, help='Read content from file instead of command line (avoids escaping issues)')
    parser.add_argument('--stdin', action='store_true', help='Read content from stdin (for piping)')
    parser.add_argument('--case-sensitive', action='store_true', help='Case-sensitive search')
    parser.add_argument('--max-age', type=int, default=3600, help='Max age in seconds for cleanup (default 3600)')
    parser.add_argument('--dry-run', action='store_true', help='Preview edit without applying')
    parser.add_argument('--parent', default=None, help='Parent node ID for add command (default: root)')
    parser.add_argument('--remove', action='store_true', help='Remove hook configuration (for setup --remove)')
    # Hook event handling (called by Claude Code hooks)
    parser.add_argument('--hook-event', default=None, help='Hook event name (internal use by hooks)')
    parser.add_argument('--hook-input', default=None, help='Path to hook input JSON file (internal use)')

    args = parser.parse_args()

    # Help commands
    if args.command == 'help':
        if args.args:
            print_command_help(args.args[0])
        else:
            print(LLM_SYSTEM_PROMPT)
        return

    # ==========================================================================
    # SETUP COMMAND - Doesn't require database
    # ==========================================================================
    if args.command == 'setup':
        if not args.args:
            print_command_help('setup')
            print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ ERROR: Missing integration target                                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ USAGE:                                                                       ║
║   niwa setup claude     # Set up Claude Code hooks           ║
║   niwa setup --remove   # Remove hooks (after target)        ║
║                                                                              ║
║ SUPPORTED TARGETS:                                                           ║
║   claude - Claude Code (creates .claude/settings.json)                       ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            return

        target = args.args[0].lower()

        if target == 'claude':
            project_dir = os.getcwd()
            success, message = setup_claude_hooks(project_dir, remove=args.remove)

            if success:
                if args.remove:
                    print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ✅ CLAUDE CODE HOOKS REMOVED                                                 ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ {message:<76} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                else:
                    print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ✅ CLAUDE CODE HOOKS INSTALLED                                               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ {message:<76} ║
║                                                                              ║
║ HOOKS INSTALLED:                                                             ║
║   • SessionStart - Injects Niwa usage guide + status on session start        ║
║   • PreCompact   - Preserves Niwa context before context compaction          ║
║   • PreToolUse   - Warns about conflicts before Write/Edit                   ║
║   • PostToolUse  - Hints to sync after markdown file changes                 ║
║   • Stop         - Reminds about unresolved conflicts                        ║
║                                                                              ║
║ Claude will remember how to use Niwa even after /compact.                    ║
║                                                                              ║
║ TO REMOVE LATER:                                                             ║
║   niwa setup claude --remove                                                ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            else:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ SETUP FAILED                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ {message:<76} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            return
        else:
            print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ UNKNOWN TARGET: {target:<56} ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ SUPPORTED TARGETS:                                                           ║
║   claude - Claude Code (creates .claude/settings.json)                       ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa setup claude                                          ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            return

    # ==========================================================================
    # HOOK COMMAND - Called by Claude Code hooks (internal use)
    # ==========================================================================
    if args.command == 'hook':
        if not args.hook_event:
            print("Hook event not specified. Use --hook-event <event_name>", file=sys.stderr)
            sys.exit(1)

        # Handle the hook event
        exit_code = handle_hook_event(args.hook_event)
        sys.exit(exit_code)

    # Check for database existence for commands that need it
    db_exists = Path(".niwa/data.lmdb").exists()

    if args.command != 'init' and not db_exists:
        print(LLM_SYSTEM_PROMPT)
        print("\n" + "=" * 80)
        print("❌ DATABASE NOT INITIALIZED - YOU MUST INITIALIZE FIRST")
        print("=" * 80)
        print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ DATABASE NOT INITIALIZED                                                  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ The database doesn't exist yet. You need to initialize it first.             ║
║                                                                              ║
║ STEP 1: Initialize                                                           ║
║   niwa init                                                  ║
║                                                                              ║
║ STEP 2: Add nodes to build the tree                                          ║
║   niwa add "Section Title" --agent <name>                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
        return

    db = Niwa()

    # Validate agent name for commands that use it
    agent_commands = ['read', 'edit', 'resolve', 'status', 'conflicts', 'whoami', 'dry-run', 'rollback', 'add']
    if args.command in agent_commands and args.agent != 'default_agent':
        valid, msg = db.validate_agent_name(args.agent)
        if not valid:
            print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ INVALID AGENT NAME                                                        ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ {msg:<76} ║
║                                                                              ║
║ Agent names must:                                                            ║
║   - Contain only letters, numbers, underscore (_), hyphen (-)                ║
║   - Be 1-50 characters long                                                  ║
║   - Not be "default_agent"                                                   ║
║                                                                              ║
║ GOOD EXAMPLES: claude_1, researcher_A, agent-42                              ║
║ BAD EXAMPLES: "my agent", agent@1, agent/sub                                 ║
║                                                                              ║
║ Get a suggested name: niwa whoami                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            db.close()
            return

    try:
        if args.command == 'init':
            if db_exists:
                print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ℹ️  DATABASE ALREADY EXISTS                                                   ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ A database already exists at .niwa/                                          ║
║                                                                              ║
║ OPTIONS:                                                                     ║
║   - Continue using existing database: niwa tree              ║
║   - Add nodes directly: niwa add "Title" --agent <name>      ║
║   - Start fresh: rm -rf .niwa && niwa init                   ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            else:
                db.create_node('root', 'root', 'Document', '', 0, None, 'system')
                print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ✅ DATABASE INITIALIZED                                                      ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Created database at .niwa/                                                   ║
║                                                                              ║
║ NEXT STEP:                                                                   ║
║   niwa add "Section Title" --agent <name>           # Build tree directly   ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa add "Requirements" --agent claude_1                    ║
║   niwa add "Design" --parent h1_0 --agent claude_1            ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")

        elif args.command == 'load':
            if not args.args:
                print_error('no_file')
                return
            md_file = args.args[0]
            if not Path(md_file).exists():
                print(LLM_SYSTEM_PROMPT)
                print("\n" + "=" * 80)
                print("❌ FILE NOT FOUND")
                print("=" * 80)
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ FILE NOT FOUND: {md_file[:50]:<56} ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ The specified file doesn't exist.                                            ║
║                                                                              ║
║ CHECK:                                                                       ║
║   - Is the path correct?                                                     ║
║   - Is the filename spelled correctly?                                       ║
║   - Are you in the right directory?                                          ║
║                                                                              ║
║ Current directory: {os.getcwd()[:56]:<56} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                return
            root_id = db.load_markdown(md_file)
            print(f"✅ Loaded {md_file}\n")
            print(db.get_tree())
            print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ NEXT STEPS:                                                                  ║
║   niwa tree                  # View structure anytime        ║
║   niwa read <node_id> --agent <your_name>  # Read to edit    ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")

        elif args.command == 'add':
            if not args.args:
                print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ MISSING TITLE                                                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ USAGE:                                                                      ║
║   niwa add "Section Title"                          # Add under root        ║
║   niwa add "Subsection" --parent h1_0               # Add under parent      ║
║   niwa add "Section" "content here"                 # With inline content   ║
║   niwa add "Section" --file content.md              # Content from file     ║
║   niwa add "Section" --stdin                        # Content from stdin    ║
║   niwa add "Section" --agent alice                  # Specify agent         ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                return

            title = args.args[0]

            # Determine parent
            parent_id = args.parent if args.parent else 'root'
            parent_node = db.read_node(parent_id)
            if not parent_node:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ PARENT NODE NOT FOUND                                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Node ID: {parent_id:<66} ║
║                                                                              ║
║ Run 'niwa tree' to see available node IDs.                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                return

            # Determine content
            content = ''
            if args.file:
                try:
                    with open(args.file, 'r') as f:
                        content = f.read()
                except Exception as e:
                    print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ CANNOT READ FILE                                                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ {str(e)[:76]:<76} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                    return
            elif args.stdin:
                import sys
                content = sys.stdin.read()
            elif len(args.args) >= 2:
                content = args.args[1]

            # Check for duplicate title under same parent
            existing = db.find_child_by_title(parent_id, title)
            if existing:
                ex_id = existing['id']
                ex_agent = existing.get('last_agent', 'unknown')
                ex_ver = existing.get('version', 1)
                ex_content = existing.get('content', '')
                ex_preview = ex_content[:60] + ('...' if len(ex_content) > 60 else '') if ex_content else '(empty)'
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ⚠️  DUPLICATE TITLE DETECTED                                                ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ A node with this title already exists under the same parent.               ║
║                                                                              ║
║ EXISTING NODE:                                                               ║
║   ID:      {ex_id:<64} ║
║   Title:   {existing.get('title', '')[:64]:<64} ║
║   Agent:   {ex_agent:<64} ║
║   Version: {str(ex_ver):<64} ║
║   Content: {ex_preview[:64]:<64} ║
║                                                                              ║
║ OPTIONS:                                                                     ║
║   1. Edit existing: niwa read {ex_id} --agent <name>           ║
║   2. Use a different title                                                   ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                print(f"EXISTING_NODE_ID: {ex_id}")
                return

            # Infer level from parent
            level = parent_node.get('level', 0) + 1

            # Generate next sequential node ID
            node_id = db.next_node_id(level)

            agent_id = args.agent
            success = db.create_node(node_id, 'heading', title, content, level, parent_id, agent_id)

            if success:
                print(f"✅ Created node '{node_id}' under '{parent_id}'\n")
                print(f"   Title: {title}")
                if content:
                    preview = content[:80] + ('...' if len(content) > 80 else '')
                    print(f"   Content: {preview}")
                print(f"   Level: {level}")
                print(f"   Agent: {agent_id}\n")
                print(f"NODE_ID: {node_id}\n")
                print(db.get_tree())
            else:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ FAILED TO CREATE NODE                                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Node ID '{node_id}' may already exist.                                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")

        elif args.command == 'tree':
            tree = db.get_tree()
            if not tree or tree.strip() == "# Document Structure\n":
                print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ℹ️  DATABASE IS EMPTY                                                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ No nodes created yet.                                                        ║
║                                                                              ║
║ ADD YOUR FIRST NODE:                                                         ║
║   niwa add "Section Title" --agent <name>                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            else:
                print(tree)

        elif args.command == 'peek':
            if not args.args:
                print_error('no_node_id')
                print_command_help('peek')
                return
            node_id = args.args[0]
            result = db.peek(node_id)
            if "not found" in result:
                print_error('node_not_found', {'provided_id': node_id}, show_full_guide=True)
            else:
                print(result)

        elif args.command == 'read':
            if not args.args:
                print_error('no_node_id')
                print_command_help('read')
                return
            node_id = args.args[0]
            node = db.read_for_edit(node_id, args.agent)
            if node:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ 📖 NODE READ SUCCESSFULLY                                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Node ID: {node_id:<63} ║
║ Version: {node['version']:<63} ║
║ Title: {node.get('title', '(none)')[:61]:<63} ║
║ Last edited by: {node.get('last_agent', '?'):<54} ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ ⚠️  IMPORTANT: Your agent "{args.agent}" is now tracked as reading v{node['version']:<9} ║
║    If you edit and someone else edited in between → CONFLICT                 ║
╚══════════════════════════════════════════════════════════════════════════════╝

CONTENT:
─────────────────────────────────────────────────────────────────────────────────
{node['content']}
─────────────────────────────────────────────────────────────────────────────────

NEXT: To edit this node, run:
  niwa edit {node_id} "<your new content>" --agent {args.agent} --summary "what you changed"
""")
            else:
                print_error('node_not_found', {'provided_id': node_id})

        elif args.command == 'edit':
            if not args.args:
                print_error('no_node_id')
                print_command_help('edit')
                return

            node_id = args.args[0]

            # Get content from: --file, --stdin, or command line arg
            content = None
            if args.file:
                # Read content from file (avoids shell escaping issues!)
                try:
                    with open(args.file, 'r') as f:
                        content = f.read()
                    print(f"📄 Read content from file: {args.file}")
                except Exception as e:
                    print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ CANNOT READ FILE: {str(e)[:54]:<54} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                    return
            elif args.stdin:
                # Read content from stdin (for piping)
                import sys
                content = sys.stdin.read()
                print("📄 Read content from stdin")
            elif len(args.args) >= 2:
                content = args.args[1]
            else:
                print_error('no_content')
                print_command_help('edit')
                print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║ 💡 TIP: Use --file to avoid shell escaping issues!                           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ For complex content with quotes/newlines:                                    ║
║                                                                              ║
║ 1. Write content to a file:                                                  ║
║    echo "Your content here" > /tmp/edit_content.txt                          ║
║                                                                              ║
║ 2. Edit using --file:                                                        ║
║    niwa edit h2_3 --file /tmp/edit_content.txt --agent me    ║
║                                                                              ║
║ Or use --stdin for piping:                                                   ║
║    cat content.txt | niwa edit h2_3 --stdin --agent me       ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                return

            result = db.edit_node(
                node_id, content, args.agent, args.summary,
                resolution_strategy=args.strategy
            )

            if result.success:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ✅ EDIT SUCCESSFUL                                                           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ {result.message:<76} ║
║ Agent: {args.agent:<69} ║
║ Summary: {(args.summary or '(none)')[:66]:<67} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            elif result.conflict:
                # Store conflict for sub-agents to retrieve later
                db.store_conflict(args.agent, result.conflict)

                print("=" * 80)
                print("⚠️  CONFLICT DETECTED!")
                print("=" * 80)
                print(result.conflict.to_llm_prompt())
                print("=" * 80)
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ 💾 CONFLICT STORED - Can be retrieved later with:                            ║
║    niwa status --agent {args.agent:<37} ║
║    niwa conflicts --agent {args.agent:<34} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ HOW TO RESOLVE THIS CONFLICT:                                                ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║ Option 1 - Use YOUR version (discards their changes):                        ║
║   niwa resolve {node_id} ACCEPT_YOURS --agent {args.agent:<14} ║
║                                                                              ║
║ Option 2 - Keep THEIR version (discards your changes):                       ║
║   niwa resolve {node_id} ACCEPT_THEIRS --agent {args.agent:<13} ║
║                                                                              ║""")
                if result.conflict.auto_merge_possible:
                    print(f"""║ Option 3 - Use AUTO-MERGE (system's suggestion):                             ║
║   niwa resolve {node_id} ACCEPT_AUTO_MERGE --agent {args.agent:<8} ║
║                                                                              ║""")
                print(f"""║ Option 4 - MANUAL MERGE (combine both - RECOMMENDED):                        ║
║   niwa resolve {node_id} MANUAL_MERGE "<merged>" --agent {args.agent:<5} ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

💡 TIP: MANUAL_MERGE is usually best - it preserves everyone's work!
   Look at both "YOUR CHANGES" and "THEIR CHANGES" above, then combine them.
""")
            else:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ EDIT FAILED                                                               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ {result.message:<76} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                if "not found" in result.message.lower():
                    print_error('node_not_found', {'provided_id': node_id})

        elif args.command == 'resolve':
            if not args.args:
                print_error('no_node_id')
                print_command_help('resolve')
                return
            if len(args.args) < 2:
                print_error('no_resolution')
                print_command_help('resolve')
                return

            node_id = args.args[0]
            resolution = args.args[1].upper()

            # Get manual_content from: --file, --stdin, or command line arg
            manual_content = None
            if resolution == 'MANUAL_MERGE':
                if args.file:
                    try:
                        with open(args.file, 'r') as f:
                            manual_content = f.read()
                        print(f"📄 Read merged content from file: {args.file}")
                    except Exception as e:
                        print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ CANNOT READ FILE: {str(e)[:54]:<54} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                        return
                elif args.stdin:
                    import sys
                    manual_content = sys.stdin.read()
                    print("📄 Read merged content from stdin")
                elif len(args.args) > 2:
                    manual_content = args.args[2]

            valid_resolutions = ['ACCEPT_YOURS', 'ACCEPT_THEIRS', 'ACCEPT_AUTO_MERGE', 'MANUAL_MERGE']
            if resolution not in valid_resolutions:
                print(LLM_SYSTEM_PROMPT)
                print("\n" + "=" * 80)
                print("❌ INVALID RESOLUTION TYPE")
                print("=" * 80)
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ INVALID RESOLUTION: {resolution[:50]:<53} ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Valid options are:                                                           ║
║   - ACCEPT_YOURS                                                             ║
║   - ACCEPT_THEIRS                                                            ║
║   - ACCEPT_AUTO_MERGE                                                        ║
║   - MANUAL_MERGE                                                             ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                print_command_help('resolve')
                return

            if resolution == 'MANUAL_MERGE' and not manual_content:
                print(LLM_SYSTEM_PROMPT)
                print("\n" + "=" * 80)
                print("❌ MANUAL_MERGE REQUIRES CONTENT")
                print("=" * 80)
                print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ MANUAL_MERGE REQUIRES CONTENT                                             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ When using MANUAL_MERGE, you must provide the merged content.                ║
║                                                                              ║
║ USAGE (3 ways):                                                              ║
║                                                                              ║
║ 1. INLINE:                                                                   ║
║    niwa resolve <id> MANUAL_MERGE "<content>" --agent <me>   ║
║                                                                              ║
║ 2. FROM FILE (recommended for complex content!):                             ║
║    niwa resolve <id> MANUAL_MERGE --file <path> --agent <me> ║
║                                                                              ║
║ 3. FROM STDIN:                                                               ║
║    cat merged.txt | niwa resolve <id> MANUAL_MERGE --stdin   ║
║                                                                              ║
║ EXAMPLE:                                                                     ║
║   niwa resolve h2_3 MANUAL_MERGE --file /tmp/merged.md       ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                return

            result = db.resolve_conflict(
                node_id, resolution, args.agent,
                manual_content=manual_content
            )

            if result.success:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ✅ CONFLICT RESOLVED                                                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ {result.message:<76} ║
║ Resolution: {resolution:<64} ║
║ New version: {result.new_version if result.new_version else 'N/A':<63} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                # Clear the stored conflict
                db.clear_conflict(args.agent, node_id)
            else:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ RESOLUTION FAILED                                                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ {result.message:<76} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")

        elif args.command == 'title':
            if len(args.args) < 2:
                print_command_help('title')
                return
            node_id = args.args[0]
            title = args.args[1]
            result = db.update_title(node_id, title, args.agent)
            if result.success:
                print(f"✅ Title updated for {node_id}")
            else:
                print(f"❌ {result.message}")

        elif args.command == 'summarize':
            if len(args.args) < 2:
                print_command_help('summarize')
                return
            node_id = args.args[0]
            summary = args.args[1]
            result = db.update_summary(node_id, summary, args.agent)
            if result.success:
                print(f"✅ Summary updated for {node_id}")
            else:
                print(f"❌ {result.message}")

        elif args.command == 'export':
            print(db.export_markdown())

        # =====================================================================
        # AGENT STATUS COMMANDS (critical for sub-agents with fresh context)
        # =====================================================================

        elif args.command == 'status':
            status = db.get_agent_status(args.agent)
            print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ 📊 AGENT STATUS                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Agent: {args.agent:<69} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            # Pending reads
            if status['pending_reads']:
                print("📖 PENDING READS (you read but haven't edited yet):")
                print("─" * 78)
                for pr in status['pending_reads']:
                    stale_warning = ""
                    if pr['stale']:
                        stale_warning = f" ⚠️  STALE! (outdated by {pr['stale_by']} version(s))"
                    print(f"  [{pr['node_id']}] read v{pr['read_version']}, now at v{pr['current_version']}{stale_warning}")
                print()
            else:
                print("📖 No pending reads.\n")

            # Pending conflicts
            if status['pending_conflicts']:
                print("⚠️  PENDING CONFLICTS (need resolution!):")
                print("─" * 78)
                for pc in status['pending_conflicts']:
                    auto = "auto-merge available" if pc.get('auto_merge_possible') else "manual merge needed"
                    print(f"  [{pc['node_id']}] \"{pc.get('node_title', '?')[:40]}\" ({auto})")
                    print(f"     Your version was based on v{pc.get('your_base_version')}, current is v{pc.get('current_version')}")
                    print(f"     → Resolve: niwa resolve {pc['node_id']} <RESOLUTION> --agent {args.agent}")
                print()
            else:
                print("✅ No pending conflicts.\n")

            # Recent edits
            if status['recent_edits']:
                print("✏️  RECENT EDITS (last hour):")
                print("─" * 78)
                for re in status['recent_edits'][:5]:  # Show max 5
                    from datetime import datetime
                    ts = datetime.fromtimestamp(re['timestamp']).strftime('%H:%M:%S')
                    print(f"  [{re['node_id']}] v{re['version']} at {ts}: {re.get('summary', '(no summary)')[:50]}")
                print()

            # Nodes touched
            if status['nodes_touched']:
                print(f"📝 Nodes you've touched: {', '.join(status['nodes_touched'][:10])}")
                if len(status['nodes_touched']) > 10:
                    print(f"   ... and {len(status['nodes_touched']) - 10} more")

        elif args.command == 'conflicts':
            conflicts = db.get_pending_conflicts(args.agent if args.agent != "default_agent" else None)

            if not conflicts:
                print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ✅ NO PENDING CONFLICTS                                                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            else:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ⚠️  PENDING CONFLICTS: {len(conflicts):<53} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                for c in conflicts:
                    from datetime import datetime
                    ts = datetime.fromtimestamp(c.get('stored_at', 0)).strftime('%Y-%m-%d %H:%M')
                    auto = "✓ auto-merge" if c.get('auto_merge_possible') else "✗ manual"
                    print(f"""
┌──────────────────────────────────────────────────────────────────────────────┐
│ Node: [{c['node_id']}] "{c.get('node_title', '?')[:50]}"
│ Agent: {c['agent_id']}
│ Stored: {ts}
│ Version conflict: v{c.get('your_base_version')} → v{c.get('current_version')} ({auto})
│
│ Resolve with:
│   niwa resolve {c['node_id']} ACCEPT_YOURS --agent {c['agent_id']}
│   niwa resolve {c['node_id']} ACCEPT_THEIRS --agent {c['agent_id']}
│   niwa resolve {c['node_id']} MANUAL_MERGE "<content>" --agent {c['agent_id']}
└──────────────────────────────────────────────────────────────────────────────┘""")

        elif args.command == 'check':
            health = db.get_db_health()
            status_icon = "✅" if health['initialized'] else "❌"
            print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ 🏥 DATABASE HEALTH CHECK                                                     ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Status: {status_icon} {'INITIALIZED' if health['initialized'] else 'NOT INITIALIZED':<66} ║
║ Path: {str(health['db_path'])[:70]:<70} ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Nodes: {health['node_count']:<69} ║
║ Has root: {'Yes' if health['has_root'] else 'No':<66} ║
║ Total versions: {health['total_versions']:<60} ║
║ Pending edits: {health['pending_edit_count']:<61} ║
║ Pending conflicts: {health['pending_conflict_count']:<57} ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Active agents: {', '.join(health['active_agents'][:5]) if health['active_agents'] else '(none)':<61} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            if not health['initialized']:
                print("""
⚠️  DATABASE NOT INITIALIZED!

Run these commands to set up:
  niwa init
  niwa add "Section Title" --agent <name>
""")

        elif args.command == 'agents':
            agents = db.list_all_agents()
            if not agents:
                print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║ No agents have used this database yet.                                       ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            else:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ 👥 REGISTERED AGENTS: {len(agents):<54} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                for a in agents:
                    from datetime import datetime
                    first = datetime.fromtimestamp(a['first_seen']).strftime('%m-%d %H:%M') if a['first_seen'] else '?'
                    last = datetime.fromtimestamp(a['last_seen']).strftime('%m-%d %H:%M') if a['last_seen'] else '?'
                    nodes = ', '.join(a['nodes_edited'][:3])
                    if len(a['nodes_edited']) > 3:
                        nodes += f" +{len(a['nodes_edited'])-3} more"
                    print(f"""
┌──────────────────────────────────────────────────────────────────────────────┐
│ Agent: {a['agent_id']:<68} │
│ Edits: {a['edit_count']:<68} │
│ First seen: {first:<63} │
│ Last seen: {last:<64} │
│ Nodes: {nodes:<68} │
└──────────────────────────────────────────────────────────────────────────────┘""")

                # Suggest a new unique name
                suggested = db.suggest_agent_name()
                print(f"""
💡 Need a unique agent name? Try: --agent {suggested}
""")

        elif args.command == 'whoami':
            if args.agent == "default_agent":
                # No agent specified - suggest one
                suggested = db.suggest_agent_name()
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ 🤖 AGENT NAME SUGGESTION                                                     ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ You haven't specified an agent name.                                         ║
║                                                                              ║
║ Suggested unique name: {suggested:<53} ║
║                                                                              ║
║ Use it like this:                                                            ║
║   niwa read <node_id> --agent {suggested:<29} ║
║   niwa edit <node_id> "<content>" --agent {suggested:<18} ║
╚══════════════════════════════════════════════════════════════════════════════╝

⚠️  IMPORTANT FOR SUB-AGENTS:
   - Use a UNIQUE name to avoid conflicts with other agents
   - Use the SAME name consistently for all your reads/edits
   - Check existing agents with: niwa agents
""")
            else:
                # Agent specified - show their state
                status = db.get_agent_status(args.agent)
                pending_reads = len(status['pending_reads'])
                stale_reads = sum(1 for pr in status['pending_reads'] if pr['stale'])
                pending_conflicts = len(status['pending_conflicts'])

                state_icon = "✅" if pending_conflicts == 0 and stale_reads == 0 else "⚠️"
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ 🤖 AGENT: {args.agent:<66} ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Status: {state_icon} {'CLEAR' if pending_conflicts == 0 and stale_reads == 0 else 'NEEDS ATTENTION':<67} ║
║ Pending reads: {pending_reads:<61} ║
║ Stale reads: {stale_reads:<63} ║
║ Pending conflicts: {pending_conflicts:<57} ║
║ Nodes touched: {len(status['nodes_touched']):<61} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                if pending_conflicts > 0:
                    print(f"⚠️  You have {pending_conflicts} conflict(s) to resolve!")
                    print(f"    Run: niwa conflicts --agent {args.agent}")
                if stale_reads > 0:
                    print(f"⚠️  You have {stale_reads} stale read(s) - re-read before editing!")
                    print(f"    Run: niwa status --agent {args.agent}")
                if pending_conflicts == 0 and stale_reads == 0:
                    print("✅ Ready to work! No pending issues.")

        # =====================================================================
        # SEARCH, HISTORY, ROLLBACK, DRY-RUN, CLEANUP
        # =====================================================================

        elif args.command == 'search':
            if not args.args:
                print_command_help('search')
                return
            query = args.args[0]
            results = db.search_content(query, case_sensitive=args.case_sensitive)

            if not results:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ 🔍 NO RESULTS FOUND                                                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Query: "{query[:60]:<60}" ║
║ Case-sensitive: {'Yes' if args.case_sensitive else 'No':<59} ║
║                                                                              ║
║ Try:                                                                         ║
║   - Different keywords                                                       ║
║   - Without --case-sensitive                                                 ║
║   - Partial words                                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            else:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ 🔍 SEARCH RESULTS: {len(results)} node(s) found                                        ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Query: "{query[:60]:<60}" ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                for r in results[:10]:  # Limit to 10 results
                    title_match = " (title match)" if r['match_in_title'] else ""
                    print(f"""
┌──────────────────────────────────────────────────────────────────────────────┐
│ [{r['node_id']}] v{r['version']} "{r['title'][:50]}"{title_match}
│ Matches: {r['total_matches']} line(s)""")
                    for line_num, line_text in r['matching_lines'][:3]:
                        print(f"│   Line {line_num}: {line_text[:65]}...")
                    print("│")
                    print(f"│ → Read: niwa read {r['node_id']} --agent <your_name>")
                    print("└──────────────────────────────────────────────────────────────────────────────┘")

                if len(results) > 10:
                    print(f"\n... and {len(results) - 10} more results")

        elif args.command == 'history':
            if not args.args:
                print_command_help('history')
                return
            node_id = args.args[0]
            history = db.get_node_history(node_id)

            if not history:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ NO HISTORY FOUND                                                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Node "{node_id}" not found or has no history.                                ║
║                                                                              ║
║ Check available nodes: niwa tree                             ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
            else:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ 📜 VERSION HISTORY: [{node_id}]                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                for h in history:
                    from datetime import datetime
                    ts = datetime.fromtimestamp(h['timestamp']).strftime('%Y-%m-%d %H:%M:%S') if h.get('timestamp') else '?'
                    has_content = "✓ can rollback" if h.get('has_content') else "✗ no content stored"
                    preview = h.get('content_preview', '')[:60].replace('\n', ' ') + '...' if h.get('content_preview') else ''
                    print(f"""
┌──────────────────────────────────────────────────────────────────────────────┐
│ Version {h['version']} - {ts}
│ Agent: {h.get('agent', '?')}
│ Summary: {h.get('summary', '(none)')[:60]}
│ Status: {has_content}
│ Preview: {preview[:60]}
└──────────────────────────────────────────────────────────────────────────────┘""")

                print(f"""
💡 To rollback: niwa rollback {node_id} <version> --agent <you>
""")

        elif args.command == 'rollback':
            if len(args.args) < 2:
                print_command_help('rollback')
                return
            node_id = args.args[0]
            try:
                version = int(args.args[1])
            except ValueError:
                print(f"❌ Version must be a number, got: {args.args[1]}")
                return

            # Get the old content
            old_content = db.get_version_content(node_id, version)
            if not old_content:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ❌ CANNOT ROLLBACK                                                           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Version {version} content not found for node {node_id}.                        ║
║                                                                              ║
║ Possible reasons:                                                            ║
║   - Version doesn't exist                                                    ║
║   - Content wasn't stored (older versions)                                   ║
║                                                                              ║
║ Check available versions: niwa history {node_id}             ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                return

            # Read first (to set up for edit)
            db.read_for_edit(node_id, args.agent)

            # Apply the rollback as an edit
            result = db.edit_node(
                node_id, old_content, args.agent,
                f"Rollback to v{version}",
                resolution_strategy='force'  # Force because we're intentionally overwriting
            )

            if result.success:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ✅ ROLLBACK SUCCESSFUL                                                       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Node: {node_id:<70} ║
║ Rolled back to version {version} content                                      ║
║ New version: {result.new_version:<63} ║
║ Agent: {args.agent:<69} ║
╚══════════════════════════════════════════════════════════════════════════════╝

💡 Note: This created a NEW version with the old content. History is preserved.
   To undo this rollback, run rollback again with the previous version number.
""")
            else:
                print(f"❌ Rollback failed: {result.message}")

        elif args.command == 'dry-run':
            if not args.args:
                print_command_help('dry-run')
                return

            node_id = args.args[0]

            # Get content from file, stdin, or args
            content = None
            if args.file:
                try:
                    with open(args.file, 'r') as f:
                        content = f.read()
                except Exception as e:
                    print(f"❌ Cannot read file: {e}")
                    return
            elif args.stdin:
                import sys
                content = sys.stdin.read()
            elif len(args.args) >= 2:
                content = args.args[1]
            else:
                print("❌ No content provided. Use inline, --file, or --stdin")
                return

            result = db.dry_run_edit(node_id, content, args.agent)

            if result['would_succeed']:
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ ✅ DRY RUN: EDIT WOULD SUCCEED                                               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Node: {node_id:<70} ║
║ {result['message']:<76} ║
║ Content changed: {'Yes' if result.get('content_changed') else 'No (same content)':<58} ║
╚══════════════════════════════════════════════════════════════════════════════╝

To actually apply this edit:
  niwa edit {node_id} {"--file " + args.file if args.file else '"<content>"'} --agent {args.agent}
""")
            else:
                icon = "⚠️" if result['reason'] == 'conflict' else "❌"
                print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ {icon} DRY RUN: EDIT WOULD FAIL                                               ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Node: {node_id:<70} ║
║ Reason: {result['reason']:<68} ║
║ {result['message']:<76} ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
                if result['reason'] == 'conflict':
                    print(f"""
You're {result.get('versions_behind', '?')} version(s) behind.

Options:
  1. Re-read to get latest: niwa read {node_id} --agent {args.agent}
  2. Force edit (dangerous): niwa edit {node_id} ... --strategy force
""")

        elif args.command == 'cleanup':
            reads_cleaned = db.cleanup_stale_reads(args.max_age)
            conflicts_cleaned = db.cleanup_stale_conflicts(args.max_age * 24)  # 24x longer for conflicts

            print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║ 🧹 CLEANUP COMPLETE                                                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Stale pending reads removed: {reads_cleaned:<46} ║
║ Stale conflicts removed: {conflicts_cleaned:<51} ║
║ Max age for reads: {args.max_age} seconds ({args.max_age // 60} minutes)                           ║
║ Max age for conflicts: {args.max_age * 24} seconds ({args.max_age * 24 // 3600} hours)                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")

        else:
            print_error('unknown_command')
            print(f"\nYou entered: '{args.command}'")

    finally:
        db.close()


if __name__ == "__main__":
    main()
