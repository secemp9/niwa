"""niwa.command - Auto-split module"""


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


def print_command_help(command: str):
    """Print detailed help for a specific command."""
    if command in COMMAND_HELP:
        print(COMMAND_HELP[command])
    else:
        print(f"No detailed help for '{command}'. Run 'niwa help' for full guide.")
