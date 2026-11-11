import pandas as pd
import pytest

from analyzer.cleaning import CleaningOptions, clean, normalise_name
from tests.conftest import raw


def test_column_names_are_normalised_and_kept_unique() -> None:
    df, report = clean(raw([{" Order Date ": "x", "order-date": "y", "Total (€)": "1"}]))
    assert list(df.columns) == ["order_date", "order_date_2", "total"]
    assert any("same name" in w for w in report.warnings)
    assert normalise_name("%%%") == "column"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["1,234.50", "10.00", "2.5"], [1234.5, 10.0, 2.5]),  # US style
        (["1.234,50", "10,00", "2,5"], [1234.5, 10.0, 2.5]),  # European style
        (["€1.234,50", "€ 10,00", "€2,5"], [1234.5, 10.0, 2.5]),  # with currency symbol
        (["$1,234.50", "-3.00", "+4"], [1234.5, -3.0, 4.0]),  # signs
        (["1,234,567", "2", "3"], [1234567.0, 2.0, 3.0]),  # repeated thousands separators
    ],
)
def test_number_formats(values: list[str], expected: list[float]) -> None:
    df, report = clean(raw([{"amount": v} for v in values]))
    assert df["amount"].tolist() == expected
    assert report.conversions[0].converted_to == "number"


def test_ambiguous_numbers_follow_the_column_majority() -> None:
    # "1,234" alone could be 1.234 or 1234; the rest of the column is European style.
    df, report = clean(raw([{"amount": v} for v in ["1,234", "5,50", "7,25", "9,99"]]))
    assert df["amount"].tolist() == [1.234, 5.5, 7.25, 9.99]
    assert "ambiguous" in report.conversions[0].note

    df, _ = clean(raw([{"amount": v} for v in ["1,234", "5.50", "7.25", "9.99"]]))
    assert df["amount"].tolist() == [1234.0, 5.5, 7.25, 9.99]


def test_mixed_decimal_styles_are_each_read_correctly() -> None:
    values = ["1,234.50"] * 9 + ["1.234,50"]
    # A distinct line number per row, so duplicate removal doesn't shrink the test data.
    df, report = clean(raw([{"line": f"L{i}", "amount": v} for i, v in enumerate(values)]))
    assert len(df) == 10
    assert set(df["amount"]) == {1234.5}
    assert "1 value(s) used the other decimal style" in report.conversions[0].note


def test_unparseable_numbers_become_missing_and_are_counted() -> None:
    values = [str(i) for i in range(19)] + ["n/a"]
    df, report = clean(raw([{"qty": v} for v in values]))
    assert df["qty"].isna().sum() == 1
    assert report.conversions[0].unparseable == 1


def test_mostly_text_column_stays_text() -> None:
    df, report = clean(raw([{"code": v} for v in ["A1", "B2", "17", "C3"]]))
    assert pd.api.types.is_string_dtype(df["code"])
    assert report.conversions == []


def test_mixed_currencies_are_not_added_up() -> None:
    df, report = clean(raw([{"amount": v} for v in ["€10", "$12", "€3"]]))
    assert pd.api.types.is_string_dtype(df["amount"])
    assert any("mixes currencies" in w for w in report.warnings)


def test_dates_in_two_formats() -> None:
    df, report = clean(raw([{"d": v} for v in ["2025-03-01", "25/03/2025", "2025-03-02T10:00:00"]]))
    assert df["d"].dt.strftime("%Y-%m-%d").tolist() == ["2025-03-01", "2025-03-25", "2025-03-02"]
    assert report.conversions[0].converted_to == "date"


def test_day_month_order_is_detected_per_column() -> None:
    df, _ = clean(raw([{"d": v} for v in ["03/04/2025", "03/25/2025"]]))
    assert df["d"].dt.month.tolist() == [3, 3]  # 25 can't be a month, so month comes first


def test_ambiguous_dates_use_the_option_and_say_so() -> None:
    rows = raw([{"d": "03/04/2025"}, {"d": "05/06/2025"}])
    df, report = clean(rows)
    assert df["d"].iloc[0] == pd.Timestamp("2025-04-03")
    assert "unclear" in report.conversions[0].note
    df, _ = clean(rows, CleaningOptions(dayfirst=False))
    assert df["d"].iloc[0] == pd.Timestamp("2025-03-04")


def test_empty_rows_whitespace_and_duplicates() -> None:
    table = raw(
        [
            {"a": " x ", "b": "1"},
            {"a": "x", "b": "1"},  # duplicate once trimmed
            {"a": "", "b": ""},  # empty
            {"a": "y", "b": "2"},
        ]
    )
    df, report = clean(table)
    assert len(df) == 2
    assert report.cells_trimmed == 1
    assert report.empty_rows_removed == 1
    assert report.duplicate_rows == 1

    kept, report = clean(table, CleaningOptions(drop_duplicates=False))
    assert len(kept) == 3
    assert report.duplicate_rows == 1
    assert "Kept 1 exact duplicate" in "\n".join(report.summary())


def test_input_is_not_modified() -> None:
    table = raw([{" A ": " 1 "}])
    before = table.copy()
    clean(table)
    pd.testing.assert_frame_equal(table, before)


def test_sample_file_cleaning_report(sample_path: object) -> None:
    from analyzer.loading import load

    df, report = clean(load(sample_path))  # type: ignore[arg-type]
    assert report.rows_in == 1218
    assert report.empty_rows_removed == 3
    assert report.duplicate_rows == 15
    assert report.rows_out == 1200
    assert pd.api.types.is_datetime64_any_dtype(df["order_date"])
    assert pd.api.types.is_float_dtype(df["amount"])
