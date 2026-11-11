"""Regression tests for the security review: hostile files and queries must fail fast."""

import io
import time
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from analyzer import export, loading
from analyzer.loading import InputError, load_csv, load_excel
from analyzer.pipeline import run
from analyzer.sql import QueryError, run_query
from tests.conftest import raw

DATA = pd.DataFrame({"a": [1, 2]})


def sparse_xlsx(declared: str) -> bytes:
    """A tiny workbook with one extra cell at XFD200000: billions of mostly empty cells.

    `declared` is the sheet's <dimension> tag, which a hostile file can set to anything.
    """
    buffer = io.BytesIO()
    pd.DataFrame({"a": [1]}).to_excel(buffer, index=False)
    source = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    far_cell = b'<row r="200000"><c r="XFD200000" t="inlineStr"><is><t>x</t></is></c></row>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                assert b'<dimension ref="A1:A2" />' in content
                content = content.replace(b"</sheetData>", far_cell + b"</sheetData>")
                content = content.replace(
                    b'<dimension ref="A1:A2" />', f'<dimension ref="{declared}" />'.encode()
                )
            target.writestr(item, content)
    return out.getvalue()


@pytest.mark.parametrize("declared", ["A1:XFD200000", "A1:A2"])  # honest, then lying
def test_sparse_excel_sheet_is_rejected_quickly(declared: str) -> None:
    start = time.monotonic()
    with pytest.raises(InputError, match="too large|more than"):
        load_excel(io.BytesIO(sparse_xlsx(declared)))
    assert time.monotonic() - start < 10


def test_excel_rows_outside_the_declared_size_are_not_dropped() -> None:
    buffer = io.BytesIO()
    pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}).to_excel(buffer, index=False)
    source = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                content = content.replace(
                    b'<dimension ref="A1:B4" />', b'<dimension ref="A1:A2" />'
                )
            target.writestr(item, content)
    loaded = load_excel(io.BytesIO(out.getvalue()))
    assert loaded.to_dict("list") == {"a": ["1", "2", "3"], "b": ["x", "y", "z"]}


def test_excel_zip_bomb_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = io.BytesIO()
    pd.DataFrame({"a": range(1000)}).to_excel(buffer, index=False)
    monkeypatch.setattr(loading, "MAX_UNPACKED_BYTES", 1000)
    with pytest.raises(InputError, match="too large once unpacked"):
        load_excel(io.BytesIO(buffer.getvalue()))


def test_excel_values_are_read_as_text(tmp_path: Path) -> None:
    path = tmp_path / "t.xlsx"
    df = pd.DataFrame({"when": pd.to_datetime(["2025-01-02"]), "n": [1.5], "blank": [None]})
    df.to_excel(path, index=False)
    loaded = load_excel(path)
    assert loaded.iloc[0].tolist() == ["2025-01-02 00:00:00", "1.5", ""]


def test_csv_with_huge_number_of_columns_is_rejected_quickly() -> None:
    start = time.monotonic()
    with pytest.raises(InputError, match="columns"):
        load_csv(io.BytesIO(b"," * 5_000_000))
    assert time.monotonic() - start < 5


def test_csv_cell_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loading, "MAX_CELLS", 10)
    with pytest.raises(InputError, match="cells"):
        load_csv(io.BytesIO(b"a,b\n" + b"1,2\n" * 10))


def test_sql_memory_is_capped() -> None:
    start = time.monotonic()
    with pytest.raises(QueryError, match="memory"):
        run_query(DATA, "SELECT len(list(range)) FROM range(2000000000)")
    assert time.monotonic() - start < 5


def test_sql_time_limit_also_covers_fetching() -> None:
    start = time.monotonic()
    with pytest.raises(QueryError):
        run_query(DATA, "SELECT repeat('x', 10000000) AS s FROM range(1000)", timeout_s=1)
    assert time.monotonic() - start < 10


def test_sql_query_length_is_capped() -> None:
    with pytest.raises(QueryError, match="longer than"):
        run_query(DATA, "SELECT 1" + " " * 20_000)


def test_sql_does_not_reveal_server_paths() -> None:
    settings = run_query(DATA, "SELECT name, value FROM duckdb_settings()").rows
    home = str(Path.home())
    assert not settings["value"].astype(str).str.contains(home, regex=False).any()


def test_sql_does_not_install_extensions() -> None:
    with pytest.raises(QueryError):
        run_query(DATA, "SELECT * FROM sqlite_scan('x.db', 't')")
    with pytest.raises(QueryError):
        run_query(DATA, "SELECT * FROM read_xlsx('x.xlsx')")


def test_control_characters_cannot_hide_a_formula_in_csv() -> None:
    table = raw([{"date": "2025-01-01", "amount": "1", "product": "\x00=1+1"}])
    csv_text = export.cleaned_csv(run(table, "t.csv")).decode("utf-8-sig")
    assert "'=1+1" in csv_text
    assert "\x00" not in csv_text
