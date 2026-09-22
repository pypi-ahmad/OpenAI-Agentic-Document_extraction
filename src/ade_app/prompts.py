"""Load and render version-controlled Markdown prompt templates."""

from __future__ import annotations

from pathlib import Path

PROMPT_DIRECTORY = Path(__file__).with_name("prompts")


def load_prompt(name: str) -> str:
    """Load one non-empty Markdown prompt by its fixed application name."""
    if not name or Path(name).name != name or not name.endswith(".md"):
        raise ValueError("prompt name must be a plain Markdown filename")
    prompt = (PROMPT_DIRECTORY / name).read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"prompt is empty: {name}")
    return prompt


def render_prompt(name: str, **values: object) -> str:
    """Render trusted application values into a Markdown prompt template."""
    try:
        return load_prompt(name).format_map(values)
    except KeyError as error:
        raise ValueError(f"prompt value is missing: {error.args[0]}") from error


def page_request_prompt(*, has_previous: bool, has_next: bool, **values: object) -> str:
    """Select the template that exactly describes the attached image sequence."""
    suffix = (
        "previous_next"
        if has_previous and has_next
        else "previous"
        if has_previous
        else "next"
        if has_next
        else "target"
    )
    return render_prompt(f"page_request_{suffix}.md", **values)
