import io
from pathlib import Path

import pandas as pd
import pytest

from analyzer import loading
from analyzer.loading import InputError, load, load_csv, load_excel


def test_semicolon_csv_keeps_values_as_text(tmp_path: Path) -> None:
    path = tmp_path / "eu.csv"
    path.write_text("Datum;Betrag\n01.02.2025;1.234,50\n", encoding="utf-8")
    df = load(path)
    assert list(df.columns) == ["Datum", "Betrag"]
    assert df.iloc[0, 1] == "1.234,50"  # untouched until cleaning


def test_windows_1252_file_is_read(tmp_path: Path) -> None:
    path = tmp_path / "excel-export.csv"
    path.write_bytes("Product,Amount\nCafé crème,3\n".encode("cp1252"))
    assert load(path).iloc[0, 0] == "Café crème"


def test_utf8_bom_is_not_part_of_the_first_column_name(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    path.write_bytes("﻿Date,Amount\n2025-01-01,1\n".encode())
    assert list(load(path).columns) == ["Date", "Amount"]


def test_excel_sheet_can_be_chosen(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="First", index=False)
        pd.DataFrame({"b": [2]}).to_excel(writer, sheet_name="Second", index=False)
    assert loading.excel_sheets(path) == ["First", "Second"]
    assert list(load_excel(path, "Second").columns) == ["b"]
    with pytest.raises(InputError):
        load_excel(path, "Missing")


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("empty.csv", b"", "empty"),
        ("data.pdf", b"%PDF", "Unsupported file type"),
        ("broken.xlsx", b"not a zip", "Could not open"),
    ],
)
def test_bad_files_give_clear_errors(
    tmp_path: Path, name: str, content: bytes, message: str
) -> None:
    path = tmp_path / name
    path.write_bytes(content)
    with pytest.raises(InputError, match=message):
        load(path)


def test_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loading, "MAX_FILE_BYTES", 10)
    with pytest.raises(InputError, match="larger than"):
        load_csv(io.BytesIO(b"a,b\n" + b"1,2\n" * 10))


def test_row_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loading, "MAX_ROWS", 3)
    with pytest.raises(InputError, match="more than 3 rows"):
        load_csv(io.BytesIO(b"a\n1\n2\n3\n4\n"))


def test_missing_file() -> None:
    with pytest.raises(InputError, match="not found"):
        load("does-not-exist.csv")
