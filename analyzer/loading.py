"""Reading CSV and Excel files.

Every column is read as text. Converting to numbers and dates happens later in
cleaning.py, where each conversion is counted and reported instead of being
guessed silently by the file reader.
"""

import csv
import io
import zipfile
from pathlib import Path
from typing import BinaryIO

import openpyxl
import pandas as pd
from openpyxl.workbook.workbook import Workbook

MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_UNPACKED_BYTES = 300 * 1024 * 1024  # .xlsx files are zip archives
MAX_ROWS = 1_000_000
MAX_COLUMNS = 500
MAX_CELLS = 20_000_000
ENCODINGS = ("utf-8-sig", "cp1252")  # cp1252 covers most files saved by Excel on Windows
EXCEL_SUFFIXES = {".xlsx", ".xlsm"}


class InputError(ValueError):
    """The file can't be read. The message is safe to show to the user."""


def _read_bytes(source: str | Path | BinaryIO) -> tuple[bytes, str]:
    if isinstance(source, str | Path):
        path = Path(source)
        if not path.is_file():
            raise InputError(f"File not found: {path.name}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise InputError("File is larger than 50 MB.")
        return path.read_bytes(), path.name
    data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise InputError("File is larger than 50 MB.")
    return data, getattr(source, "name", "upload")


def _decode(data: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise InputError("Could not read the file's text encoding. Save it as UTF-8 and try again.")


def _sniff_delimiter(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:20_000], delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def load_csv(source: str | Path | BinaryIO) -> pd.DataFrame:
    data, _ = _read_bytes(source)
    text = _decode(data)
    if not text.strip():
        raise InputError("The file is empty.")
    sep = _sniff_delimiter(text)
    # Check the shape before parsing: a small file of only delimiters can describe
    # millions of columns and take pandas minutes to read.
    header = text[: text.find("\n")] if "\n" in text else text
    columns = header.count(sep) + 1
    if columns > MAX_COLUMNS:
        raise InputError(f"The file has more than {MAX_COLUMNS} columns.")
    if (text.count("\n") + 1) * columns > MAX_CELLS:
        raise InputError(f"The file has more than {MAX_CELLS:,} cells.")
    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=sep,
            dtype=str,
            keep_default_na=False,  # keep "" and "NA" as text; cleaning decides what is missing
            nrows=MAX_ROWS + 1,
        )
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as e:
        raise InputError(f"Could not parse the CSV file: {e}") from None
    return _check_size(df)


def _open_workbook(data: bytes) -> Workbook:
    """Open an .xlsx in streaming mode after checking it isn't a 'zip bomb'."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            unpacked = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile:
        raise InputError("Could not open the Excel file.") from None
    if unpacked > MAX_UNPACKED_BYTES:
        raise InputError("The Excel file is too large once unpacked.")
    try:
        return openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # openpyxl raises several unrelated types for corrupt files
        raise InputError("Could not open the Excel file.") from e


def excel_sheets(source: str | Path | BinaryIO) -> list[str]:
    data, _ = _read_bytes(source)
    workbook = _open_workbook(data)
    try:
        return [str(s) for s in workbook.sheetnames]
    finally:
        workbook.close()


def _cell_text(value: object) -> str:
    return "" if value is None else str(value)


def load_excel(source: str | Path | BinaryIO, sheet: str | int = 0) -> pd.DataFrame:
    """Read one sheet; the first row is the header. Values are converted to text."""
    data, _ = _read_bytes(source)
    workbook = _open_workbook(data)
    try:
        try:
            ws = workbook.worksheets[sheet] if isinstance(sheet, int) else workbook[sheet]
        except (IndexError, KeyError):
            raise InputError(f"Worksheet {sheet!r} not found.") from None
        # The sheet's declared size: one cell far away (e.g. XFD200000) would make
        # the sheet billions of cells, almost all of them empty.
        declared_rows, declared_cols = ws.max_row or 0, ws.max_column or 0
        if declared_cols > MAX_COLUMNS or declared_rows * declared_cols > MAX_CELLS:
            raise InputError(
                f"The sheet is too large (limits: {MAX_COLUMNS} columns, {MAX_CELLS:,} cells)."
            )
        # The declared size can also be wrong (too small would silently drop data), so
        # read what is actually there and enforce the limits while reading.
        ws.reset_dimensions()
        rows: list[list[str]] = []
        cells = 0
        for values in ws.iter_rows(values_only=True):
            if len(values) > MAX_COLUMNS:
                raise InputError(f"The sheet has more than {MAX_COLUMNS} columns.")
            rows.append([_cell_text(v) for v in values])
            cells += len(values)
            if len(rows) > MAX_ROWS + 1 or cells > MAX_CELLS:
                raise InputError(f"The sheet has more than {MAX_CELLS:,} cells.")
    finally:
        workbook.close()

    if not rows:
        raise InputError("The sheet is empty.")
    # Rows can have different lengths; pad all of them (and the header) to the widest.
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = [h or f"column_{i + 1}" for i, h in enumerate(rows[0])]
    return _check_size(pd.DataFrame(rows[1:], columns=header, dtype="string"))


def load(source: str | Path | BinaryIO, filename: str | None = None) -> pd.DataFrame:
    """Load a CSV or Excel file, chosen by its extension."""
    if filename is None:
        filename = str(source) if isinstance(source, str | Path) else getattr(source, "name", "")
    suffix = Path(str(filename)).suffix.lower()
    if suffix in EXCEL_SUFFIXES:
        return load_excel(source)
    if suffix in {".csv", ".txt", ".tsv"}:
        return load_csv(source)
    raise InputError("Unsupported file type. Use .csv, .xlsx or .xlsm.")


def _check_size(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) > MAX_ROWS:
        raise InputError(f"File has more than {MAX_ROWS:,} rows.")
    if df.columns.empty:
        raise InputError("The file has no columns.")
    return df
