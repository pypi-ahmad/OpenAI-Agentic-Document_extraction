from pathlib import Path

import pytest

from ade_app.prompts import (
    PROMPT_DIRECTORY,
    load_prompt,
    page_request_prompt,
    render_prompt,
)

EXPECTED_PROMPTS = {
    "page_parse.md",
    "page_request_target.md",
    "page_request_previous.md",
    "page_request_next.md",
    "page_request_previous_next.md",
    "crop_read.md",
    "crop_request.md",
    "page_markdown.md",
    "page_markdown_request.md",
}


def test_all_runtime_prompts_are_markdown_files() -> None:
    assert {path.name for path in PROMPT_DIRECTORY.iterdir() if path.is_file()} == EXPECTED_PROMPTS
    assert all(load_prompt(name) for name in EXPECTED_PROMPTS)


@pytest.mark.parametrize(
    ("previous", "next_page", "image_count"),
    [(False, False, 1), (True, False, 2), (False, True, 2), (True, True, 3)],
)
def test_page_request_variants_match_attached_image_count(
    previous: bool, next_page: bool, image_count: int
) -> None:
    prompt = page_request_prompt(
        has_previous=previous,
        has_next=next_page,
        target_page=4,
        selected_page_count=9,
    )
    assert "page 4 of 9" in prompt
    assert prompt.count("**") == image_count * 2
    assert "{" not in prompt


def test_crop_template_requires_all_values() -> None:
    with pytest.raises(ValueError, match="prompt value is missing"):
        render_prompt("crop_request.md", source_page=1)


def test_prompt_loader_rejects_paths() -> None:
    with pytest.raises(ValueError, match="plain Markdown filename"):
        load_prompt(str(Path("other") / "prompt.md"))
