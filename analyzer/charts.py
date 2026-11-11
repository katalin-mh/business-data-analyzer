"""Plotly figures used by both the app and the HTML report."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

ACCENT = "#2a6f97"
LAYOUT = {"template": "plotly_white", "margin": {"l": 10, "r": 10, "t": 50, "b": 10}, "height": 380}


def _empty(title: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text="No data", showarrow=False, font={"size": 16})
    fig.update_layout(title=title, xaxis_visible=False, yaxis_visible=False, **LAYOUT)
    return fig


def monthly_revenue(monthly: pd.DataFrame) -> go.Figure:
    title = "Revenue by month"
    if monthly.empty:
        return _empty(title)
    fig = px.line(monthly, x="month", y="revenue", markers=True, title=title)
    fig.update_traces(line_color=ACCENT, hovertemplate="%{x}<br>Revenue: %{y:,.2f}<extra></extra>")
    fig.update_layout(xaxis_title=None, yaxis_title="Revenue", **LAYOUT)
    return fig


def top_bar(table: pd.DataFrame, role: str, title: str) -> go.Figure:
    if table.empty:
        return _empty(title)
    ordered = table.sort_values("revenue")
    fig = px.bar(ordered, x="revenue", y=role, orientation="h", title=title)
    fig.update_traces(
        marker_color=ACCENT, hovertemplate="%{y}<br>Revenue: %{x:,.2f}<extra></extra>"
    )
    fig.update_layout(xaxis_title="Revenue", yaxis_title=None, **LAYOUT)
    return fig


def amount_distribution(data: pd.DataFrame) -> go.Figure:
    title = "Distribution of order line amounts"
    amounts = data["amount"].dropna().astype("float64")
    if amounts.empty:
        return _empty(title)
    fig = px.histogram(amounts, nbins=40, title=title)
    fig.update_traces(marker_color=ACCENT, hovertemplate="%{x}<br>Rows: %{y}<extra></extra>")
    fig.update_layout(xaxis_title="Amount", yaxis_title="Rows", showlegend=False, **LAYOUT)
    return fig
