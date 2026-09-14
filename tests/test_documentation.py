import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
MARKDOWN_FILES = (ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md")))
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
PYTHON_FENCE = re.compile(r"^```python\s*\n(.*?)^```", re.MULTILINE | re.DOTALL)


def test_relative_markdown_links_resolve() -> None:
    broken: list[str] = []
    for document in MARKDOWN_FILES:
        for target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            path = target.split("#", 1)[0].strip()
            if not path or "://" in path or path.startswith("mailto:"):
                continue
            if not (document.parent / path).resolve().exists():
                broken.append(f"{document.relative_to(ROOT)} -> {target}")
    assert not broken, "broken documentation links:\n" + "\n".join(broken)


def test_python_examples_are_syntactically_valid() -> None:
    for document in MARKDOWN_FILES:
        text = document.read_text(encoding="utf-8")
        for index, example in enumerate(PYTHON_FENCE.findall(text), start=1):
            compile(example, f"{document.relative_to(ROOT)}:example-{index}", "exec")
