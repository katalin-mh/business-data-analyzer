# Architecture

## Flow

```
file (CSV / Excel)
   │  loading.py   read every column as text; size, row, encoding and delimiter checks
   v
raw table (all text)
   │  cleaning.py  rename columns, trim, drop empty/duplicate rows, convert numbers and dates
   v                → CleaningReport (every change, with counts)
cleaned table (typed)
   │  schema.py    which column is the date, amount, customer, ...? (auto-detected, user can change)
   v
mapped table (standard names: date, amount, order_id, customer, product, quantity, region)
   │  kpis.py, stats.py
   v
Analysis (pipeline.py) ──> charts.py ──> report.py (HTML)
                      ├──> export.py (Excel workbook, cleaned CSV)
                      ├──> sql.py (read-only DuckDB queries)
                      └──> app.py (Streamlit) / cli.py (command line)
```

The CLI and the Streamlit app are thin layers over `pipeline.py`, so both always produce the same numbers. Everything under `analyzer/` is plain functions on DataFrames with no UI code, which is what makes it testable without a browser.

## Modules

| Module | Responsibility |
| --- | --- |
| `loading.py` | Read CSV (UTF-8, falling back to Windows-1252; delimiter `, ; tab \|` detected) and Excel (`.xlsx`, `.xlsm`; chosen sheet, streamed with openpyxl). Every value is read as text. Limits: 50 MB file, 300 MB unpacked Excel, 1,000,000 rows, 500 columns, 20,000,000 cells, all checked *before or while* parsing so crafted files fail fast. |
| `cleaning.py` | Normalise column names, trim spaces, turn empty cells into missing values, remove empty and (optionally) duplicate rows, and convert number and date columns. Returns a `CleaningReport`. |
| `schema.py` | Map business roles to columns. `detect()` guesses from names and types; `build_mapping()` validates a choice (required roles, no column used twice, right types). |
| `kpis.py` | Revenue, orders, average order value, unique and repeat customers, refunds, monthly revenue with growth, top-N tables. |
| `stats.py` | Missing values, descriptive statistics, IQR outliers, correlations. |
| `sql.py` | Read-only SQL over the cleaned data with DuckDB. |
| `charts.py` | Plotly figures shared by the app and the report. |
| `report.py` + `templates/report.html.j2` | One self-contained HTML file (plotly.js embedded, so it opens offline). |
| `export.py` | Excel workbook (KPIs, Monthly, Top products, Top customers, Regions, Cleaning log, Data) and cleaned CSV. |
| `pipeline.py` | `prepare()` cleans and guesses the mapping; `analyze()` computes everything into an `Analysis` dataclass. |
| `cli.py`, `app.py` | User interfaces. |

## Cleaning rules

**Why read everything as text first?** If pandas guessed the types while reading, `"1.234,50"` would silently stay text or `"007"` would lose its zeros. Reading text and converting in one place means each conversion can be counted and explained.

**Numbers.** A column is converted when at least 90% of its non-empty values look numeric once currency symbols (€, $, £, EUR, USD, RON, lei, GBP) and spaces are removed. Accounting negatives `(40.00)` become `-40.00`, and scientific notation (`2.5E+3`) is accepted.

Thousands groups must have exactly three digits, so `01.02.2025` is not mistaken for a number and is left for the date step.

**Identifiers stay text** even when they are all digits:
- names containing `id`, `code`, `sku`, `ean`, `zip`, `postcode`, `phone` or `iban`
- values with a leading zero (`0042`)
- values with more than 15 digits (a float can't store those exactly, so two different ids could become equal)

The decimal separator is decided per value when it can be (`1,234.50` vs `1.234,50`). Values that are ambiguous (`1,234` could be 1234 or 1.234) follow the column's majority style, and the report says how many there were.

A column that mixes currencies is left as text with a warning, because adding euros to dollars would give a meaningless total.

**Dates.** ISO dates (`2025-03-01`, with or without a time) are parsed first. Values with a time zone (`+02:00`, `Z`) are converted to UTC and the count is reported. Only values shaped like dates are parsed, so a plain `1001` is never read as the year 1001. Years outside 1900–2200 are treated as unreadable. For `DD/MM/YYYY` vs `MM/DD/YYYY`, the column is inspected: a first part above 12 means day first, a second part above 12 means month first. If nothing decides it, the user's option applies (day first by default) and the report notes the ambiguity.

**Unreadable values** become missing, are counted per column, and are listed in the report. Rows are never dropped because of one bad cell.

**Duplicates.** Exact duplicate rows are removed by default, but this is a checkbox. Two identical lines might be a real repeat purchase, and only the user knows.

## KPI definitions

| KPI | Definition |
| --- | --- |
| Revenue | Sum of `amount`, net of refunds (negative amounts). Rows without an amount are excluded and counted in a note. |
| Orders | Distinct `order_id` values whose lines have an amount and add up to more than zero; fully refunded orders don't count. Without an order id column, each row is one order. |
| Average order value | Revenue ÷ orders (n/a when there are no orders). |
| Unique customers | Distinct customers among rows that have one. |
| Repeat customer rate | Share of those customers with more than one order. Needs an order id column; without one it is n/a, because one basket can span several rows. |
| Monthly growth | Change from the previous calendar month. Months with no sales are shown as zero. Growth is only computed against a previous month with positive revenue. The first and last month are marked `partial` if the data doesn't cover the whole month, and get no growth figure (a half month compared with a full one would look like a collapse). |
| Share % | A product's (or customer's or region's) revenue ÷ total revenue. |

## SQL safety

Users can type SQL, so `sql.py` treats it as untrusted:

1. **Query text of at most 10,000 characters.**
2. **One statement, and only `SELECT`.** DuckDB's own parser (`extract_statements`) checks this, not a regex, so tricks with comments or semicolons don't work.
3. **A fresh in-memory database per query** that holds only the user's table.
4. **No external access.** `enable_external_access = false`, extension auto-install and auto-load are off, and the home and secret directories are set to neutral values, so `duckdb_settings()` doesn't reveal server paths.
5. **`lock_configuration = true`.** A query can't switch any of that back on.
6. **Resource limits:**
   - 256 MB of memory, 2 threads, and no spilling to disk.
   - At most 1,000 rows are returned, and longer results are flagged.
   - A 5-second limit. A timer interrupts DuckDB, and results are fetched in chunks of 100 so the limit is also checked while rows are converted to Python, which the interrupt alone can't stop.

The security review could not read files, write files, reach the network or change settings through this. The remaining risk is resource use within those limits, since everything runs in one process (see README, "Known limitations").

## Output safety

- **Spreadsheet formula injection.** Text cells starting with `= + - @` (or tab / carriage return) are prefixed with `'` in CSV and Excel exports. Control characters are removed first, so they can't hide a formula and don't make the workbook invalid. A product named `=HYPERLINK(...)` is therefore shown as text, not run as a formula.
- **Streamlit:** tracebacks are hidden from users (`showErrorDetails = "none"`), and expected problems are shown as plain messages. Caches expire after an hour.
- **HTML report.** Jinja2 autoescaping and pandas `to_html` (which escapes by default) mean a product called `<script>` is displayed, not executed. plotly.js is embedded, so the report makes no network requests.

## Design decisions

| Decision | Why |
| --- | --- |
| pandas + DuckDB | pandas for cleaning and KPIs (the industry default), and DuckDB to give users real SQL over the same DataFrame without a database server. |
| Streamlit for the UI | A full interactive app in one Python file, which suits a data tool. The analysis logic stays outside it, so the UI could be replaced. |
| Per-column format decisions | Guessing cell by cell can read one column half as US and half as European numbers without anyone noticing. |
| `Float64` / `string` pandas dtypes | They keep missing values as `<NA>` instead of turning integers into floats or text into the string `"nan"`. |
| argparse, not click | Standard library: one less dependency. |
| Synthetic sample data from a seeded script | Reproducible, contains realistic mess for the cleaning step, and can't be mistaken for real customer data. |
