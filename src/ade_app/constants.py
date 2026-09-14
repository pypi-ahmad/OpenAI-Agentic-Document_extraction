"""Application-wide constants and default tuning parameters.

Responsible for fixed model definitions, reasoning efforts, standard endpoints,
default DPI resolutions, and default quality thresholds shared across the pipeline.
Must NOT load environment variables, read disk configs, or manage runtime state.
Next: ade_app.config for user-tunable configuration that overrides these defaults.
"""

from typing import Final

LUNA_MODEL: Final = ("gpt-5.6-luna", "low")
TERRA_MODEL: Final = ("gpt-5.6-terra", "medium")
SOL_MODEL: Final = ("gpt-5.6-sol", "low")
MODEL_CASCADE: Final = (LUNA_MODEL, TERRA_MODEL, SOL_MODEL)
PRIMARY_MODEL: Final = LUNA_MODEL
VERIFICATION_MODEL: Final = TERRA_MODEL
REPAIR_MODEL: Final = SOL_MODEL
MODEL_ID: Final = "+".join(model for model, _ in MODEL_CASCADE)
OPENAI_BASE_URL: Final = "https://api.openai.com/v1"
GROUNDTRUTH_OPENAPI_SPEC: Final = "https://api.ade.landing.ai/openapi.json"
PAGE_BREAK: Final = "<!-- PAGE BREAK -->"
DEFAULT_DPI: Final = 300
MAX_IMAGE_PATCHES: Final = 29_000
QUALITY_THRESHOLD: Final = 90.0
QUALITY_PROFILE_PATH: Final = "profiles/segment-quality-terra-v3.json"
