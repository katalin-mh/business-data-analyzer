"""Drive the Streamlit app headlessly with Streamlit's AppTest."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "app.py"
TIMEOUT = 60


def start() -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=TIMEOUT)
    app.run()
    return app


def test_starts_with_instructions() -> None:
    app = start()
    assert not app.exception
    assert "Upload a CSV or Excel file" in app.markdown[0].value


def test_sample_data_flow() -> None:
    app = start()
    app.button[0].click().run()  # "Use sample data (synthetic)"
    assert not app.exception
    assert not app.error

    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Revenue"] == "174,350.04"
    assert metrics["Orders"] == "1,200"
    assert "1,218 rows read, 1,200 after cleaning" in app.caption[0].value
    assert [t.label for t in app.tabs] == [
        "Overview",
        "Breakdown",
        "Data quality",
        "SQL",
        "Data",
        "Export",
    ]


def test_sql_tab_runs_queries_and_shows_refusals() -> None:
    app = start()
    app.button[0].click().run()
    app.text_area(key="sql_query").set_value("SELECT COUNT(*) AS n FROM sales").run()
    app.button(key="run_sql").click().run()
    assert not app.error
    sql_tab = app.tabs[3]
    assert sql_tab.dataframe[0].value.iloc[0, 0] == 1200

    app.text_area(key="sql_query").set_value("DROP TABLE sales").run()
    app.button(key="run_sql").click().run()
    assert app.error[0].value == "Only SELECT queries are allowed."


def test_mapping_errors_are_shown() -> None:
    app = start()
    app.button[0].click().run()
    app.selectbox(key="map_amount").set_value("customer").run()
    assert "more than one role" in app.error[0].value

    app.selectbox(key="map_customer").select_index(0).run()  # "—" (no column)
    assert "does not contain numbers" in app.error[0].value
    assert not app.metric  # no KPIs are shown from an invalid mapping
