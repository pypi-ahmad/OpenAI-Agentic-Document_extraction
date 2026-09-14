"""Command-line interface entry point for single document extraction.

Responsible for parsing arguments (`ade-extract`), configuring logging, loading TOML config,
invoking `run_pipeline`, writing output artifacts (Markdown, JSON, PDF, Manifest) to disk,
and returning process exit codes (0=complete, 1=partial/review required, 2=failed).
Must NOT execute pipeline operations directly; delegates to ade_app.runner.
Next: ade_app.runner for high-level pipeline execution.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from ade_app.config import PipelineConfig
from ade_app.inputs import DocumentInput, parse_page_range
from ade_app.logging import configure_logging
from ade_app.raster import get_page_count
from ade_app.runner import PipelineResult, run_pipeline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract a PDF or image into reviewable artifacts")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--pages", default="", help="1-based pages, for example 1,3-5")
    parser.add_argument("--dpi", type=int)
    parser.add_argument("--device", choices=("auto", "gpu", "cpu"))
    parser.add_argument("--max-workers", type=int)
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    parser.add_argument("--log-format", choices=("text", "json"))
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _settings(args: argparse.Namespace) -> PipelineConfig:
    config = PipelineConfig.from_toml(args.config) if args.config else PipelineConfig()
    if args.dpi is not None:
        config = config.model_copy(
            update={"imaging": config.imaging.model_copy(update={"dpi": args.dpi})}
        )
    if args.device is not None:
        config = config.model_copy(
            update={"layout": config.layout.model_copy(update={"device": args.device})}
        )
    if args.max_workers is not None:
        config = config.model_copy(
            update={
                "runtime": config.runtime.model_copy(update={"max_page_workers": args.max_workers})
            }
        )
    logging_updates = {
        key: value
        for key, value in (("level", args.log_level), ("format", args.log_format))
        if value is not None
    }
    if logging_updates:
        config = config.model_copy(
            update={"logging": config.logging.model_copy(update=logging_updates)}
        )
    return PipelineConfig.model_validate(config.model_dump())


def _atomic_write(path: Path, data: bytes, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_result(result: PipelineResult, directory: Path, *, overwrite: bool) -> None:
    if result.run is None:
        _atomic_write(
            directory / "failure-report.json", result.report_text.encode(), overwrite=overwrite
        )
        return
    run = result.run
    outputs = {
        run.markdown_filename: run.artifact.markdown.encode(),
        run.json_filename: run.json_text.encode(),
        run.confidence_filename: run.confidence_text.encode(),
        run.annotated_pdf_filename: run.annotated_pdf,
        "manifest.json": json.dumps(run.manifest, indent=2).encode(),
        run.zip_filename: run.zip_bytes,
    }
    for filename, data in outputs.items():
        _atomic_write(directory / filename, data, overwrite=overwrite)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = _settings(args)
        configure_logging(config.logging)
        data = args.input.read_bytes()
        source = DocumentInput(args.input.name, data)
        pages = parse_page_range(args.pages, get_page_count(source))
        result = run_pipeline(source, pages, config=config)
        output_dir = args.output_dir or args.input.with_name(f"{args.input.stem}.outputs")
        _write_result(result, output_dir, overwrite=args.overwrite)
    except (OSError, ValueError) as error:
        print(f"ade-extract: {error}", file=sys.stderr)
        return 1
    print(output_dir)
    return {"complete": 0, "partial": 2, "failed": 1}[result.status]
