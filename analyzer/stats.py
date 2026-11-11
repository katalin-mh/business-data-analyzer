"""Descriptive statistics that don't depend on the business meaning of columns."""

import pandas as pd


def missing_values(df: pd.DataFrame) -> pd.DataFrame:
    counts = df.isna().sum()
    table = pd.DataFrame(
        {"missing": counts, "missing_pct": (counts / max(len(df), 1) * 100).round(1)}
    )
    return table.rename_axis("column").reset_index()


def describe_numeric(df: pd.DataFrame) -> pd.DataFrame:
    numeric = df.select_dtypes("number")
    if numeric.empty:
        return pd.DataFrame()
    table = numeric.describe(percentiles=[0.25, 0.5, 0.75]).T
    return table.rename(columns={"50%": "median"}).rename_axis("column").reset_index()


def iqr_outliers(series: pd.Series, k: float = 1.5) -> pd.Series:
    """Boolean mask of values outside [Q1 - k*IQR, Q3 + k*IQR] (Tukey's rule)."""
    values = series.astype("float64")
    q1, q3 = values.quantile(0.25), values.quantile(0.75)
    iqr = q3 - q1
    mask = (values < q1 - k * iqr) | (values > q3 + k * iqr)
    return mask.fillna(False).astype(bool)


def outlier_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in df.select_dtypes("number").columns:
        mask = iqr_outliers(df[col])
        rows.append({"column": col, "outliers": int(mask.sum())})
    return pd.DataFrame(rows, columns=["column", "outliers"])


def correlations(df: pd.DataFrame) -> pd.DataFrame:
    numeric = df.select_dtypes("number").astype("float64")
    if numeric.shape[1] < 2:
        return pd.DataFrame()
    return numeric.corr().round(3)
