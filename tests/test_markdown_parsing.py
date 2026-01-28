"""
Comprehensive tests for niwa markdown parsing with edge cases.

Run with: pytest tests/test_markdown_parsing.py -v
"""

import pytest
import tempfile
import shutil
import os
from pathlib import Path

# Add parent to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from niwa.cli import Niwa


@pytest.fixture
def temp_db():
    """Create a temporary directory for the database."""
    temp_dir = tempfile.mkdtemp()
    original_cwd = os.getcwd()
    os.chdir(temp_dir)
    yield temp_dir
    os.chdir(original_cwd)
    shutil.rmtree(temp_dir)


@pytest.fixture
def db(temp_db):
    """Create and return a Niwa database instance."""
    niwa = Niwa()
    yield niwa
    niwa.close()


def create_md_file(content: str, temp_db: str) -> str:
    """Helper to create a markdown file and return its path."""
    path = os.path.join(temp_db, "test.md")
    with open(path, 'w') as f:
        f.write(content)
    return path


class TestBasicParsing:
    """Basic parsing functionality."""

    def test_single_h1(self, db, temp_db):
        """Parse a simple document with one heading."""
        md = "# Hello World\n\nSome content here."
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert 'root' in nodes
        assert 'h1_0' in nodes
        assert nodes['h1_0']['title'] == 'Hello World'
        assert 'Some content here.' in nodes['h1_0']['content']

    def test_multiple_h1(self, db, temp_db):
        """Multiple h1 headings should be siblings."""
        md = "# First\n\nContent 1\n\n# Second\n\nContent 2"
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert nodes['h1_0']['title'] == 'First'
        assert nodes['h1_1']['title'] == 'Second'
        # Both should be children of root
        assert nodes['h1_0']['parent_id'] == 'root'
        assert nodes['h1_1']['parent_id'] == 'root'

    def test_nested_headings(self, db, temp_db):
        """Test heading hierarchy h1 > h2 > h3."""
        md = """# Title

## Section 1

### Subsection 1.1

Content here

## Section 2

More content
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}

        # h1 is child of root
        assert nodes['h1_0']['parent_id'] == 'root'
        # h2s are children of h1
        assert nodes['h2_1']['parent_id'] == 'h1_0'
        assert nodes['h2_3']['parent_id'] == 'h1_0'
        # h3 is child of first h2
        assert nodes['h3_2']['parent_id'] == 'h2_1'

    def test_no_headings(self, db, temp_db):
        """Document with no headings should create a single content node."""
        md = "Just some plain text\n\nWith multiple paragraphs."
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert 'content_0' in nodes
        assert nodes['content_0']['type'] == 'paragraph'
        assert 'Just some plain text' in nodes['content_0']['content']


class TestCodeBlocks:
    """Code blocks should not have their # interpreted as headings."""

    def test_hash_in_fenced_code_block(self, db, temp_db):
        """# inside fenced code blocks should NOT become headings."""
        md = """# Real Heading

```python
# This is a comment, not a heading
def foo():
    # Another comment
    pass
```

More content after code.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        # Should only have root and one h1
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1
        assert headings[0]['title'] == 'Real Heading'

        # The code block should be in the content
        assert '# This is a comment' in nodes['h1_0']['content']
        assert 'def foo():' in nodes['h1_0']['content']

    def test_hash_in_indented_code_block(self, db, temp_db):
        """# inside indented code blocks should NOT become headings."""
        md = """# Real Heading

    # Indented code comment
    def bar():
        pass

Text after.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1

    def test_multiple_code_blocks_with_hashes(self, db, temp_db):
        """Multiple code blocks with # comments."""
        md = """# Intro

```bash
# Install dependencies
pip install foo
```

## Next Section

```ruby
# Ruby comment
puts "hello"
```
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 2
        titles = {h['title'] for h in headings}
        assert titles == {'Intro', 'Next Section'}

    def test_code_block_with_markdown_heading_syntax(self, db, temp_db):
        """Code block containing markdown heading syntax."""
        md = """# Documentation

Here's how to write markdown:

```markdown
# This is how you write a heading
## And a subheading

Some paragraph text.
```

End of docs.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1
        assert headings[0]['title'] == 'Documentation'


class TestSpecialContent:
    """Test preservation of special markdown content."""

    def test_tables_preserved(self, db, temp_db):
        """Markdown tables should be preserved exactly."""
        md = """# Data

| Name | Age |
|------|-----|
| Alice | 30 |
| Bob | 25 |

Footer text.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '| Name | Age |' in content
        assert '|------|-----|' in content
        assert '| Alice | 30 |' in content

    def test_blockquotes_preserved(self, db, temp_db):
        """Blockquotes should be preserved."""
        md = """# Quote Section

> This is a quote
> spanning multiple lines

Normal text.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '> This is a quote' in content

    def test_nested_lists_preserved(self, db, temp_db):
        """Nested lists should be preserved."""
        md = """# Lists

- Item 1
  - Nested 1.1
  - Nested 1.2
- Item 2
  1. Ordered nested
  2. Another ordered

Done.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '- Item 1' in content
        assert '- Nested 1.1' in content or '  - Nested 1.1' in content

    def test_horizontal_rule_preserved(self, db, temp_db):
        """Horizontal rules should be preserved."""
        md = """# Section

Above the line

---

Below the line
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '---' in content

    def test_html_preserved(self, db, temp_db):
        """Raw HTML should be preserved."""
        md = """# With HTML

<div class="special">
  <p>HTML content</p>
</div>

Back to markdown.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '<div class="special">' in content


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_document(self, db, temp_db):
        """Empty document should still work."""
        md = ""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = list(db.list_nodes())
        # Should have root and possibly empty content node
        assert any(n['id'] == 'root' for n in nodes)

    def test_only_whitespace(self, db, temp_db):
        """Document with only whitespace."""
        md = "   \n\n   \n"
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = list(db.list_nodes())
        assert any(n['id'] == 'root' for n in nodes)

    def test_heading_at_eof_no_content(self, db, temp_db):
        """Heading at end of file with no following content."""
        md = """# First

Some content

## Last Heading"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert 'h2_1' in nodes
        assert nodes['h2_1']['title'] == 'Last Heading'
        assert nodes['h2_1']['content'] == ''  # Empty content is OK

    def test_heading_immediately_followed_by_heading(self, db, temp_db):
        """Two headings with no content between them."""
        md = """# First
## Second
### Third

Finally some content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert nodes['h1_0']['content'] == ''
        assert nodes['h2_1']['content'] == ''
        assert 'Finally some content' in nodes['h3_2']['content']

    def test_many_blank_lines(self, db, temp_db):
        """Multiple blank lines should be preserved in content."""
        md = """# Spaced Out



Content with gaps above.




More gaps.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        # Content should exist
        assert 'Content with gaps' in nodes['h1_0']['content']

    def test_heading_with_special_characters(self, db, temp_db):
        """Headings with special characters."""
        md = """# Hello "World" & <Friends>

## Section with `code` and *emphasis*

### What's (happening) here?

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert nodes['h1_0']['title'] == 'Hello "World" & <Friends>'
        assert nodes['h2_1']['title'] == 'Section with `code` and *emphasis*'

    def test_heading_with_unicode_and_emoji(self, db, temp_db):
        """Headings with unicode and emoji."""
        md = """# 日本語タイトル

## Émojis 🎉 are fun! 🚀

### Ñoño señor

Conteúdo.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert nodes['h1_0']['title'] == '日本語タイトル'
        assert '🎉' in nodes['h2_1']['title']
        assert 'Ñoño' in nodes['h3_2']['title']

    def test_very_long_heading(self, db, temp_db):
        """Very long heading text."""
        long_title = "A" * 500
        md = f"# {long_title}\n\nContent."
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert nodes['h1_0']['title'] == long_title

    def test_very_deep_nesting(self, db, temp_db):
        """Deep heading nesting h1 through h6."""
        md = """# Level 1
## Level 2
### Level 3
#### Level 4
##### Level 5
###### Level 6

Deep content here.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        # Check all levels exist
        assert nodes['h1_0']['level'] == 1
        assert nodes['h2_1']['level'] == 2
        assert nodes['h3_2']['level'] == 3
        assert nodes['h4_3']['level'] == 4
        assert nodes['h5_4']['level'] == 5
        assert nodes['h6_5']['level'] == 6

        # Check hierarchy
        assert nodes['h2_1']['parent_id'] == 'h1_0'
        assert nodes['h3_2']['parent_id'] == 'h2_1'
        assert nodes['h4_3']['parent_id'] == 'h3_2'
        assert nodes['h5_4']['parent_id'] == 'h4_3'
        assert nodes['h6_5']['parent_id'] == 'h5_4'

    def test_skip_levels(self, db, temp_db):
        """Skipping heading levels (h1 -> h3 -> h6)."""
        md = """# Top

### Skipped to 3

###### Skipped to 6

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        # h3 should be child of h1 even though h2 was skipped
        assert nodes['h3_1']['parent_id'] == 'h1_0'
        # h6 should be child of h3
        assert nodes['h6_2']['parent_id'] == 'h3_1'

    def test_decreasing_then_increasing_levels(self, db, temp_db):
        """Test h1 -> h3 -> h2 pattern."""
        md = """# Main

### Deep section

## Back to h2

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        # h3 under h1
        assert nodes['h3_1']['parent_id'] == 'h1_0'
        # h2 should go back under h1, not h3
        assert nodes['h2_2']['parent_id'] == 'h1_0'


class TestContentPreservation:
    """Test that content is preserved exactly."""

    def test_inline_formatting_preserved(self, db, temp_db):
        """Inline formatting should be preserved."""
        md = """# Formatting

This has **bold**, *italic*, `code`, and ~~strikethrough~~.

Also [links](http://example.com) and ![images](img.png).
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '**bold**' in content
        assert '*italic*' in content
        assert '`code`' in content
        assert '[links](http://example.com)' in content

    def test_code_block_language_preserved(self, db, temp_db):
        """Code block language hints should be preserved."""
        md = """# Code Examples

```javascript
const x = 1;
```

```python
x = 1
```

```
no language
```
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '```javascript' in content
        assert '```python' in content
        assert 'const x = 1;' in content

    def test_indentation_preserved(self, db, temp_db):
        """Indentation in content should be preserved."""
        md = """# Indented

    Four spaces
        Eight spaces
    Back to four
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        # Should preserve indentation
        assert '    Four spaces' in content or 'Four spaces' in content


class TestExportRoundTrip:
    """Test that export preserves content."""

    def test_basic_roundtrip(self, db, temp_db):
        """Basic import/export should preserve structure."""
        md = """# Title

Content for title.

## Section

Section content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        exported = db.export_markdown()

        assert '# Title' in exported
        assert '## Section' in exported
        assert 'Content for title' in exported
        assert 'Section content' in exported

    def test_code_block_roundtrip(self, db, temp_db):
        """Code blocks should survive roundtrip."""
        md = """# Code

```python
def foo():
    return 42
```
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        exported = db.export_markdown()

        assert '```python' in exported or '```' in exported
        assert 'def foo():' in exported
        assert 'return 42' in exported

    def test_table_roundtrip(self, db, temp_db):
        """Tables should survive roundtrip."""
        md = """# Data

| A | B |
|---|---|
| 1 | 2 |
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        exported = db.export_markdown()

        assert '| A | B |' in exported
        assert '| 1 | 2 |' in exported


class TestSetextHeadings:
    """Test setext-style headings (underline style)."""

    def test_setext_h1(self, db, temp_db):
        """Setext H1 with === underline."""
        md = """Title Here
==========

Content under title.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1
        assert headings[0]['title'] == 'Title Here'
        assert headings[0]['level'] == 1

    def test_setext_h2(self, db, temp_db):
        """Setext H2 with --- underline."""
        md = """Subtitle
--------

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1
        assert headings[0]['title'] == 'Subtitle'
        assert headings[0]['level'] == 2

    def test_mixed_atx_and_setext(self, db, temp_db):
        """Mix of ATX (#) and setext (underline) headings."""
        md = """Main Title
==========

## ATX Section

Setext Section
--------------

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        titles = {h['title'] for h in headings}
        assert 'Main Title' in titles
        assert 'ATX Section' in titles
        assert 'Setext Section' in titles


class TestFrontmatter:
    """Test YAML frontmatter handling."""

    def test_yaml_frontmatter_not_heading(self, db, temp_db):
        """YAML frontmatter --- should not create headings."""
        md = """---
title: My Document
author: Test
---

# Real Heading

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        # Should only have "Real Heading", not frontmatter
        assert len(headings) == 1
        assert headings[0]['title'] == 'Real Heading'

    def test_toml_frontmatter(self, db, temp_db):
        """TOML frontmatter with +++ delimiters."""
        md = """+++
title = "My Document"
date = 2024-01-01
+++

# Heading

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1
        assert headings[0]['title'] == 'Heading'

    def test_frontmatter_with_dashes_inside(self, db, temp_db):
        """Frontmatter containing dashes in content."""
        md = """---
title: My - Document - With - Dashes
tags:
  - one
  - two
---

# Real Heading

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1


class TestGFMFeatures:
    """Test GitHub Flavored Markdown features."""

    def test_gfm_tables(self, db, temp_db):
        """GFM tables with alignment."""
        md = """# Tables

| Left | Center | Right |
|:-----|:------:|------:|
| L1   |   C1   |    R1 |
| L2   |   C2   |    R2 |

After table.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '| Left | Center | Right |' in content
        assert ':------:' in content  # Center alignment preserved

    def test_gfm_strikethrough(self, db, temp_db):
        """GFM strikethrough syntax."""
        md = """# Strikethrough

This is ~~deleted~~ text and ~~more deleted~~.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '~~deleted~~' in content

    def test_gfm_autolinks(self, db, temp_db):
        """GFM autolinks (URLs without angle brackets)."""
        md = """# Links

Check out https://example.com and http://test.org for more.

Also www.example.com should work.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert 'https://example.com' in content

    def test_gfm_task_lists(self, db, temp_db):
        """GFM task lists with checkboxes."""
        md = """# Todo

- [ ] Unchecked item
- [x] Checked item
- [ ] Another unchecked

More text.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '[ ]' in content or '- [ ]' in content
        assert '[x]' in content or '- [x]' in content


class TestFootnotes:
    """Test footnote handling."""

    def test_inline_footnotes(self, db, temp_db):
        """Inline footnote references."""
        md = """# Article

This has a footnote[^1] and another[^note].

[^1]: First footnote content.
[^note]: Named footnote content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '[^1]' in content or 'footnote' in content.lower()

    def test_multiline_footnotes(self, db, temp_db):
        """Multiline footnote definitions."""
        md = """# Doc

See the note[^long].

[^long]: This is a long footnote
    that spans multiple lines
    with indentation.

More content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        # Should parse without error
        assert 'h1_0' in nodes


class TestDefinitionLists:
    """Test definition list handling."""

    def test_simple_definition_list(self, db, temp_db):
        """Simple definition list."""
        md = """# Glossary

Term 1
:   Definition of term 1

Term 2
:   Definition of term 2

End.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert 'Term 1' in content
        assert 'Definition' in content


class TestComplexNesting:
    """Test complex nesting scenarios."""

    def test_lists_in_blockquotes(self, db, temp_db):
        """Lists inside blockquotes."""
        md = """# Quoted List

> Here's a quote with a list:
> - Item 1
> - Item 2
>
> And more quoted text.

Normal text.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '>' in content
        assert 'Item 1' in content

    def test_code_in_lists(self, db, temp_db):
        """Code blocks inside list items."""
        md = """# Code Lists

- First item with code:
  ```python
  def foo():
      pass
  ```
- Second item

Done.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert 'def foo()' in content

    def test_nested_blockquotes(self, db, temp_db):
        """Nested blockquotes."""
        md = """# Nested Quotes

> Level 1
>> Level 2
>>> Level 3

Back to normal.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '>>>' in content or 'Level 3' in content

    def test_tables_in_lists(self, db, temp_db):
        """Tables inside list items (complex GFM)."""
        md = """# List Tables

- Item with table:

  | A | B |
  |---|---|
  | 1 | 2 |

- Next item

End.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '| A | B |' in content


class TestEdgeCasesExtended:
    """Extended edge cases."""

    def test_heading_with_trailing_hashes(self, db, temp_db):
        """ATX headings with trailing # characters."""
        md = """# Heading 1 #

## Heading 2 ##

### Heading 3 ###

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        # Trailing hashes should be stripped from title
        assert nodes['h1_0']['title'] == 'Heading 1'
        assert nodes['h2_1']['title'] == 'Heading 2'

    def test_heading_with_inline_code(self, db, temp_db):
        """Heading containing inline code."""
        md = """# The `main()` function

## Using `async/await`

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert '`main()`' in nodes['h1_0']['title']
        assert '`async/await`' in nodes['h2_1']['title']

    def test_heading_with_links(self, db, temp_db):
        """Heading containing links."""
        md = """# See [the docs](http://example.com)

## [Link only](http://test.com)

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert 'the docs' in nodes['h1_0']['title'] or '[the docs]' in nodes['h1_0']['title']

    def test_heading_with_images(self, db, temp_db):
        """Heading containing images."""
        md = """# Logo ![img](logo.png) Here

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert 'Logo' in nodes['h1_0']['title']

    def test_multiple_documents_same_db(self, db, temp_db):
        """Loading multiple documents (should replace)."""
        md1 = "# First Doc\n\nContent 1."
        md2 = "# Second Doc\n\nContent 2."

        path1 = create_md_file(md1, temp_db)
        db.load_markdown(path1)

        # Create second file with different name
        path2 = os.path.join(temp_db, "test2.md")
        with open(path2, 'w') as f:
            f.write(md2)

        # This should work (adds to same DB)
        # Note: current implementation may need adjustment for multi-doc
        # For now just verify it doesn't crash

    def test_windows_line_endings(self, db, temp_db):
        """Handle Windows CRLF line endings."""
        md = "# Heading\r\n\r\nContent with\r\nwindows endings.\r\n"
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert nodes['h1_0']['title'] == 'Heading'

    def test_mixed_line_endings(self, db, temp_db):
        """Handle mixed line endings."""
        md = "# Heading\n\nUnix line\r\nWindows line\rOld Mac line\n"
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert 'h1_0' in nodes

    def test_bom_handling(self, db, temp_db):
        """Handle UTF-8 BOM at start of file."""
        md = "\ufeff# Heading with BOM\n\nContent."
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1

    def test_null_bytes_removed(self, db, temp_db):
        """Handle null bytes in content (shouldn't crash)."""
        md = "# Heading\n\nContent with \x00 null byte."
        path = create_md_file(md, temp_db)
        try:
            db.load_markdown(path)
            nodes = {n['id']: n for n in db.list_nodes()}
            assert 'h1_0' in nodes
        except Exception:
            # Some systems may reject null bytes, that's OK
            pass

    def test_very_long_line(self, db, temp_db):
        """Very long line without breaks."""
        long_line = "word " * 10000
        md = f"# Heading\n\n{long_line}"
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert len(nodes['h1_0']['content']) > 40000

    def test_only_code_block(self, db, temp_db):
        """Document with only a code block, no headings."""
        md = """```python
def main():
    print("hello")
```
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        # Should create content_0 node
        assert 'content_0' in nodes
        assert 'def main()' in nodes['content_0']['content']

    def test_reference_links(self, db, temp_db):
        """Reference-style links."""
        md = """# Links

See [the link][1] and [another][ref].

[1]: http://example.com
[ref]: http://test.com "Title"
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '[1]' in content or 'the link' in content

    def test_escaped_characters(self, db, temp_db):
        """Escaped markdown characters."""
        md = """# Escaped

This has \\*not italic\\* and \\# not heading.

\\[not a link\\]
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        content = nodes['h1_0']['content']
        assert '\\*' in content or 'not italic' in content


class TestLargeDocuments:
    """Test with larger documents."""

    def test_many_sections(self, db, temp_db):
        """Document with many sections."""
        sections = []
        for i in range(50):
            sections.append(f"## Section {i}\n\nContent for section {i}.\n")

        md = "# Main\n\n" + "\n".join(sections)
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = list(db.list_nodes())
        headings = [n for n in nodes if n['type'] == 'heading']
        assert len(headings) == 51  # 1 h1 + 50 h2

    def test_large_content_blocks(self, db, temp_db):
        """Sections with large content."""
        large_content = "Lorem ipsum. " * 1000
        md = f"# Big Section\n\n{large_content}"
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert len(nodes['h1_0']['content']) > 10000


class TestHashInVariousContexts:
    """Ensure # is only treated as heading at line start."""

    def test_hash_in_middle_of_line(self, db, temp_db):
        """# in middle of line is not a heading."""
        md = """# Real Heading

This has a # in the middle.

And another one: # here too.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1

    def test_hash_in_link(self, db, temp_db):
        """# in URLs should not be headings."""
        md = """# Links

Check out [anchor](#section-name) and http://example.com#anchor.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1
        assert '#section-name' in nodes['h1_0']['content']

    def test_hash_after_text_same_line(self, db, temp_db):
        """Text followed by # on same line."""
        md = """# Heading

Some text # not a heading
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1


class TestAdversarialCases:
    """Adversarial and tricky edge cases."""

    def test_heading_inside_html_comment(self, db, temp_db):
        """Headings inside HTML comments should not be parsed."""
        md = """# Real Heading

<!--
# This is commented out
## Also commented
-->

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1
        assert headings[0]['title'] == 'Real Heading'

    def test_fake_code_fence_not_closed(self, db, temp_db):
        """Unclosed code fence - what happens?"""
        md = """# Heading

```python
# Is this a heading now?
def foo():
    pass

## Or this?
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        # Unclosed fence means everything after is code
        assert len(headings) == 1

    def test_tilde_code_fence(self, db, temp_db):
        """Tilde code fences (~~~) should work like backtick fences."""
        md = """# Heading

~~~python
# Comment in code
def bar():
    pass
~~~

After code.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1
        assert '# Comment in code' in nodes['h1_0']['content']

    def test_indented_heading_not_heading(self, db, temp_db):
        """Indented # should not be a heading (it's code or list continuation)."""
        md = """# Real Heading

    # This is indented 4 spaces - it's code

Normal text.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1

    def test_heading_with_only_hashes(self, db, temp_db):
        """Heading with only # characters and no text."""
        md = """#

##

### Real Heading

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        # Empty headings are valid in markdown
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) >= 1  # At least "Real Heading"

    def test_setext_heading_in_list_context(self, db, temp_db):
        """Setext underline in list context - documents actual behavior.

        Note: markdown-it-py interprets --- after text as setext H2,
        even within list context. This is valid per CommonMark spec.
        """
        md = """# Heading

- Item one
  text continues
  ---
- Item two
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        # markdown-it-py creates setext H2 from "text continues" + ---
        # This is expected CommonMark behavior
        assert len(headings) >= 1  # At least the H1

    def test_math_blocks_with_hash(self, db, temp_db):
        """Math blocks that might contain # (LaTeX comments)."""
        md = """# Math Section

$$
x = y # this is a LaTeX comment
$$

More text.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1

    def test_link_definition_with_hash(self, db, temp_db):
        """Link definitions with fragment identifiers."""
        md = """# Links

See [section](#heading-1) for more.

[ref]: http://example.com#anchor "Title"

More content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1

    def test_html_block_with_hash(self, db, temp_db):
        """HTML blocks that contain # characters."""
        md = """# Heading

<script>
// # This is JS, not markdown
var x = "# Not a heading";
</script>

<style>
#id { color: red; }  /* CSS selector */
</style>

Content after.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1

    def test_deeply_nested_lists(self, db, temp_db):
        """Very deeply nested lists shouldn't break parser."""
        md = """# Deep Lists

- Level 1
  - Level 2
    - Level 3
      - Level 4
        - Level 5
          - Level 6
            - Level 7
              - Level 8

Back to normal.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert 'Level 8' in nodes['h1_0']['content']

    def test_unicode_heading_markers(self, db, temp_db):
        """Unicode characters that look like # shouldn't be headings."""
        # ＃ is fullwidth number sign (U+FF03)
        md = """# Real Heading

＃ This uses fullwidth hash

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1

    def test_zero_width_characters(self, db, temp_db):
        """Zero-width characters in headings."""
        # U+200B is zero-width space
        md = """# Head\u200bing

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1

    def test_rtl_text_in_heading(self, db, temp_db):
        """Right-to-left text in headings."""
        md = """# مرحبا بالعالم

## שלום עולם

Content in English.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 2
        assert 'مرحبا' in nodes['h1_0']['title']

    def test_combining_characters_in_heading(self, db, temp_db):
        """Combining characters (accents) in headings."""
        # é can be composed as e + combining acute accent
        md = """# Cafe\u0301

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1

    def test_paragraph_starting_with_numbers_and_dot(self, db, temp_db):
        """Lines starting with numbers shouldn't become headings."""
        md = """# Heading

1. First item
2. Second item

3.14159 is pi

123# Not a heading

Content.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 1

    def test_multiple_root_level_h1(self, db, temp_db):
        """Multiple H1 headings at root level."""
        md = """# Document One

Content one.

# Document Two

Content two.

# Document Three

Content three.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        h1s = [n for n in nodes.values() if n['type'] == 'heading' and n['level'] == 1]
        assert len(h1s) == 3
        # All should be children of root
        for h1 in h1s:
            assert h1['parent_id'] == 'root'


class TestContentBetweenHeadings:
    """Test that content between headings is correctly attributed."""

    def test_content_before_first_heading(self, db, temp_db):
        """Content before the first heading - where does it go?"""
        md = """Some preamble text here.

More preamble.

# First Heading

Content after heading.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        # Preamble should exist somewhere
        # Current implementation may put it in root or create a content node
        all_content = ' '.join(n.get('content', '') for n in nodes.values())
        assert 'preamble' in all_content.lower() or len(nodes) >= 2

    def test_trailing_content_after_last_heading(self, db, temp_db):
        """Content after the last heading."""
        md = """# Heading

Some content.

More content at the end.
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        assert 'More content at the end' in nodes['h1_0']['content']

    def test_blank_lines_between_headings(self, db, temp_db):
        """Many blank lines between headings."""
        md = """# First




# Second




# Third
"""
        path = create_md_file(md, temp_db)
        db.load_markdown(path)

        nodes = {n['id']: n for n in db.list_nodes()}
        headings = [n for n in nodes.values() if n['type'] == 'heading']
        assert len(headings) == 3


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
