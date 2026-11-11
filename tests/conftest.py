from pathlib import Path

import pandas as pd
import pytest

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "sales_synthetic.csv"


def raw(rows: list[dict[str, object]]) -> pd.DataFrame:
    """A raw table as the loader produces it: every value is text."""
    return pd.DataFrame(rows).astype(str).replace({"None": ""})


@pytest.fixture
def sample_path() -> Path:
    return SAMPLE
