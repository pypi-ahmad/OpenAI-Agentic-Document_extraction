import pytest
from pydantic import ValidationError

from ade_app.models import Box, PageRead


def test_box_requires_ordered_normalized_coordinates() -> None:
    with pytest.raises(ValidationError):
        Box(xmin=0.8, ymin=0, xmax=0.2, ymax=1)


def test_page_read_limits_zoom_requests() -> None:
    requests = [{"box": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1}, "reason": "small"}] * 5
    with pytest.raises(ValidationError):
        PageRead(blocks=[], zoom_requests=requests)
