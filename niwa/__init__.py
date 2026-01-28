"""niwa - Auto-split package"""

__version__ = "0.2.0"

from .niwa import Niwa
from .cli import main
from .models import ConflictType, Edit, ConflictAnalysis, EditResult
from .command import COMMAND_HELP, print_command_help
from .core import generate_claude_hooks_config, get_niwa_usage_guide, handle_hook_event, setup_claude_hooks, LLM_SYSTEM_PROMPT, ERROR_PROMPTS, print_error

__all__ = ['Niwa', 'main', 'ConflictType', 'Edit', 'ConflictAnalysis', 'EditResult', 'COMMAND_HELP', 'print_command_help', 'generate_claude_hooks_config', 'get_niwa_usage_guide', 'handle_hook_event', 'setup_claude_hooks', 'LLM_SYSTEM_PROMPT', 'ERROR_PROMPTS', 'print_error', '__version__']
