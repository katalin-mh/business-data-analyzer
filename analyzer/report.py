"""Self-contained HTML report: one file, no internet connection needed to view it."""

from datetime import datetime
from importlib.resources import files

import pandas as pd
import plotly.graph_objects as go
from jinja2 import Environment, select_autoescape
from plotly.offline import get_plotlyjs

from analyzer import charts
from analyzer.pipeline import Analysis

# Autoescaping means a product called "<script>" is shown as text, not run.
_env = Environment(autoescape=select_autoescape(default=True, default_for_string=True))
_template = _env.from_string(
    files("analyzer").joinpath("templates/report.html.j2").read_text(encoding="utf-8")
)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _chart(fig: go.Figure) -> str:
    # plotly.js is embedded once in <head>; each chart only carries its data.
    return str(fig.to_html(full_html=False, include_plotlyjs=False, config={"displaylogo": False}))


def _table(df: pd.DataFrame) -> str:
    """HTML table. pandas escapes cell text by default (escape=True)."""
    if df.empty:
        return "<p class='note'>Nothing to show.</p>"
    return df.to_html(index=False, border=0, na_rep="", float_format=lambda v: f"{v:,.2f}")


def render(analysis: Analysis) -> str:
    return _template.render(
        a=analysis,
        k=analysis.kpis,
        fmt=_fmt,
        pct=_pct,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        plotly_js=get_plotlyjs(),
        charts={
            "monthly": _chart(charts.monthly_revenue(analysis.monthly)),
            "products": _chart(charts.top_bar(analysis.top_products, "product", "Top products")),
            "customers": _chart(
                charts.top_bar(analysis.top_customers, "customer", "Top customers")
            ),
            "regions": _chart(charts.top_bar(analysis.regions, "region", "Revenue by region")),
            "distribution": _chart(charts.amount_distribution(analysis.data)),
        },
        tables={
            "monthly": _table(analysis.monthly),
            "products": _table(analysis.top_products),
            "missing": _table(analysis.missing),
            "outliers": _table(analysis.outliers),
            "numeric": _table(analysis.numeric_summary),
        },
    )
