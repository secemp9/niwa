"""niwa.core - Auto-split module"""

import json
from pathlib import Path
from typing import Optional, Tuple
import sys

from .niwa import Niwa


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

CONFLICT RESOLUTIONS: ACCEPT_YOURS | ACCEPT_THEIRS | MANUAL_MERGE "content"

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
