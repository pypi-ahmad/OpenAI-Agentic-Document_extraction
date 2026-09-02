"""Safe rendering adapter for GroundTruth-style mixed Markdown and HTML."""

from __future__ import annotations

import re

_ALLOWED_TAG = re.compile(
    r"<!-- (?:PAGE BREAK|doc_id=parse-[0-9a-hjkmnp-tv-z]{26}) -->"
    r"|</?(?:table|tr|td|figure|description|strong|em)>"
    r"|<br>"
    r'|<td(?: (?:colspan|rowspan)="[1-9]\d*"){1,2}>'
    r'|<figure type="[^"<>]*">'
)
_TABLE = re.compile(r"<table>.*?</table>", re.DOTALL)
_STRONG = re.compile(r"\*\*([^*<>\n]+)\*\*")
_EMPHASIS = re.compile(r"(?<!\*)\*([^*<>\n]+)\*(?!\*)")
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]\n]*)\]\((?:[^()\n]|\([^()\n]*\))*\)")
_MARKDOWN_LINK = re.compile(r"\[([^\]\n]*)\]\((?:[^()\n]|\([^()\n]*\))*\)")


def safe_markdown_preview(markdown: str) -> str:
    """Allow renderer-owned HTML tags and neutralize document-supplied tags."""

    markdown = _MARKDOWN_IMAGE.sub(r"[image: \1]", markdown)
    markdown = _MARKDOWN_LINK.sub(r"\1", markdown)
    markdown = _TABLE.sub(lambda match: _html_inline_styles(match.group()), markdown)
    parts: list[str] = []
    cursor = 0
    for match in _ALLOWED_TAG.finditer(markdown):
        parts.append(_escape_angles(markdown[cursor : match.start()]))
        parts.append(match.group())
        cursor = match.end()
    parts.append(_escape_angles(markdown[cursor:]))
    return "".join(parts)


def _escape_angles(value: str) -> str:
    return value.replace("<", "&lt;").replace(">", "&gt;")


def _html_inline_styles(value: str) -> str:
    value = _STRONG.sub(r"<strong>\1</strong>", value)
    return _EMPHASIS.sub(r"<em>\1</em>", value)
