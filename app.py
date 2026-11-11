"""Streamlit interface. Run with: streamlit run app.py"""

import hashlib
import io
from pathlib import Path

import pandas as pd
import streamlit as st

from analyzer import charts, export, report, schema, sql
from analyzer.cleaning import CleaningOptions, CleaningReport
from analyzer.loading import InputError, excel_sheets, load_csv, load_excel
from analyzer.pipeline import Analysis, analyze, prepare

SAMPLE = Path(__file__).parent / "samples" / "sales_synthetic.csv"
ROLE_LABELS = {
    "date": "Date *",
    "amount": "Amount *",
    "order_id": "Order id",
    "customer": "Customer",
    "product": "Product",
    "quantity": "Quantity",
    "region": "Region",
}

st.set_page_config(page_title="Business Data Analyzer", layout="wide")


@st.cache_data(show_spinner="Reading and cleaning the file…", max_entries=5, ttl=3600)
def load_and_prepare(
    data: bytes, name: str, sheet: str | None, drop_duplicates: bool, dayfirst: bool
) -> tuple[pd.DataFrame, CleaningReport, dict[str, str | None]]:
    buffer = io.BytesIO(data)
    raw = (
        load_excel(buffer, sheet or 0)
        if name.lower().endswith((".xlsx", ".xlsm"))
        else load_csv(buffer)
    )
    return prepare(raw, CleaningOptions(drop_duplicates=drop_duplicates, dayfirst=dayfirst))


@st.cache_data(max_entries=5, ttl=3600)
def sheet_names(data: bytes) -> list[str]:
    return excel_sheets(io.BytesIO(data))


@st.cache_data(show_spinner="Building the report files…", max_entries=3, ttl=3600)
def build_exports(
    data: bytes,
    name: str,
    sheet: str | None,
    drop_duplicates: bool,
    dayfirst: bool,
    mapping: tuple[tuple[str, str | None], ...],
) -> tuple[str, bytes, bytes]:
    """HTML report, Excel workbook and cleaned CSV. Cached on everything they depend on."""
    cleaned, cleaning_report, _ = load_and_prepare(data, name, sheet, drop_duplicates, dayfirst)
    analysis = analyze(name, cleaned, cleaning_report, dict(mapping))
    return report.render(analysis), export.workbook(analysis), export.cleaned_csv(analysis)


def money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def sidebar() -> tuple[bytes, str, str | None, CleaningOptions] | None:
    st.sidebar.header("Data")
    upload = st.sidebar.file_uploader("CSV or Excel file", type=["csv", "xlsx", "xlsm"])
    if st.sidebar.button("Use sample data (synthetic)"):
        st.session_state["use_sample"] = True
    if upload is not None:
        st.session_state["use_sample"] = False

    if upload is not None:
        data, name = upload.getvalue(), upload.name
    elif st.session_state.get("use_sample"):
        data, name = SAMPLE.read_bytes(), SAMPLE.name
    else:
        return None

    sheet = None
    if name.lower().endswith((".xlsx", ".xlsm")):
        try:
            sheet = st.sidebar.selectbox("Sheet", sheet_names(data))
        except InputError as e:
            st.error(str(e))
            return None

    st.sidebar.header("Cleaning options")
    drop = st.sidebar.checkbox("Remove exact duplicate rows", value=True)
    order = st.sidebar.radio(
        "Dates like 03/04/2025 mean", ["3 April (day first)", "March 4 (month first)"]
    )
    return data, name, sheet, CleaningOptions(drop_duplicates=drop, dayfirst=order.startswith("3"))


def mapping_form(cleaned: pd.DataFrame, guess: dict[str, str | None]) -> dict[str, str | None]:
    st.subheader("Which column is which?")
    st.caption("Detected automatically from the column names. Fields marked * are required.")
    options = [None, *cleaned.columns]
    chosen: dict[str, str | None] = {}
    cols = st.columns(len(schema.ROLES))
    for col, role in zip(cols, schema.ROLES, strict=True):
        default = options.index(guess[role]) if guess[role] in options else 0
        chosen[role] = col.selectbox(
            ROLE_LABELS[role],
            options,
            index=default,
            key=f"map_{role}",
            format_func=lambda v: "—" if v is None else str(v),
        )
    return chosen


def overview(a: Analysis) -> None:
    k = a.kpis
    c = st.columns(5)
    c[0].metric("Revenue", money(k.revenue))
    c[1].metric("Orders", f"{k.orders:,}")
    c[2].metric("Average order value", money(k.average_order_value))
    c[3].metric(
        "Unique customers", "n/a" if k.unique_customers is None else f"{k.unique_customers:,}"
    )
    rate = k.repeat_customer_rate
    c[4].metric("Repeat customers", "n/a" if rate is None else f"{rate:.1%}")
    if k.first_date is not None and k.last_date is not None:
        st.caption(f"Period: {k.first_date.date()} to {k.last_date.date()}")
    for note in k.notes:
        st.info(note)
    st.plotly_chart(charts.monthly_revenue(a.monthly), use_container_width=True)
    st.dataframe(a.monthly, hide_index=True, use_container_width=True)


def breakdown(a: Analysis) -> None:
    left, right = st.columns(2)
    left.plotly_chart(
        charts.top_bar(a.top_products, "product", "Top products"), use_container_width=True
    )
    right.plotly_chart(
        charts.top_bar(a.top_customers, "customer", "Top customers"), use_container_width=True
    )
    left.plotly_chart(
        charts.top_bar(a.regions, "region", "Revenue by region"), use_container_width=True
    )
    right.plotly_chart(charts.amount_distribution(a.data), use_container_width=True)
    st.dataframe(a.top_products, hide_index=True, use_container_width=True)


def quality(a: Analysis) -> None:
    st.markdown(
        "\n".join(f"- {line}" for line in a.cleaning.summary()) or "No changes were needed."
    )
    left, right = st.columns(2)
    left.markdown("**Missing values**")
    left.dataframe(a.missing, hide_index=True, use_container_width=True)
    right.markdown("**Outliers (IQR rule)**")
    right.dataframe(a.outliers, hide_index=True, use_container_width=True)
    st.markdown("**Numeric columns**")
    st.dataframe(a.numeric_summary, hide_index=True, use_container_width=True)
    if not a.correlations.empty:
        st.markdown("**Correlations**")
        st.dataframe(a.correlations, use_container_width=True)


def sql_tab(a: Analysis) -> None:
    st.caption(
        f"Query the cleaned data as table `{sql.TABLE}` (columns: {', '.join(a.cleaned.columns)}). "
        "Read-only: one SELECT statement, up to 1,000 rows, 5 second limit."
    )
    query = st.text_area(
        "SQL",
        value=f"SELECT * FROM {sql.TABLE} LIMIT 20",  # noqa: S608  (constant table name)
        height=120,
        key="sql_query",
    )
    if st.button("Run query", key="run_sql"):
        try:
            result = sql.run_query(a.cleaned, query)
        except sql.QueryError as e:
            st.error(str(e))
            return
        if result.truncated:
            st.warning("Showing the first 1,000 rows.")
        st.dataframe(result.rows, hide_index=True, use_container_width=True)


def downloads(
    selection: tuple[bytes, str, str | None, CleaningOptions], chosen: dict[str, str | None]
) -> None:
    """Files are built only on request (a large workbook can take seconds), then cached."""
    data, name, sheet, options = selection
    args = (data, name, sheet, options.drop_duplicates, options.dayfirst, tuple(chosen.items()))
    signature = (hashlib.sha256(data).hexdigest(), *args[1:])
    if st.button("Prepare downloads", key="prepare_downloads"):
        st.session_state["exports_for"] = signature
    if st.session_state.get("exports_for") != signature:
        st.caption("Builds an HTML report, an Excel workbook and the cleaned data as CSV.")
        return
    html, xlsx, csv = build_exports(*args)
    stem = Path(name).stem
    st.download_button("HTML report", html, f"{stem}_report.html", "text/html")
    xlsx_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    st.download_button("Excel workbook", xlsx, f"{stem}_report.xlsx", xlsx_type)
    st.download_button("Cleaned data (CSV)", csv, f"{stem}_cleaned.csv", "text/csv")


def reset_mapping_for_new_file(data: bytes, sheet: str | None) -> None:
    """Forget the previous file's column choices so the new file's detection applies."""
    file_id = (hashlib.sha256(data).hexdigest(), sheet)
    if st.session_state.get("file_id") != file_id:
        for role in schema.ROLES:
            st.session_state.pop(f"map_{role}", None)
        st.session_state["file_id"] = file_id


def main() -> None:
    st.title("Business Data Analyzer")
    selection = sidebar()
    if selection is None:
        st.write("Upload a CSV or Excel file with sales data, or try the synthetic sample data.")
        return
    data, name, sheet, options = selection
    try:
        cleaned, cleaning_report, guess = load_and_prepare(
            data, name, sheet, options.drop_duplicates, options.dayfirst
        )
    except InputError as e:
        st.error(str(e))
        return

    rows_in, rows_out = cleaning_report.rows_in, cleaning_report.rows_out
    st.caption(f"{name}: {rows_in:,} rows read, {rows_out:,} after cleaning")
    reset_mapping_for_new_file(data, sheet)
    chosen = mapping_form(cleaned, guess)
    try:
        analysis = analyze(name, cleaned, cleaning_report, chosen)
    except schema.MappingError as e:
        st.error(str(e))
        return

    tabs = st.tabs(["Overview", "Breakdown", "Data quality", "SQL", "Data", "Export"])
    with tabs[0]:
        overview(analysis)
    with tabs[1]:
        breakdown(analysis)
    with tabs[2]:
        quality(analysis)
    with tabs[3]:
        sql_tab(analysis)
    with tabs[4]:
        st.dataframe(analysis.cleaned, hide_index=True, use_container_width=True)
    with tabs[5]:
        downloads(selection, chosen)


main()
