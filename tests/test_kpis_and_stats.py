import math

import pandas as pd
import pytest

from analyzer import kpis, schema, stats


def sales(
    rows: list[tuple[str, float, str | None, str | None]], with_order_id: bool = True
) -> pd.DataFrame:
    """(date, amount, customer, order_id) rows, already in schema role names."""
    df = pd.DataFrame(rows, columns=["date", "amount", "customer", "order_id"])
    df["date"] = pd.to_datetime(df["date"])
    return df if with_order_id else df.drop(columns="order_id")


def test_summary_on_a_hand_computed_example() -> None:
    df = sales(
        [
            ("2025-01-05", 100.0, "ana", "o1"),
            ("2025-01-05", 50.0, "ana", "o1"),  # second line of the same order
            ("2025-02-01", 30.0, "ana", "o2"),
            ("2025-02-03", 20.0, "ion", "o3"),
        ]
    )
    k = kpis.summary(df)
    assert k.revenue == 200
    assert k.orders == 3
    assert k.average_order_value == pytest.approx(200 / 3)
    assert k.unique_customers == 2
    assert k.repeat_customer_rate == 0.5  # ana ordered twice, ion once
    assert k.first_date == pd.Timestamp("2025-01-05")


def test_without_order_id_each_row_is_an_order() -> None:
    df = sales(
        [("2025-01-01", 10.0, "a", None), ("2025-01-01", 10.0, "a", None)], with_order_id=False
    )
    k = kpis.summary(df)
    assert k.orders == 2
    assert any("each row is counted" in n for n in k.notes)


def test_refunds_are_netted_and_reported() -> None:
    k = kpis.summary(sales([("2025-01-01", 100.0, "a", "1"), ("2025-01-02", -40.0, "a", "2")]))
    assert k.revenue == 60
    assert k.refunds == -40
    assert any("refunds" in n for n in k.notes)


def test_no_customer_column_gives_none_not_zero() -> None:
    df = sales([("2025-01-01", 10.0, None, "1")]).drop(columns="customer")
    k = kpis.summary(df)
    assert k.unique_customers is None
    assert k.repeat_customer_rate is None


def test_empty_data_does_not_crash() -> None:
    df = sales([])
    k = kpis.summary(df)
    assert k.revenue == 0
    assert k.orders == 0
    assert k.average_order_value is None
    assert kpis.monthly(df).empty
    assert kpis.top(df, "customer").empty


def test_monthly_fills_gap_months_and_growth() -> None:
    df = sales(
        [
            ("2025-01-01", 100.0, "a", "1"),  # data starts on the 1st...
            ("2025-03-10", 50.0, "a", "2"),  # no sales in February
            ("2025-04-30", 75.0, "a", "3"),  # ...and ends on the last day: no partial months
        ]
    )
    m = kpis.monthly(df)
    assert not m["partial"].any()
    assert m["month"].tolist() == ["2025-01", "2025-02", "2025-03", "2025-04"]
    assert m["revenue"].tolist() == [100, 0, 50, 75]
    assert math.isnan(m["growth_pct"][0])  # no previous month
    assert m["growth_pct"][1] == -100.0
    assert math.isnan(m["growth_pct"][2])  # growth from zero is undefined
    assert m["growth_pct"][3] == 50.0


def test_rows_without_date_are_excluded_from_monthly_only() -> None:
    df = sales([("2025-01-10", 100.0, "a", "1"), (None, 50.0, "a", "2")])  # type: ignore[list-item]
    assert kpis.summary(df).revenue == 150
    assert kpis.monthly(df)["revenue"].sum() == 100
    assert any("without a readable date" in n for n in kpis.summary(df).notes)


def test_top_customers_with_share() -> None:
    df = sales([("2025-01-01", 75.0, "big", "1"), ("2025-01-01", 25.0, "small", "2")])
    top = kpis.top(df, "customer", n=1)
    assert top.to_dict("records") == [
        {"customer": "big", "revenue": 75.0, "orders": 1, "share_pct": 75.0}
    ]


def test_iqr_flags_an_obvious_outlier() -> None:
    values = pd.Series([10, 11, 12, 10, 11, 500], dtype="Float64")
    assert stats.iqr_outliers(values).tolist() == [False] * 5 + [True]


def test_stats_tables() -> None:
    df = pd.DataFrame({"a": [1.0, 2.0, None], "b": [2.0, 4.0, 6.0], "c": ["x", None, "z"]})
    missing = stats.missing_values(df).set_index("column")
    assert missing.loc["a", "missing"] == 1
    assert missing.loc["c", "missing_pct"] == 33.3
    assert set(stats.describe_numeric(df)["column"]) == {"a", "b"}
    assert stats.correlations(df).loc["a", "b"] == pytest.approx(1.0)


def test_schema_detection_and_validation() -> None:
    df = pd.DataFrame(
        {
            "order_date": pd.to_datetime(["2025-01-01"]),
            "total": [1.0],
            "client": ["a"],
            "notes": ["x"],
        }
    )
    guess = schema.detect(df)
    assert guess["date"] == "order_date"
    assert guess["amount"] == "total"
    assert guess["customer"] == "client"
    assert guess["product"] is None

    with pytest.raises(schema.MappingError, match="date"):
        schema.build_mapping(df, {"date": None, "amount": "total"})
    with pytest.raises(schema.MappingError, match="more than one role"):
        schema.build_mapping(df, {"date": "order_date", "amount": "total", "customer": "total"})
    with pytest.raises(schema.MappingError, match="does not contain numbers"):
        schema.build_mapping(df, {"date": "order_date", "amount": "client"})

    mapped = schema.apply(df, schema.build_mapping(df, guess))
    assert list(mapped.columns) == ["date", "amount", "customer"]
