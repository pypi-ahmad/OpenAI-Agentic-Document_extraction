"""Application-wide constants."""

from typing import Final

MODEL_CASCADE: Final = (
    ("gpt-5.6-terra", "medium"),
    ("gpt-5.6-sol", "low"),
)
PRIMARY_MODEL: Final = MODEL_CASCADE[0]
REPAIR_MODEL: Final = MODEL_CASCADE[1]
MODEL_ID: Final = "+".join(model for model, _ in MODEL_CASCADE)
OPENAI_BASE_URL: Final = "https://api.openai.com/v1"
GROUNDTRUTH_OPENAPI_SPEC: Final = "https://api.ade.landing.ai/openapi.json"
PAGE_BREAK: Final = "<!-- PAGE BREAK -->"
DEFAULT_DPI: Final = 200
MAX_IMAGE_PATCHES: Final = 29_000
QUALITY_THRESHOLD: Final = 90.0
QUALITY_PROFILE_PATH: Final = "profiles/segment-quality-terra-v3.json"
