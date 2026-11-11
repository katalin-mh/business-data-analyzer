"""Writing results: cleaned CSV and a multi-sheet Excel workbook."""

import io
from typing import Any

import pandas as pd
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from analyzer.pipeline import Analysis

# A cell starting with one of these is run as a formula by Excel and other
# spreadsheet apps ("CSV injection"). Prefixing a quote makes it plain text.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    # Control characters (common in POS/ERP exports) are invalid in .xlsx files,
    # and a leading one could hide a formula from the check below.
    value = ILLEGAL_CHARACTERS_RE.sub("", value)
    if value.startswith(_FORMULA_START):
        return "'" + value
    return value


def make_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of df with text cells neutralised and pandas NA turned into None."""
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_string_dtype(out[col]) or out[col].dtype == object:
            out[col] = out[col].astype(object).map(_safe_cell)
    return out.astype(object).where(out.notna(), None)


def cleaned_csv(analysis: Analysis) -> bytes:
    # utf-8-sig so Excel opens accented names correctly.
    return make_safe(analysis.cleaned).to_csv(index=False).encode("utf-8-sig")


def kpi_table(analysis: Analysis) -> pd.DataFrame:
    k = analysis.kpis
    rows: list[tuple[str, Any]] = [
        ("Source file", analysis.source_name),
        ("First date", k.first_date.date() if k.first_date is not None else None),
        ("Last date", k.last_date.date() if k.last_date is not None else None),
        ("Revenue", round(k.revenue, 2)),
        ("Orders", k.orders),
        ("Average order value", round(k.average_order_value, 2) if k.average_order_value else None),
        ("Unique customers", k.unique_customers),
        (
            "Repeat customer rate (%)",
            round(k.repeat_customer_rate * 100, 1) if k.repeat_customer_rate is not None else None,
        ),
        ("Refunds", round(k.refunds, 2)),
    ]
    rows += [("Note", n) for n in k.notes]
    return pd.DataFrame(rows, columns=["metric", "value"])


def workbook(analysis: Analysis) -> bytes:
    sheets = {
        "KPIs": kpi_table(analysis),
        "Monthly": analysis.monthly,
        "Top products": analysis.top_products,
        "Top customers": analysis.top_customers,
        "Regions": analysis.regions,
        "Cleaning log": pd.DataFrame({"step": analysis.cleaning.summary()}),
        "Data": analysis.cleaned,
    }
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, table in sheets.items():
            make_safe(table).to_excel(writer, sheet_name=name, index=False)
    return buffer.getvalue()
