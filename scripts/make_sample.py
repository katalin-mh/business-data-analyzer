"""Generate the SYNTHETIC sample dataset in samples/.

The data is random (fixed seed, so the output is identical on every run) and does
not describe any real business. It deliberately contains the kind of mess real
exports have, so the cleaning step has something to do:

- duplicated rows, blank rows and stray whitespace
- amounts written as "1,234.50", "€ 99.00" or "1.234,50"
- dates in two formats, plus a few unparseable ones
- missing customer and region values

Usage: python scripts/make_sample.py
"""

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SEED = 2026
OUT = Path(__file__).resolve().parent.parent / "samples"

PRODUCTS = {
    "Desk lamp": 24.90,
    "Office chair": 149.00,
    "Notebook A5": 3.50,
    "Monitor stand": 39.90,
    "USB-C hub": 29.00,
    "Standing desk": 389.00,
    "Cable organiser": 9.90,
    "Webcam": 59.00,
}
REGIONS = ["North", "South", "East", "West"]


def _format_amount(value: float, rng: random.Random) -> str:
    style = rng.random()
    if style < 0.70:
        return f"{value:.2f}"
    if style < 0.85:
        return f"{value:,.2f}"  # 1,234.50
    if style < 0.95:
        return f"€ {value:.2f}"
    # European style: 1.234,50
    return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _format_date(day: date, rng: random.Random) -> str:
    if rng.random() < 0.01:
        return "unknown"
    return day.isoformat() if rng.random() < 0.8 else day.strftime("%d/%m/%Y")


def build(rows: int = 1200) -> pd.DataFrame:
    rng = random.Random(SEED)  # noqa: S311  (sample data, not security-related)
    customers = [f"CUST-{i:04d}" for i in range(1, 181)]
    start = date(2025, 1, 1)
    records = []
    for i in range(1, rows + 1):
        product = rng.choice(list(PRODUCTS))
        quantity = rng.choices([1, 2, 3, 4, 5], weights=[60, 20, 10, 6, 4])[0]
        amount = round(PRODUCTS[product] * quantity * rng.uniform(0.9, 1.0), 2)
        day = start + timedelta(days=rng.randrange(0, 365 + 181))
        records.append(
            {
                "Order ID": f"SO-{i:05d}",
                "Order Date": _format_date(day, rng),
                " Customer ": rng.choice(customers) if rng.random() > 0.03 else "",
                "Product": f"  {product} " if rng.random() < 0.05 else product,
                "Quantity": quantity,
                "Amount": _format_amount(amount, rng),
                "Region": rng.choice(REGIONS) if rng.random() > 0.05 else None,
            }
        )
    df = pd.DataFrame(records)
    duplicates = df.sample(n=15, random_state=SEED)
    blank = pd.DataFrame([dict.fromkeys(df.columns)] * 3)
    return pd.concat([df, duplicates, blank], ignore_index=True)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    df = build()
    # Fixed line endings so the file is byte-identical on every operating system.
    df.to_csv(OUT / "sales_synthetic.csv", index=False, lineterminator="\n")
    df.head(300).to_excel(OUT / "sales_synthetic.xlsx", index=False, sheet_name="Sales")
    print(f"Wrote {len(df)} rows to {OUT}")


if __name__ == "__main__":
    main()
