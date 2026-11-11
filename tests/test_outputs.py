import io
import re
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from analyzer import export, report
from analyzer.cli import main
from analyzer.loading import load
from analyzer.pipeline import Analysis, run
from tests.conftest import raw


def small_analysis(product: str = "Lamp") -> Analysis:
    table = raw(
        [
            {"Date": "2025-01-01", "Amount": "10", "Product": product, "Customer": "=HYPERLINK(1)"},
            {"Date": "2025-02-01", "Amount": "20", "Product": "Chair", "Customer": "b"},
        ]
    )
    return run(table, "test.csv")


def test_workbook_sheets_and_formula_escaping() -> None:
    book = openpyxl.load_workbook(io.BytesIO(export.workbook(small_analysis())))
    assert book.sheetnames == [
        "KPIs", "Monthly", "Top products", "Top customers", "Regions", "Cleaning log", "Data",
    ]  # fmt: skip
    data = book["Data"]
    header = [c.value for c in data[1]]
    customer = data.cell(row=2, column=header.index("customer") + 1).value
    assert customer == "'=HYPERLINK(1)"  # stored as text, not a formula
    kpis = {row[0].value: row[1].value for row in book["KPIs"].iter_rows(min_row=2)}
    assert kpis["Revenue"] == 30


def test_cleaned_csv_is_excel_friendly() -> None:
    data = export.cleaned_csv(small_analysis())
    assert data.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    df = pd.read_csv(io.BytesIO(data), encoding="utf-8-sig")
    assert df.loc[0, "customer"] == "'=HYPERLINK(1)"


def test_report_escapes_user_text_and_needs_no_internet() -> None:
    html = report.render(small_analysis(product="<script>alert(1)</script>"))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert not re.search(r"<script[^>]+src=", html)  # plotly.js is embedded, not fetched
    assert "Revenue by month" in html


def test_cli_writes_all_outputs(tmp_path: Path, sample_path: Path, capsys: object) -> None:
    out = tmp_path / "out"
    args = ["report", str(sample_path)]
    args += ["--html", str(out / "r.html"), "--xlsx", str(out / "r.xlsx")]
    args += ["--csv", str(out / "c.csv")]
    code = main(args)
    assert code == 0
    for name in ("r.html", "r.xlsx", "c.csv"):
        assert (out / name).stat().st_size > 0
    assert len(load(out / "c.csv")) == 1200


def test_cli_column_override_and_errors(
    tmp_path: Path, sample_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # quantity was auto-detected as the quantity column; choosing it as the amount
    # moves it rather than failing with "used for more than one role".
    assert main(["report", str(sample_path), "--amount-column", "quantity"]) == 0
    assert "'amount': 'quantity'" in capsys.readouterr().out
    assert main(["report", str(sample_path), "--amount-column", "nope"]) == 2
    assert main(["report", str(tmp_path / "missing.csv")]) == 2


def test_sample_generator_is_deterministic() -> None:
    from scripts.make_sample import build

    assert build().equals(build())
    committed = (
        Path(__file__).resolve().parent.parent / "samples" / "sales_synthetic.csv"
    ).read_bytes()
    assert build().to_csv(index=False, lineterminator="\n").encode() == committed
