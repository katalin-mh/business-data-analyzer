from pathlib import Path

import pandas as pd
import pytest

from analyzer.sql import QueryError, run_query

DATA = pd.DataFrame({"product": ["a", "b", "a"], "amount": [1.0, 2.0, 3.0]})


def test_select_and_with_queries() -> None:
    result = run_query(
        DATA, "SELECT product, SUM(amount) AS total FROM sales GROUP BY 1 ORDER BY 1"
    )
    assert result.rows.to_dict("records") == [
        {"product": "a", "total": 4.0},
        {"product": "b", "total": 2.0},
    ]
    assert not result.truncated
    assert len(run_query(DATA, "WITH t AS (SELECT * FROM sales) SELECT * FROM t").rows) == 3


@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE sales",
        "INSERT INTO sales VALUES ('c', 1)",
        "CREATE TABLE x AS SELECT 1",
        "COPY sales TO 'leak.csv'",
        "ATTACH 'other.db'",
        "SET enable_external_access = true",
        "INSTALL httpfs",
        "SELECT 1; SELECT 2",
        "PRAGMA database_list",
    ],
)
def test_non_select_statements_are_refused(query: str) -> None:
    with pytest.raises(QueryError):
        run_query(DATA, query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM read_csv('{path}')",
        "SELECT * FROM '{path}'",
        "SELECT * FROM read_text('{path}')",
    ],
)
def test_files_cannot_be_read(tmp_path: Path, query: str) -> None:
    secret = tmp_path / "secret.csv"
    secret.write_text("password\nhunter2\n")
    with pytest.raises(QueryError):
        run_query(DATA, query.format(path=secret.as_posix()))


def test_configuration_cannot_be_unlocked_inside_a_select() -> None:
    with pytest.raises(QueryError):
        run_query(DATA, "SELECT * FROM (SELECT set_config('enable_external_access', 'true'))")


def test_row_limit_flags_truncation() -> None:
    result = run_query(DATA, "SELECT * FROM range(50)", row_limit=10)
    assert len(result.rows) == 10
    assert result.truncated


def test_slow_query_times_out() -> None:
    with pytest.raises(QueryError, match="longer than"):
        # Streams with constant memory, so only the time limit can stop it.
        run_query(DATA, "SELECT SUM(hash(range)) FROM range(1000000000000)", timeout_s=0.5)


def test_syntax_errors_are_reported() -> None:
    with pytest.raises(QueryError):
        run_query(DATA, "SELEC nonsense")
    with pytest.raises(QueryError):
        run_query(DATA, "SELECT missing_column FROM sales")
