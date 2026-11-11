"""Regression tests for issues found in code and security review."""

import io

import openpyxl
import pandas as pd
import pytest

from analyzer import export, kpis, schema
from analyzer.cleaning import CleaningOptions, clean
from analyzer.pipeline import run
from tests.conftest import raw

KEEP_ALL = CleaningOptions(drop_duplicates=False)


def column(name: str, values: list[str]) -> pd.Series:
    df, _ = clean(raw([{name: v} for v in values]), KEEP_ALL)
    return df[name]


# --- cleaning --------------------------------------------------------------------


def test_dates_with_time_zones_are_converted_to_utc() -> None:
    values = ["2025-01-31T23:30:00+02:00", "2025-02-01T10:00:00Z", "2025-02-02"]
    df, report = clean(raw([{"d": v} for v in values]))
    assert df["d"].dt.strftime("%Y-%m-%d %H:%M").tolist() == [
        "2025-01-31 21:30",
        "2025-02-01 10:00",
        "2025-02-02 00:00",
    ]
    assert "2 value(s) with a time zone converted to UTC" in report.conversions[0].note


def test_dotted_european_dates_are_dates_not_numbers() -> None:
    values = column("d", ["01.02.2025", "15.03.2025", "28.04.2025"])
    assert pd.api.types.is_datetime64_any_dtype(values)
    assert values.dt.strftime("%Y-%m-%d").tolist() == ["2025-02-01", "2025-03-15", "2025-04-28"]


@pytest.mark.parametrize(
    ("name", "values"),
    [
        ("customer_id", ["1001", "1002", "1003"]),  # name says it's an id
        ("zip", ["10001", "20002", "30003"]),
        ("account", ["0042", "0043", "1044"]),  # leading zeros would be lost
        ("ref", ["1234567890123456789", "1234567890123456788", "1"]),  # too long for float
        ("contact", ["0722 123 456", "0733 999 000", "0744 000 111"]),
    ],
)
def test_identifiers_stay_text(name: str, values: list[str]) -> None:
    result = column(name, values)
    assert pd.api.types.is_string_dtype(result)
    assert result.tolist() == values


def test_accounting_negatives_and_scientific_notation() -> None:
    df, report = clean(raw([{"n": v} for v in ["100.00", "(40.00)", "2.5E+3", "1e-02"]]), KEEP_ALL)
    assert df["n"].tolist() == [100.0, -40.0, 2500.0, 0.01]
    assert report.conversions[0].unparseable == 0
    assert "1 value(s) in parentheses read as negative" in report.conversions[0].note


def test_ambiguous_note_shows_the_actual_reading() -> None:
    # Under the (default) 1,234.50 convention, "1.500" means one and a half.
    df, report = clean(raw([{"n": v} for v in ["1.500", "2.000", "3"]]), KEEP_ALL)
    assert df["n"].tolist() == [1.5, 2.0, 3.0]
    assert "'1.500' read as 1.5" in report.conversions[0].note


def test_junk_does_not_count_as_ambiguous() -> None:
    values = [str(i) for i in range(1, 20)] + ["n/a"]
    _, report = clean(raw([{"n": v} for v in values]), KEEP_ALL)
    assert report.conversions[0].unparseable == 1
    assert "ambiguous" not in report.conversions[0].note


# --- schema ----------------------------------------------------------------------


def test_unit_price_and_status_are_not_auto_mapped() -> None:
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01"]),
            "price": [10.0],
            "quantity": [5.0],
            "state": ["cancelled"],
        }
    )
    guess = schema.detect(df)
    assert guess["amount"] is None  # the user must choose; price x quantity isn't revenue
    assert guess["region"] is None


# --- KPIs ------------------------------------------------------------------------


def frame(
    rows: list[tuple[str, float | None, str]], customer: list[str] | None = None
) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "amount", "order_id"])
    df["date"] = pd.to_datetime(df["date"])
    df["amount"] = df["amount"].astype("Float64")
    if customer is not None:
        df["customer"] = customer
    return df


def test_numeric_order_ids_with_blanks_do_not_crash() -> None:
    table = raw(
        [
            {"date": "2025-01-01", "amount": "10", "order_id": "1001"},
            {"date": "2025-01-02", "amount": "20", "order_id": ""},
            {"date": "2025-01-03", "amount": "30", "order_id": "1002"},
        ]
    )
    analysis = run(table, "t.csv")
    assert analysis.kpis.orders == 3


def test_orders_without_amount_or_net_refunds_are_not_counted() -> None:
    df = frame(
        [
            ("2025-01-01", 100.0, "a"),
            ("2025-01-02", None, "b"),  # no amount
            ("2025-01-03", 100.0, "c"),
            ("2025-01-04", -100.0, "c"),  # fully refunded
        ]
    )
    k = kpis.summary(df)
    assert k.revenue == 100
    assert k.orders == 1
    assert k.average_order_value == 100


def test_repeat_rate_needs_order_ids() -> None:
    df = frame([("2025-01-01", 10.0, "x"), ("2025-01-01", 5.0, "x")], customer=["ana", "ana"])
    assert kpis.summary(df).repeat_customer_rate == 0  # one basket with two lines
    no_ids = df.drop(columns="order_id")
    k = kpis.summary(no_ids)
    assert k.repeat_customer_rate is None
    assert any("needs an order id" in n for n in k.notes)


def test_growth_after_a_negative_month_is_undefined() -> None:
    df = frame([("2025-01-01", -100.0, "r"), ("2025-02-01", 50.0, "a"), ("2025-02-28", 1.0, "b")])
    m = kpis.monthly(df)
    assert pd.isna(m["growth_pct"][1])


def test_partial_last_month_gets_no_growth() -> None:
    df = frame([("2025-01-01", 100.0, "a"), ("2025-01-31", 100.0, "b"), ("2025-02-03", 10.0, "c")])
    m = kpis.monthly(df)
    assert m["partial"].tolist() == [False, True]
    assert pd.isna(m["growth_pct"][1])  # not a -95% "collapse"


# --- export ----------------------------------------------------------------------


def test_excel_export_survives_control_characters() -> None:
    table = raw([{"date": "2025-01-01", "amount": "10", "product": "Lamp\x0bBlue"}])
    book = openpyxl.load_workbook(io.BytesIO(export.workbook(run(table, "t.csv"))))
    data = book["Data"]
    header = [c.value for c in data[1]]
    assert data.cell(row=2, column=header.index("product") + 1).value == "LampBlue"


def test_formula_escaping_covers_summary_sheets() -> None:
    table = raw([{"date": "2025-01-01", "amount": "10", "product": "=1+1"}])
    book = openpyxl.load_workbook(io.BytesIO(export.workbook(run(table, "t.csv"))))
    values = [row[0].value for row in book["Top products"].iter_rows(min_row=2)]
    assert values == ["'=1+1"]
