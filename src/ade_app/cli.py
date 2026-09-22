"""Command-line entry point for parse-only document conversion."""

import argparse
from pathlib import Path

from ade_app.artifacts import build_artifacts
from ade_app.inputs import DocumentInput, parse_page_range
from ade_app.models import DocumentResult
from ade_app.parser import parse_document, resolve_api_key
from ade_app.raster import page_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse a scanned PDF or image with GPT-6 Sol")
    parser.add_argument("input", type=Path)
    parser.add_argument("--pages", default="")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    source = DocumentInput(args.input.name, args.input.read_bytes())
    pages = parse_page_range(args.pages, page_count(source))
    document, assets, rendered = parse_document(source, pages, api_key=resolve_api_key())
    artifacts = build_artifacts(document, assets, rendered)
    directory = args.output_dir or args.input.with_name(f"{args.input.stem}.outputs")
    directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        artifacts.markdown_name: artifacts.markdown.encode(),
        artifacts.json_name: artifacts.json_text.encode(),
        artifacts.html_name: artifacts.html.encode(),
        artifacts.pdf_name: artifacts.annotated_pdf,
        artifacts.zip_name: artifacts.zip_bytes,
    }
    _write_outputs(directory, outputs, overwrite=args.overwrite)
    print(directory)
    return _exit_code(document)


def _write_outputs(directory: Path, outputs: dict[str, bytes], *, overwrite: bool) -> None:
    paths = {name: directory / name for name in outputs}
    if not overwrite:
        existing = next((path for path in paths.values() if path.exists()), None)
        if existing is not None:
            raise FileExistsError(f"output already exists: {existing}")
    for name, data in outputs.items():
        paths[name].write_bytes(data)


def _exit_code(document: DocumentResult) -> int:
    statuses = {page.status for page in document.pages}
    if "failed" in statuses:
        return 1
    if "partial" in statuses:
        return 2
    return 0
