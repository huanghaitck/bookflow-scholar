from pathlib import Path
import os

import pytest


@pytest.fixture
def candidate_wheel() -> Path:
    value = os.environ.get("BOOKFLOW_CANDIDATE_WHEEL")
    if not value:
        pytest.skip("set BOOKFLOW_CANDIDATE_WHEEL for independent black-box acceptance")
    path = Path(value)
    if not path.is_file():
        pytest.fail(f"candidate wheel not found: {path}")
    return path
