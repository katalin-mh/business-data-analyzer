"""Sales KPIs. Input is a DataFrame already renamed to schema roles (date, amount, ...).

An *order* is a distinct order id (or a row, when there is no order id column)
whose lines add up to more than zero. Lines without an amount, and refund-only
orders, still affect revenue as described below but are not counted as orders.
"""

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class KPIs:
    revenue: float
    orders: int
    average_order_value: float | None
    unique_customers: int | None
    repeat_customer_rate: float | None  # share of customers with more than one order
    refunds: float  # sum of negative amounts (already included in revenue)
    first_date: pd.Timestamp | None
    last_date: pd.Timestamp | None
    notes: list[str] = field(default_factory=list)


def _order_key(df: pd.DataFrame) -> pd.Series:
    """One value per order: the order id as text if mapped, otherwise the row number."""
    rows = pd.Series(df.index, index=df.index).map(lambda i: f"row-{i}")
    if "order_id" in df.columns:
        return df["order_id"].astype("string").fillna(rows)
    return rows


def _counted(df: pd.DataFrame) -> pd.Series:
    """True for rows that belong to an order with a positive total."""
    key = _order_key(df)
    order_total = df["amount"].astype("float64").groupby(key).transform("sum")
    return df["amount"].notna() & (order_total > 0)


def _orders_by(df: pd.DataFrame, group: pd.Series) -> pd.Series:
    counted = _counted(df)
    return _order_key(df)[counted].groupby(group[counted]).nunique()


def summary(df: pd.DataFrame) -> KPIs:
    notes = []
    missing_amount = int(df["amount"].isna().sum())
    if missing_amount:
        notes.append(f"{missing_amount} row(s) without an amount are excluded from revenue.")
    missing_date = int(df["date"].isna().sum())
    if missing_date:
        notes.append(
            f"{missing_date} row(s) without a readable date are left out of monthly figures."
        )
    has_order_id = "order_id" in df.columns
    if not has_order_id:
        notes.append("No order id column: each row is counted as one order.")

    revenue = float(df["amount"].sum())
    orders = int(_order_key(df)[_counted(df)].nunique())
    refunds = float(df.loc[df["amount"] < 0, "amount"].sum())
    if refunds:
        notes.append(
            f"Revenue is net of {abs(refunds):,.2f} in refunds (negative amounts); "
            "orders that net to zero or less are not counted as orders."
        )

    unique_customers = repeat_rate = None
    if "customer" in df.columns:
        with_customer = df[df["customer"].notna()]
        unique_customers = int(with_customer["customer"].nunique())
        if has_order_id:
            per_customer = _orders_by(with_customer, with_customer["customer"])
            repeat_rate = (
                float((per_customer > 1).sum() / unique_customers) if unique_customers else None
            )
        else:
            notes.append(
                "Repeat customer rate needs an order id column (one order can have several rows)."
            )
        if len(with_customer) < len(df):
            notes.append(
                f"{len(df) - len(with_customer)} row(s) without a customer are left out of "
                "customer KPIs."
            )

    dates = df["date"].dropna()
    return KPIs(
        revenue=revenue,
        orders=orders,
        average_order_value=revenue / orders if orders else None,
        unique_customers=unique_customers,
        repeat_customer_rate=repeat_rate,
        refunds=refunds,
        first_date=dates.min() if not dates.empty else None,
        last_date=dates.max() if not dates.empty else None,
        notes=notes,
    )


def monthly(df: pd.DataFrame) -> pd.DataFrame:
    """Revenue and orders per calendar month, with month-over-month growth.

    - Months with no sales are included with zero revenue.
    - Growth is only computed against a previous month with positive revenue
      (growth from zero or from a net refund month isn't meaningful).
    - The first and last months are marked `partial` when the data doesn't cover
      the whole month, and get no growth figure, since a half month compared with a
      full one would look like a collapse.
    """
    columns = ["month", "revenue", "orders", "growth_pct", "partial"]
    dated = df[df["date"].notna()]
    if dated.empty:
        return pd.DataFrame(columns=columns)
    month = dated["date"].dt.to_period("M")
    table = pd.DataFrame({"revenue": dated["amount"].groupby(month).sum()})
    table["orders"] = _orders_by(dated, month)
    full = pd.period_range(month.min(), month.max(), freq="M")
    table = table.reindex(full, fill_value=0).fillna({"orders": 0})
    table["orders"] = table["orders"].astype(int)

    previous = table["revenue"].shift(1)
    growth = (table["revenue"] - previous) / previous.where(previous > 0) * 100
    first, last = dated["date"].min(), dated["date"].max()
    table["partial"] = False
    if first.day != 1:
        table.loc[full[0], "partial"] = True
    if last.day != last.days_in_month:
        table.loc[full[-1], "partial"] = True
    growth[table["partial"]] = float("nan")
    table["growth_pct"] = growth.astype("float64").round(1)
    table.index = table.index.astype(str)
    return table.rename_axis("month").reset_index()[columns]


def top(df: pd.DataFrame, role: str, n: int = 10) -> pd.DataFrame:
    """Top N values of `role` (product, customer or region) by revenue."""
    if role not in df.columns:
        return pd.DataFrame(columns=[role, "revenue", "orders", "share_pct"])
    rows = df[df[role].notna()]
    grouped = pd.DataFrame({"revenue": rows["amount"].groupby(rows[role]).sum()})
    grouped["orders"] = _orders_by(rows, rows[role])
    grouped["orders"] = grouped["orders"].fillna(0).astype(int)
    if "quantity" in rows.columns:
        grouped["units"] = rows["quantity"].groupby(rows[role]).sum()
    total = df["amount"].sum()
    grouped["share_pct"] = (grouped["revenue"] / total * 100).round(1) if total else float("nan")
    return grouped.sort_values("revenue", ascending=False).head(n).rename_axis(role).reset_index()
