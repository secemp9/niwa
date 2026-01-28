# Niwa 庭

**A Zen Garden for Your Plans - Collaborative Markdown Database for LLM Agents**

Niwa (庭, "garden") is a CLI tool that enables multiple LLM agents to collaboratively edit markdown documents with automatic conflict detection and resolution. Like a zen garden where gravel is raked into patterns, Niwa helps agents weave their edits together harmoniously.

Built on LMDB for high-performance concurrent access.

## Features

- **Concurrent Editing**: Multiple agents can read/edit simultaneously
- **Conflict Detection**: Automatic version tracking detects when agents edit the same section
- **Smart Merging**: Auto-merge suggestions for compatible changes, detailed diffs for conflicts
- **Sub-Agent Support**: Stored conflicts survive context switches, status commands for fresh context
- **Version History**: Full audit trail with rollback capability
- **Search**: Find content by keyword when you don't know the node ID
- **LLM-Friendly**: Comprehensive error messages with usage guides
- **Claude Code Integration**: Hooks to inject usage context on session start and compaction

## Installation

```bash
# From PyPI (when published)
pip install niwa

# Or from source
git clone https://github.com/secemp9/niwa
cd niwa
pip install .

# For development (editable install)
pip install -e .

# Or with uv
uv pip install .
```

## Quick Start

```bash
# 1. Initialize database
niwa init

# 2. Load a markdown file
niwa load document.md

# 3. View structure
niwa tree

# 4. Read a section (as an agent)
niwa read h2_3 --agent claude_1

# 5. Edit
niwa edit h2_3 "New content" --agent claude_1 --summary "Updated section"

# 6. Export back to markdown
niwa export > updated.md
```

## Commands

### Setup
| Command | Description |
|---------|-------------|
| `init` | Initialize a new database |
| `load <file>` | Load markdown file into database |
| `check` | Verify database health |
| `setup claude` | Set up Claude Code hooks integration |
| `setup claude --remove` | Remove Claude Code hooks |

### Browse
| Command | Description |
|---------|-------------|
| `tree` | Show document structure with node IDs |
| `peek <id>` | Quick view (doesn't track read version) |
| `search <query>` | Find content by keyword |

### Edit
| Command | Description |
|---------|-------------|
| `read <id> --agent <name>` | Read for editing (tracks version) |
| `edit <id> <content> --agent <name>` | Edit a node |
| `resolve <id> <resolution> --agent <name>` | Resolve a conflict |
| `title <id> <title>` | Update node title |
| `summarize <id> <summary>` | Add summary to node |

### Agent Status
| Command | Description |
|---------|-------------|
| `status --agent <name>` | Check pending reads, conflicts, recent edits |
| `conflicts --agent <name>` | List unresolved conflicts |
| `agents` | List all agents who've used this DB |
| `whoami` | Get suggested unique agent name |

### History & Undo
| Command | Description |
|---------|-------------|
| `history <id>` | View version history |
| `rollback <id> <version> --agent <name>` | Restore to previous version |
| `dry-run <id> <content> --agent <name>` | Preview edit without applying |
| `cleanup` | Remove stale pending reads/conflicts |

### Output
| Command | Description |
|---------|-------------|
| `export` | Export database to markdown |
| `help` | Show full usage guide |

## Multi-Agent Workflow

```
Agent A: reads section (v1)          Agent B: reads section (v1)
    │                                     │
    ▼                                     ▼
Agent A: edits                       Agent B: edits
    │                                     │
    ▼                                     ▼
SUCCESS (v1→v2)                      CONFLICT! (expected v1, found v2)
                                          │
                                          ▼
                                     Agent B resolves with MANUAL_MERGE
                                          │
                                          ▼
                                     SUCCESS (v2→v3) - both changes preserved!
```

## Conflict Resolution

When a conflict is detected, you get 4 options:

```bash
# Use your version (discard theirs)
niwa resolve h2_3 ACCEPT_YOURS --agent me

# Use their version (discard yours)
niwa resolve h2_3 ACCEPT_THEIRS --agent me

# Use auto-merge suggestion
niwa resolve h2_3 ACCEPT_AUTO_MERGE --agent me

# Manual merge (recommended!)
niwa resolve h2_3 MANUAL_MERGE "merged content" --agent me
niwa resolve h2_3 MANUAL_MERGE --file merged.md --agent me
```

## Complex Content

For content with quotes, newlines, or special characters, use `--file` or `--stdin`:

```bash
# Edit from file
niwa edit h2_3 --file content.txt --agent me

# Edit from stdin
cat content.md | niwa edit h2_3 --stdin --agent me

# Resolve from file
niwa resolve h2_3 MANUAL_MERGE --file merged.md --agent me
```

## Sub-Agents / Fresh Context

If you're a sub-agent or have fresh context, run these first:

```bash
# Get a unique agent name
niwa whoami

# Check your state
niwa status --agent <your_name>

# Check for pending conflicts
niwa conflicts --agent <your_name>
```

**Key rules:**
- Use a **unique** agent name (check with `agents` command)
- Use the **same** name for all your reads/edits
- Conflicts are **stored** and survive context switches

## Node IDs

Nodes follow the pattern `h{level}_{index}`:

```
[root] v1 "Document"
  [h1_0] v1 "Main Title"
    [h2_1] v3 "Section 1" (by agent_A)     ← node_id is "h2_1"
    [h2_2] v1 "Section 2"
      [h3_3] v2 "Subsection" (by agent_B)  ← node_id is "h3_3"
```

Use `tree` to find node IDs, or `search` to find by content.

## Examples

### Basic editing workflow
```bash
niwa init
niwa load README.md
niwa tree
niwa read h2_1 --agent editor
niwa edit h2_1 "# Updated Heading\n\nNew content here." \
    --agent editor --summary "Rewrote introduction"
niwa export > README_updated.md
```

### Handling a conflict
```bash
# You tried to edit but got a conflict
niwa edit h2_3 "My changes" --agent me
# Output: CONFLICT DETECTED!

# View the diff, then resolve
niwa resolve h2_3 MANUAL_MERGE \
    "Combined content from both versions" --agent me
```

### Finding and editing content
```bash
# Don't know the node ID? Search for it
niwa search "authentication"
# Output: [h2_5] "Authentication" - 3 matches

# Read and edit
niwa read h2_5 --agent me
niwa edit h2_5 --file auth_rewrite.md --agent me
```

### Rollback a bad edit
```bash
# Check history
niwa history h2_3
# Output: v3, v2, v1...

# Rollback to v2
niwa rollback h2_3 2 --agent me
```

## Claude Code Integration

Niwa integrates with Claude Code via hooks for automatic context awareness:

```bash
# Set up Claude Code hooks
niwa setup claude

# Remove hooks later
niwa setup claude --remove
```

### What the hooks do

| Hook | Trigger | Action |
|------|---------|--------|
| **SessionStart** | Session begins | Injects full Niwa usage guide + current status |
| **PreCompact** | Before `/compact` | Preserves Niwa context so Claude remembers after compaction |
| **PreToolUse** | Before Write/Edit | Warns if there are unresolved conflicts |
| **PostToolUse** | After Write/Edit | Hints to sync markdown changes to database |
| **Stop** | Session ending | Reminds about unresolved conflicts |

The SessionStart and PreCompact hooks ensure Claude always knows how to use Niwa, even after context compaction.

### How it works

1. Run `setup claude` in your project directory
2. Creates `.claude/settings.json` with hook configuration
3. Hooks automatically call `niwa.py hook --hook-event <event>`
4. Claude receives context about your markdown database state

### Example workflow with Claude Code

```bash
# 1. Initialize and set up hooks
niwa init
niwa load design_doc.md
niwa setup claude

# 2. Start Claude Code session
# Claude will see: "[Niwa Status] Database active with 15 nodes..."

# 3. When Claude edits markdown files
# Claude will see: "[Niwa] Markdown file modified... Consider syncing"

# 4. If conflicts exist when stopping
# Claude will see: "[Niwa Reminder] There are 2 unresolved conflict(s)..."
```

## Architecture

- **Storage**: LMDB (Lightning Memory-Mapped Database) for fast concurrent access
- **Versioning**: Each edit increments version, full history stored
- **Conflict Detection**: Compares read version vs current version at edit time
- **Merge Analysis**: Diff-based overlap detection with auto-merge for compatible changes
- **Hook Integration**: Claude Code hooks for automatic context awareness

## Name

Niwa (庭) means "garden" in Japanese. Like a zen garden where gravel (砂利, jari) is raked into patterns, Niwa helps organize your plans and specs with deliberate structure. Multiple agents can collaboratively tend the same garden without disturbing each other's patterns.

## License

MIT
