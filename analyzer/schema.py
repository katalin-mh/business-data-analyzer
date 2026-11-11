"""Which column means what. Later modules only use these standard names."""

from dataclasses import dataclass

import pandas as pd

REQUIRED = ("date", "amount")
OPTIONAL = ("order_id", "customer", "product", "quantity", "region")
ROLES = REQUIRED + OPTIONAL

# Column names (after cleaning.normalise_name) that usually mean each role, best first.
HINTS: dict[str, tuple[str, ...]] = {
    "date": ("order_date", "date", "invoice_date", "sale_date", "transaction_date", "created_at"),
    "amount": ("amount", "total", "revenue", "sales", "net_amount", "line_total"),
    "order_id": ("order_id", "order", "invoice_id", "invoice", "order_number", "transaction_id"),
    "customer": ("customer", "customer_id", "customer_name", "client", "client_id", "buyer"),
    "product": ("product", "product_name", "item", "sku", "description", "article"),
    "quantity": ("quantity", "qty", "units", "count"),
    "region": ("region", "country", "city", "area", "territory"),
}


class MappingError(ValueError):
    """The column mapping is unusable. The message is safe to show to the user."""


@dataclass
class Mapping:
    """Role -> column name in the cleaned data. Optional roles may be None."""

    date: str
    amount: str
    order_id: str | None = None
    customer: str | None = None
    product: str | None = None
    quantity: str | None = None
    region: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {role: col for role in ROLES if (col := getattr(self, role)) is not None}


def detect(df: pd.DataFrame) -> dict[str, str | None]:
    """Guess a column for each role from the column names and types. Never raises."""
    guesses: dict[str, str | None] = {}
    used: set[str] = set()
    for role in ROLES:
        guesses[role] = None
        for hint in HINTS[role]:
            if hint in df.columns and hint not in used and _type_fits(df[hint], role):
                guesses[role] = hint
                used.add(hint)
                break
    return guesses


def _type_fits(series: pd.Series, role: str) -> bool:
    if role == "date":
        return pd.api.types.is_datetime64_any_dtype(series)
    if role in ("amount", "quantity"):
        return pd.api.types.is_numeric_dtype(series)
    return True


def build_mapping(df: pd.DataFrame, chosen: dict[str, str | None]) -> Mapping:
    """Validate a role -> column choice and return a Mapping."""
    for role in REQUIRED:
        if not chosen.get(role):
            raise MappingError(f"Choose a column for '{role}'.")
    columns = [c for c in chosen.values() if c]
    duplicates = sorted({c for c in columns if columns.count(c) > 1})
    if duplicates:
        raise MappingError(f"Column(s) used for more than one role: {', '.join(duplicates)}.")
    for role, col in chosen.items():
        if col is None:
            continue
        if col not in df.columns:
            raise MappingError(f"Column '{col}' does not exist.")
        if not _type_fits(df[col], role):
            kind = "dates" if role == "date" else "numbers"
            raise MappingError(f"Column '{col}' (for '{role}') does not contain {kind}.")
    return Mapping(**{role: chosen.get(role) for role in ROLES})  # type: ignore[arg-type]


def apply(df: pd.DataFrame, mapping: Mapping) -> pd.DataFrame:
    """Keep the mapped columns, renamed to the standard role names."""
    columns = mapping.as_dict()
    return df[list(columns.values())].rename(columns={v: k for k, v in columns.items()})
