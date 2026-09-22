"""Application-wide constants and default tuning parameters.

Responsible for fixed model definitions, reasoning efforts, standard endpoints,
default DPI resolutions, and default quality thresholds shared across the pipeline.
Must NOT load environment variables, read disk configs, or manage runtime state.
Next: ade_app.config for user-tunable configuration that overrides these defaults.
"""

from typing import Final

MODEL_ID: Final = "gpt-6-sol"
PRIMARY_MODEL: Final = (MODEL_ID, "medium")
VERIFICATION_MODEL: Final = PRIMARY_MODEL
REPAIR_MODEL: Final = PRIMARY_MODEL
MODEL_CASCADE: Final = (PRIMARY_MODEL,)
OPENAI_BASE_URL: Final = "https://api.openai.com/v1"
GROUNDTRUTH_OPENAPI_SPEC: Final = "https://api.ade.landing.ai/openapi.json"
PAGE_BREAK: Final = "<!-- PAGE BREAK -->"
DEFAULT_DPI: Final = 300
MAX_IMAGE_PATCHES: Final = 29_000
QUALITY_THRESHOLD: Final = 90.0
QUALITY_PROFILE_PATH: Final = "profiles/segment-quality-gpt-6-sol-v3.json"
