"""Prompt loading utility.

Reads agent system prompts from the co-located ``prompts/`` directory.
Prompts are plain Markdown files — externalized here so they can be
version-tracked, reviewed, and hot-reloaded independently of Python code.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


@cache
def load_prompt(name: str) -> str:
    """Return the contents of ``prompts/<name>.md`` as a string.

    The result is cached after the first read so repeated calls within a
    process pay no I/O cost.

    Args:
        name: Prompt file stem without extension, e.g. ``"sales_agent"``.

    Returns:
        The full text of the prompt file.

    Raises:
        FileNotFoundError: If no matching ``.md`` file exists.
    """
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {path}. "
            f"Available prompts: {[p.stem for p in _PROMPTS_DIR.glob('*.md')]}"
        )
    return path.read_text(encoding="utf-8")
