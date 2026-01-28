"""
Niwa 庭 - Collaborative Markdown Database for LLM Agents

A zen garden for your plans and specs. Multiple LLM agents can collaboratively
edit markdown documents with automatic conflict detection and resolution.
"""

__version__ = "0.1.0"

from .cli import Niwa, ConflictType, ConflictAnalysis

__all__ = ["Niwa", "ConflictType", "ConflictAnalysis", "__version__"]
