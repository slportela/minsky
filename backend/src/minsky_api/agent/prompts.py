"""Load versioned Jinja2 prompts from the repo `prompts/` directory (AGENTS.md)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def _prompts_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "prompts"
        if candidate.is_dir() and (candidate / "agent.extract.j2").exists():
            return candidate
    raise RuntimeError("prompts/ directory with agent templates not found")


@lru_cache
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_prompts_dir()),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render(name: str, **kwargs: object) -> str:
    """Render `prompts/<name>` and strip surrounding whitespace."""
    return _env().get_template(name).render(**kwargs).strip()
