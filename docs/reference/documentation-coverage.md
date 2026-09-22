# Documentation coverage

## Scope

This pass inspected the Streamlit entry point, packaging metadata, launcher,
configuration, extraction pipeline, strict models, rendering and packaging code, evaluation
commands, and current tests.

## User-facing coverage

| Surface | Status | Location |
|---|---|---|
| Installation and first run | Covered | `README.md`, first-extraction tutorial |
| Streamlit batch workflow | Covered | use-the-app how-to |
| Credentials and configuration | Covered | configuration reference |
| Output JSON/Markdown/ZIP contract | Covered | output-contract reference |
| GroundTruth evaluation and calibration | Covered | evaluation how-to |
| Local development and validation | Covered | develop-and-test how-to |
| Architecture and trust boundaries | Covered | architecture explanation |
| Common operator failures | Covered | troubleshooting guide |
| Supported Python integration surface | Covered | Python API reference |

## Inline code documentation

The Python API reference describes the principal integration surfaces, including the shared
extractor factory, pipeline result, stage configuration, and separate draft contract. It is not
a promise that every internal class or function is a supported public API. `GroundTruthDocument`
is documented in the reference rather than through a new model docstring because a Pydantic
model docstring changes its generated JSON Schema description.

Internal records do not have added docstrings. If the package becomes a
supported third-party SDK, define a stable exported namespace first, then require docstrings and
examples for that namespace.

## Validation performed

- All relative Markdown links resolve.
- Python fenced examples compile.
- Console `--help` output was checked for all four installed entry points.
- Artifact filenames were checked against `pipeline.py`.
- Documentation, single-model, and calibration tests passed (13 tests) for this sync.
- No paid extraction, live evaluation, or calibration promotion was performed for this sync.
