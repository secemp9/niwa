"""niwa.niwa - Auto-split module"""

import json
import time
import difflib
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
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
import re
import uuid

from .models import ConflictAnalysis, ConflictType, EditResult


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
