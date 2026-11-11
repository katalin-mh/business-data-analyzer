"""The full analysis in one call, shared by the CLI and the Streamlit app."""

from dataclasses import dataclass

import pandas as pd

from analyzer import kpis, schema, stats
from analyzer.cleaning import CleaningOptions, CleaningReport, clean


@dataclass
class Analysis:
    source_name: str
    cleaned: pd.DataFrame  # all columns, typed
    cleaning: CleaningReport
    mapping: schema.Mapping
    data: pd.DataFrame  # mapped columns only, renamed to roles
    kpis: kpis.KPIs
    monthly: pd.DataFrame
    top_products: pd.DataFrame
    top_customers: pd.DataFrame
    regions: pd.DataFrame
    missing: pd.DataFrame
    numeric_summary: pd.DataFrame
    outliers: pd.DataFrame
    correlations: pd.DataFrame


def prepare(
    raw: pd.DataFrame, options: CleaningOptions | None = None
) -> tuple[pd.DataFrame, CleaningReport, dict[str, str | None]]:
    """Clean the raw table and guess the column mapping (step 1, before the user confirms it)."""
    cleaned, report = clean(raw, options)
    return cleaned, report, schema.detect(cleaned)


def analyze(
    source_name: str,
    cleaned: pd.DataFrame,
    report: CleaningReport,
    chosen: dict[str, str | None],
    top_n: int = 10,
) -> Analysis:
    """Validate the mapping and compute every KPI and statistic (step 2)."""
    mapping = schema.build_mapping(cleaned, chosen)
    data = schema.apply(cleaned, mapping)
    return Analysis(
        source_name=source_name,
        cleaned=cleaned,
        cleaning=report,
        mapping=mapping,
        data=data,
        kpis=kpis.summary(data),
        monthly=kpis.monthly(data),
        top_products=kpis.top(data, "product", top_n),
        top_customers=kpis.top(data, "customer", top_n),
        regions=kpis.top(data, "region", 50),
        missing=stats.missing_values(cleaned),
        numeric_summary=stats.describe_numeric(cleaned),
        outliers=stats.outlier_summary(cleaned),
        correlations=stats.correlations(cleaned),
    )


def run(raw: pd.DataFrame, source_name: str, options: CleaningOptions | None = None) -> Analysis:
    """Clean, auto-map and analyse in one step (used by the CLI)."""
    cleaned, report, guess = prepare(raw, options)
    return analyze(source_name, cleaned, report, guess)
