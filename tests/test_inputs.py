import pytest

from ade_app.inputs import DocumentInput, parse_page_range


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", (1, 2, 3, 4, 5)),
        ("3", (3,)),
        ("1,3-5", (1, 3, 4, 5)),
        (" 1 - 3, 2,5 ", (1, 2, 3, 5)),
    ],
)
def test_parse_page_range(text: str, expected: tuple[int, ...]) -> None:
    assert parse_page_range(text, 5) == expected


@pytest.mark.parametrize("text", ["0", "6", "3-1", "1,,2", "a", "1-2-3", "-1"])
def test_parse_page_range_rejects_invalid_input(text: str) -> None:
    with pytest.raises(ValueError):
        parse_page_range(text, 5)


def test_document_input_rejects_paths_and_unsupported_types() -> None:
    with pytest.raises(ValueError, match="plain file name"):
        DocumentInput(filename="folder/file.pdf", data=b"x")
    with pytest.raises(ValueError, match="unsupported"):
        DocumentInput(filename="file.txt", data=b"x")


def test_document_input_rejects_unsafe_names_and_oversized_content(monkeypatch) -> None:
    with pytest.raises(ValueError, match="unsafe characters"):
        DocumentInput(filename="unsafe\nname.pdf", data=b"x")
    with pytest.raises(ValueError, match="unsafe characters"):
        DocumentInput(filename="unsafe?.pdf", data=b"x")
    monkeypatch.setattr("ade_app.inputs.MAX_DOCUMENT_BYTES", 2)
    with pytest.raises(ValueError, match="200 MB"):
        DocumentInput(filename="large.pdf", data=b"xxx")
