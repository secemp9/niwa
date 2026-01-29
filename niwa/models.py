"""niwa.models - Auto-split module"""

import difflib
from dataclasses import dataclass
from typing import Optional, List, Dict
from enum import Enum


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

    # Auto-merge (only set when changes are in completely different parts)
    auto_merge_possible: bool
    auto_merged_content: Optional[str] = None

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

        prompt += """### RESOLUTION OPTIONS

1. **ACCEPT_YOURS**: Overwrite with your version (discards their changes)
2. **ACCEPT_THEIRS**: Keep current version (discards your changes)
3. **MANUAL_MERGE**: Provide your own merged content

### Your Response

Please respond with ONE of:
- `ACCEPT_YOURS` - if your changes should take precedence
- `ACCEPT_THEIRS` - if their changes should take precedence
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
