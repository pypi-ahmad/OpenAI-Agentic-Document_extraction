from ade_app.preview import safe_markdown_preview


def test_preview_allows_renderer_tables_but_escapes_document_html() -> None:
    markdown = (
        '<table>\n<tr><td colspan="2">**Safe**</td></tr>\n</table>\n<img src=x onerror="alert(1)">'
    )

    preview = safe_markdown_preview(markdown)

    assert '<table>\n<tr><td colspan="2"><strong>Safe</strong></td></tr>\n</table>' in preview
    assert '&lt;img src=x onerror="alert(1)"&gt;' in preview


def test_preview_keeps_known_document_comments_hidden() -> None:
    marker = "<!-- doc_id=parse-01m1er85mf96gbjg7dvfhz5j83 -->"

    assert safe_markdown_preview(marker) == marker


def test_preview_does_not_load_document_supplied_markdown_urls() -> None:
    preview = safe_markdown_preview(
        "![tracking](https://attacker.example/pixel) [unsafe](javascript:alert(1))"
    )

    assert preview == "[image: tracking] unsafe"
    assert "https://" not in preview
    assert "javascript:" not in preview


def test_preview_neutralizes_active_and_malformed_markup() -> None:
    payload = (
        '<svg onload="alert(1)"><script>alert(2)</script></svg>'
        '<table onclick="alert(3)"><tr><td style="background:url(javascript:x)">x</td></tr>'
        '</table><a href="&#x6a;avascript:alert(4)">link</a>'
    )

    preview = safe_markdown_preview(payload)

    assert "<script" not in preview
    assert "<svg" not in preview
    assert "<table onclick=" not in preview
    assert "<td style=" not in preview
    assert "<a " not in preview
    assert "&lt;script" in preview
