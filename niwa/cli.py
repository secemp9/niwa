"""niwa.cli - Auto-split module"""

import os
from pathlib import Path
import sys
import argparse
from datetime import datetime

from . import __version__
from .command import print_command_help
from .core import LLM_SYSTEM_PROMPT, handle_hook_event, print_error, setup_claude_hooks
from .models import ConflictAnalysis, ConflictType
from .niwa import Niwa


def main():

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
    parser.add_argument('-v', '--version', action='version', version=f'niwa {__version__}')
    parser.add_argument('command', nargs='?', default='help', metavar='COMMAND',
                       help='Command to run (see commands below)')
    parser.add_argument('args', nargs='*', help='Command arguments')
    parser.add_argument('--agent', default='default_agent', help='Agent ID (use a unique name!)')
    parser.add_argument('--summary', default=None, help='Edit summary (helps with conflict resolution)')
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
                node_id, content, args.agent, args.summary
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

            # Look up the stored conflict for this agent+node so ACCEPT_YOURS
            # and ACCEPT_AUTO_MERGE have access to the conflict data.
            stored_conflict = None
            stored_conflicts = db.get_pending_conflicts(args.agent)
            for sc in stored_conflicts:
                if sc.get('node_id') == node_id:
                    stored_conflict = ConflictAnalysis(
                        conflict_type=ConflictType.TRUE_CONFLICT,
                        node_id=sc['node_id'],
                        node_title=sc.get('node_title', ''),
                        your_base_version=sc.get('your_base_version', 0),
                        current_version=sc.get('current_version', 0),
                        concurrent_edits_count=0,
                        original_content='',
                        your_content=sc.get('your_content', ''),
                        current_content=sc.get('current_content', ''),
                        your_changes=[],
                        their_changes=[],
                        overlapping_regions=[],
                        your_agent_id=args.agent,
                        other_agents=[],
                        their_edit_summaries=[],
                        auto_merge_possible=sc.get('auto_merge_possible', False),
                        auto_merged_content=sc.get('auto_merged_content'),
                    )
                    break

            result = db.resolve_conflict(
                node_id, resolution, args.agent,
                manual_content=manual_content,
                conflict=stored_conflict,
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
                    ts = datetime.fromtimestamp(h['timestamp']).strftime('%Y-%m-%d %H:%M:%S') if h.get('timestamp') else '?'
                    has_content = "✓ can rollback" if h.get('has_content') else "✗ no content stored"
                    preview = h.get('content_preview', '')[:60].replace('\n', ' ') + '...' if h.get('content_preview') else ''
                    print(f"""
┌──────────────────────────────────────────────────────────────────────────────┐
│ Version {h['version']} - {ts}
│ Agent: {h.get('agent', '?')}
│ Summary: {(h.get('summary') or '(none)')[:60]}
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

            # Apply the rollback via internal force edit (bypasses conflict detection)
            result = db._force_edit(
                node_id, old_content, args.agent,
                f"Rollback to v{version}",
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

Re-read to get the latest version, then edit:
  niwa read {node_id} --agent {args.agent}
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
