"""Turn a raw table (all text) into typed, tidy data, and record every change.

Rules:
- Nothing is changed or dropped silently: each step adds an entry to the report.
- Number and date formats are decided once per column, not guessed cell by cell,
  so a column is never half-parsed one way and half another.
- The input DataFrame is never modified.
"""

import re
from dataclasses import dataclass, field

import pandas as pd

# A column is converted when at least this share of its non-empty values parse.
CONVERT_THRESHOLD = 0.9
CURRENCY_MARKERS = {"€": "EUR", "$": "USD", "£": "GBP", "EUR": "EUR", "USD": "USD", "RON": "RON",
                    "LEI": "RON", "GBP": "GBP"}  # fmt: skip
_CURRENCY_RE = re.compile(r"€|\$|£|\b(?:EUR|USD|RON|LEI|GBP)\b", re.IGNORECASE)
# 1234 | 1234.5 | 1,234 | 1.234.567,89 | .5 — thousands groups must have exactly 3 digits,
# so a date like 01.02.2025 is not mistaken for a number.
_NUMBER_RE = re.compile(r"^[+-]?(\d{1,3}([.,]\d{3})+|\d+)?([.,]\d+)?$")
_SCIENTIFIC_RE = re.compile(r"^[+-]?\d+(\.\d+)?[eE][+-]?\d+$")
_SLASH_DATE_RE = re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$")
_TZ_RE = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")
MIN_YEAR, MAX_YEAR = 1900, 2200
# Columns holding identifiers stay text even if they look numeric: "0042" must not become 42.
_ID_NAME_RE = re.compile(r"(^|_)(id|code|sku|ean|zip|postcode|postal_code|phone|iban)($|_)")
MAX_EXACT_DIGITS = 15  # beyond this, float64 can't store integers exactly


@dataclass
class CleaningOptions:
    drop_duplicates: bool = True
    # How to read 03/04/2025 when a column gives no clue (no day above 12).
    dayfirst: bool = True


@dataclass
class ColumnChange:
    column: str
    converted_to: str  # "number" or "date"
    unparseable: int  # values that became missing because they couldn't be read
    note: str = ""


@dataclass
class CleaningReport:
    rows_in: int = 0
    rows_out: int = 0
    renamed_columns: dict[str, str] = field(default_factory=dict)
    empty_rows_removed: int = 0
    duplicate_rows: int = 0
    duplicates_removed: bool = True
    cells_trimmed: int = 0
    conversions: list[ColumnChange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> list[str]:
        """Human-readable list of what happened, for the UI and the report."""
        lines = []
        renamed = {k: v for k, v in self.renamed_columns.items() if k != v}
        if renamed:
            lines.append(f"Renamed {len(renamed)} column(s) to snake_case.")
        if self.cells_trimmed:
            lines.append(f"Trimmed surrounding spaces in {self.cells_trimmed} cell(s).")
        if self.empty_rows_removed:
            lines.append(f"Removed {self.empty_rows_removed} completely empty row(s).")
        if self.duplicate_rows:
            action = "Removed" if self.duplicates_removed else "Kept"
            lines.append(f"{action} {self.duplicate_rows} exact duplicate row(s).")
        for c in self.conversions:
            text = f"Converted '{c.column}' to {c.converted_to}"
            if c.unparseable:
                text += f"; {c.unparseable} value(s) could not be read and are now empty"
            lines.append(text + (f" ({c.note})" if c.note else "") + ".")
        lines.extend(f"Warning: {w}" for w in self.warnings)
        return lines


def normalise_name(name: str) -> str:
    """'  Order Date ' -> 'order_date'."""
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip()).strip("_").lower()
    return cleaned or "column"


def _unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for name in names:
        count = seen.get(name, 0)
        seen[name] = count + 1
        result.append(name if count == 0 else f"{name}_{count + 1}")
    return result


# --- numbers -------------------------------------------------------------------


def _number_style(value: str) -> str:
    """Classify a cleaned numeric string: 'us' (1,234.5), 'eu' (1.234,5), 'plain' or 'ambiguous'.

    'ambiguous' means a single separator followed by exactly three digits, e.g.
    '1,234' or '1.234', which could be a thousands separator or a decimal point.
    """
    digits = value.lstrip("+-")
    has_comma, has_dot = "," in digits, "." in digits
    if has_comma and has_dot:
        return "us" if digits.rfind(".") > digits.rfind(",") else "eu"
    if not has_comma and not has_dot:
        return "plain"
    sep = "," if has_comma else "."
    parts = digits.split(sep)
    if len(parts) > 2:  # 1,234,567 or 1.234.567: only thousands separators repeat
        return "us" if sep == "," else "eu"
    if len(parts[1]) == 3:
        return "ambiguous"
    return "eu" if sep == "," else "us"


def _is_number(value: str) -> bool:
    return value != "" and bool(_NUMBER_RE.match(value) or _SCIENTIFIC_RE.match(value))


def _parse_number(value: str, style: str) -> float | None:
    if _SCIENTIFIC_RE.match(value):
        return float(value)
    if value == "" or not _NUMBER_RE.match(value):
        return None
    # Drop the thousands separator, then make the decimal separator a dot.
    eu = style == "eu"
    value = value.replace(".", "").replace(",", ".") if eu else value.replace(",", "")
    try:
        return float(value)
    except ValueError:
        return None


def _strip(value: str) -> str:
    """Remove currency markers and spaces; turn accounting negatives '(40.00)' into '-40.00'."""
    value = _CURRENCY_RE.sub("", value).replace(" ", "").replace(" ", "")
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    return value


def _looks_like_identifier(name: str, numbers: pd.Series) -> bool:
    """True for ids, codes and phone numbers, which must stay text even if all digits."""
    if _ID_NAME_RE.search(name):
        return True
    has_leading_zero = numbers.str.match(r"^[+-]?0\d").any()  # "0042", but not "0.5"
    too_long = (numbers.str.count(r"\d") > MAX_EXACT_DIGITS).any()
    return bool(has_leading_zero or too_long)


def _try_numeric(series: pd.Series) -> tuple[pd.Series, ColumnChange, list[str]] | None:
    values = series.dropna()
    if values.empty:
        return None
    stripped = values.map(_strip)
    readable = stripped[stripped.map(_is_number)]
    if len(readable) / len(values) < CONVERT_THRESHOLD:
        return None
    if _looks_like_identifier(str(series.name), readable):
        return None
    currencies = {CURRENCY_MARKERS[m.upper()] for v in values for m in _CURRENCY_RE.findall(v)}
    if len(currencies) > 1:
        warning = (
            f"Column '{series.name}' mixes currencies ({', '.join(sorted(currencies))}); "
            "it was left as text because the amounts can't be added up."
        )
        return series, ColumnChange(str(series.name), "text", 0), [warning]

    # Styles are counted over readable values only, so junk can't sway the decision.
    styles = readable.map(_number_style)
    counts = styles.value_counts()
    # Ambiguous values follow the column's majority; default to 1,234.50 style.
    column_style = "eu" if counts.get("eu", 0) > counts.get("us", 0) else "us"

    def parse(v: str) -> float | None:
        style = _number_style(v)
        return _parse_number(v, column_style if style in ("ambiguous", "plain") else style)

    parsed = stripped.map(parse)
    result = pd.Series(pd.NA, index=series.index, dtype="Float64")
    result.loc[parsed.index] = pd.array(parsed.tolist(), dtype="Float64")

    notes = []
    other = "eu" if column_style == "us" else "us"
    if counts.get(other, 0):
        notes.append(f"{counts[other]} value(s) used the other decimal style and were read as such")
    ambiguous = readable[styles == "ambiguous"]
    if not ambiguous.empty:
        example = ambiguous.iloc[0]
        notes.append(
            f"{len(ambiguous)} ambiguous value(s) such as '{example}' read as "
            f"{parse(example):g}, following the rest of the column"
        )
    in_parentheses = int(values.str.fullmatch(r"\s*\(.*\)\s*").sum())
    if in_parentheses:
        notes.append(f"{in_parentheses} value(s) in parentheses read as negative")
    if currencies:
        notes.append(f"currency symbol {next(iter(currencies))} removed")
    unparseable = int(parsed.isna().sum())
    return result, ColumnChange(str(series.name), "number", unparseable, "; ".join(notes)), []


# --- dates ---------------------------------------------------------------------


def _try_dates(series: pd.Series, dayfirst: bool) -> tuple[pd.Series, ColumnChange] | None:
    values = series.dropna()
    if values.empty:
        return None
    # Values with a time zone ("...+02:00", "...Z") are converted to UTC, then all
    # dates are stored without a zone so they can be compared with each other.
    with_tz = int(values.str.contains(_TZ_RE).sum())
    # Only values shaped like dates are parsed: a plain "1001" is not the year 1001.
    iso_like = values.where(values.str.match(_ISO_DATE_RE))
    iso = pd.to_datetime(iso_like, format="ISO8601", errors="coerce", utc=True).dt.tz_localize(None)
    rest = values[iso.isna()]

    notes = [f"{with_tz} value(s) with a time zone converted to UTC"] if with_tz else []
    slash = rest.str.extract(_SLASH_DATE_RE).dropna().astype(int)
    if not slash.empty:
        if (slash[0] > 12).any() and not (slash[1] > 12).any():
            dayfirst = True
        elif (slash[1] > 12).any() and not (slash[0] > 12).any():
            dayfirst = False
        else:
            notes.append(f"day/month order unclear; read as {'DD/MM' if dayfirst else 'MM/DD'}")
    fmt = "%d/%m/%Y" if dayfirst else "%m/%d/%Y"
    other = pd.to_datetime(rest.str.replace(".", "/", regex=False), format=fmt, errors="coerce")

    parsed = iso.combine_first(other)
    # Years like 0025 are typos, and pandas can't store them in nanosecond precision.
    plausible = parsed.dt.year.between(MIN_YEAR, MAX_YEAR)
    parsed = parsed.where(plausible).astype("datetime64[ns]")
    if parsed.notna().mean() < CONVERT_THRESHOLD:
        return None
    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    result.loc[parsed.index] = parsed
    unparseable = int(parsed.isna().sum())
    return result, ColumnChange(str(series.name), "date", unparseable, "; ".join(notes))


# --- main entry point -----------------------------------------------------------


def clean(
    raw: pd.DataFrame, options: CleaningOptions | None = None
) -> tuple[pd.DataFrame, CleaningReport]:
    options = options or CleaningOptions()
    report = CleaningReport(rows_in=len(raw), duplicates_removed=options.drop_duplicates)
    df = raw.copy()

    plain = [normalise_name(c) for c in df.columns]
    names = _unique_names(plain)
    report.renamed_columns = dict(zip(map(str, raw.columns), names, strict=True))
    clashes = sorted({p for p, n in zip(plain, names, strict=True) if p != n})
    if clashes:
        report.warnings.append(
            f"Several columns had the same name after renaming ({', '.join(clashes)}); "
            "a number was added to keep them apart."
        )
    df.columns = names

    # Trim text and treat empty cells as missing.
    for col in df.columns:
        text = df[col].astype("string")
        trimmed = text.str.strip()
        report.cells_trimmed += int((trimmed != text).fillna(False).sum())
        df[col] = trimmed.mask(trimmed == "")

    empty = df.isna().all(axis=1)
    report.empty_rows_removed = int(empty.sum())
    df = df[~empty]

    duplicated = df.duplicated()
    report.duplicate_rows = int(duplicated.sum())
    if options.drop_duplicates:
        df = df[~duplicated]

    for col in df.columns:
        numeric = _try_numeric(df[col])
        if numeric is not None:
            converted, change, warnings = numeric
            report.warnings.extend(warnings)
            if change.converted_to == "number":
                df[col] = converted
                report.conversions.append(change)
            continue
        dates = _try_dates(df[col], options.dayfirst)
        if dates is not None:
            df[col], change = dates
            report.conversions.append(change)

    df = df.reset_index(drop=True)
    report.rows_out = len(df)
    return df, report
