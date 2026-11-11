"""Run read-only SQL against the loaded data with DuckDB.

Safety measures, in order:
1. The query text is limited in size and must be exactly one SELECT statement
   (checked by DuckDB's own parser, not a regex).
2. A fresh in-memory database holding only the user's table.
3. External access (files, URLs) and extension installation disabled, then the
   configuration locked so a query can't switch it back on.
4. Resource limits: memory, threads, no spilling to disk, a row limit and a time
   limit that is checked while results are being fetched too.

These limits stop accidental and casual abuse. For a public deployment with
untrusted users, run queries in a separate process with an OS-level memory cap
(see README, "Known limitations").
"""

import threading
import time
from dataclasses import dataclass

import duckdb
import pandas as pd

TABLE = "sales"
DEFAULT_ROW_LIMIT = 1000
DEFAULT_TIMEOUT_S = 5.0
MAX_QUERY_CHARS = 10_000
FETCH_CHUNK = 100

CONFIG: dict[str, str | bool | int | float | list[str]] = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "allow_community_extensions": False,
    "memory_limit": "256MB",
    "threads": 2,
    "max_temp_directory_size": "0B",  # no spilling to disk
    # Neutral values, so duckdb_settings() doesn't reveal the server's home directory.
    "secret_directory": "secrets-disabled",
    "home_directory": "home-disabled",
}


class QueryError(ValueError):
    """The query was refused or failed. The message is safe to show to the user."""


@dataclass
class QueryResult:
    rows: pd.DataFrame
    truncated: bool  # True when more rows existed than the limit


def _check(query: str) -> str:
    if len(query) > MAX_QUERY_CHARS:
        raise QueryError(f"The query is longer than {MAX_QUERY_CHARS:,} characters.")
    try:
        statements = duckdb.extract_statements(query)
    except duckdb.Error as e:
        raise QueryError(f"Could not parse the query: {_first_line(e)}") from None
    if len(statements) != 1:
        raise QueryError("Run exactly one statement at a time.")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise QueryError("Only SELECT queries are allowed.")
    return str(statements[0].query)


def _first_line(error: Exception) -> str:
    message = str(error).split("\n")[0]
    # Permission errors name local directories; don't show server paths to users.
    if "Permission Error" in message or "disabled by configuration" in message:
        return "That function is not available here (no file, network or extension access)."
    return message


def run_query(
    df: pd.DataFrame,
    query: str,
    row_limit: int = DEFAULT_ROW_LIMIT,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> QueryResult:
    statement = _check(query)
    deadline = time.monotonic() + timeout_s
    timeout = QueryError(f"The query took longer than {timeout_s:g} seconds.")

    con = duckdb.connect(":memory:", config=CONFIG)
    timer = threading.Timer(timeout_s, con.interrupt)
    try:
        con.register(TABLE, df)
        con.execute("SET lock_configuration = true")
        timer.start()
        cursor = con.execute(statement)
        names = [d[0] for d in cursor.description or []]
        rows: list[tuple[object, ...]] = []
        # Fetch in small chunks so the time limit also applies while results are
        # converted to Python, which the DuckDB interrupt can't stop.
        while len(rows) <= row_limit:
            if time.monotonic() > deadline:
                raise timeout
            chunk = cursor.fetchmany(FETCH_CHUNK)
            if not chunk:
                break
            rows.extend(chunk)
    except duckdb.InterruptException:
        raise timeout from None
    except duckdb.OutOfMemoryException:
        raise QueryError("The query needs too much memory. Try aggregating or filtering.") from None
    except duckdb.Error as e:
        raise QueryError(_first_line(e)) from None
    finally:
        timer.cancel()
        con.close()

    truncated = len(rows) > row_limit
    return QueryResult(pd.DataFrame(rows[:row_limit], columns=names), truncated)
