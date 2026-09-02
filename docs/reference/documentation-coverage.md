# Documentation coverage

## Scope

This documentation pass inspected the Streamlit entry point, packaging metadata, launcher,
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

An AST inventory found 162 non-underscore module-level classes/functions and 48 with inline
docstrings (30%). That naming-based count includes strict schemas, immutable records, console
handlers, metric helpers, and internal orchestration functions; it is not the supported public
API definition. Fifteen of the 16 principal integration classes/functions listed in the Python
API reference have inline documentation. `GroundTruthDocument` is documented in the reference
only because a Pydantic model docstring changes its generated JSON Schema description.

Adding docstrings to internal records was intentionally left out. If the package becomes a
supported third-party SDK, define a stable exported namespace first, then require docstrings and
examples for that namespace.

## Validation performed

- All relative Markdown links resolve.
- Python fenced examples compile.
- Console `--help` output was checked for all three installed entry points.
- Artifact filenames were checked against `pipeline.py`.
- The project test, Ruff, and ty gates passed after the documentation changes.
