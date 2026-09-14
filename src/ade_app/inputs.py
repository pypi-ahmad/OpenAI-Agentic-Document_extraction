"""Document input validation and inclusive page-range parsing.

This is the trust boundary for uploaded bytes and filenames: DocumentInput
rejects unsafe names, unsupported suffixes, empty content, and files over
200 MB before anything downstream treats the data as a document. Must NOT
decode, sniff, or open the file contents itself — that happens next in
ade_app.raster, which is the module to open next.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SUFFIXES = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"})
MAX_DOCUMENT_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DocumentInput:
    """Validated in-memory PDF or image supplied to the extraction pipeline."""

    filename: str
    data: bytes

    def __post_init__(self) -> None:
        # filename/data originate from an untrusted upload; reject anything
        # that isn't a plain, safe, in-size-limit name before construction
        # succeeds, so no later code path can hold an unvalidated instance.
        if not self.filename or Path(self.filename).name != self.filename:
            raise ValueError("filename must be a plain file name")
        if (
            len(self.filename) > 255
            or any(ord(character) < 32 for character in self.filename)
            or any(character in '<>:"/\\|?*' for character in self.filename)
        ):
            raise ValueError("filename contains unsafe characters or is too long")
        if Path(self.filename).suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError("unsupported document type")
        if not self.data:
            raise ValueError("document is empty")
        if len(self.data) > MAX_DOCUMENT_BYTES:
            raise ValueError("document may not exceed 200 MB")

    @property
    def suffix(self) -> str:
        return Path(self.filename).suffix.lower()

    @property
    def stem(self) -> str:
        return Path(self.filename).stem


def parse_page_range(specification: str, page_count: int) -> tuple[int, ...]:
    """Parse 1-based inclusive pages such as ``1,3-5``."""

    if page_count < 1:
        raise ValueError("page_count must be positive")
    text = specification.strip()
    if not text:
        return tuple(range(1, page_count + 1))

    pages: set[int] = set()
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError("page range contains an empty item")
        if "-" in part:
            pieces = [piece.strip() for piece in part.split("-")]
            if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
                raise ValueError(f"invalid page range: {part}")
            start, end = (int(piece) for piece in pieces)
            if start > end:
                raise ValueError(f"page range is reversed: {part}")
            pages.update(range(start, end + 1))
        else:
            if not part.isdigit():
                raise ValueError(f"invalid page: {part}")
            pages.add(int(part))

    if not pages or min(pages) < 1 or max(pages) > page_count:
        raise ValueError(f"pages must be between 1 and {page_count}")
    return tuple(sorted(pages))
