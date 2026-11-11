"""Command line: analyzer report data.csv --html report.html --xlsx report.xlsx"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from analyzer import export, report, schema
from analyzer.cleaning import CleaningOptions
from analyzer.loading import InputError, load
from analyzer.pipeline import analyze, prepare


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analyzer", description="Analyse a sales CSV or Excel file."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report", help="Clean the data and write reports")
    rep.add_argument("file", type=Path, help="CSV (.csv) or Excel (.xlsx) file")
    rep.add_argument("--html", type=Path, help="Write an HTML report to this path")
    rep.add_argument("--xlsx", type=Path, help="Write an Excel workbook to this path")
    rep.add_argument("--csv", type=Path, help="Write the cleaned data as CSV to this path")
    rep.add_argument("--keep-duplicates", action="store_true", help="Don't remove duplicate rows")
    rep.add_argument("--monthfirst", action="store_true", help="Read 03/04/2025 as March 4")
    for role in schema.ROLES:
        rep.add_argument(f"--{role.replace('_', '-')}-column", dest=role, help=f"Column for {role}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        raw = load(args.file)
        options = CleaningOptions(
            drop_duplicates=not args.keep_duplicates, dayfirst=not args.monthfirst
        )
        cleaned, cleaning_report, guess = prepare(raw, options)
        overrides = {role: getattr(args, role) for role in schema.ROLES if getattr(args, role)}
        # A column chosen explicitly is taken away from any role it was guessed for.
        chosen = {
            role: col if col not in overrides.values() else None for role, col in guess.items()
        } | overrides
        analysis = analyze(args.file.name, cleaned, cleaning_report, chosen)
    except (InputError, schema.MappingError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    k = analysis.kpis
    print(f"Rows: {cleaning_report.rows_in} read, {cleaning_report.rows_out} after cleaning")
    print(f"Columns used: {analysis.mapping.as_dict()}")
    print(f"Revenue: {k.revenue:,.2f}   Orders: {k.orders:,}", end="")
    if k.average_order_value is not None:
        print(f"   Average order value: {k.average_order_value:,.2f}", end="")
    print()
    for line in cleaning_report.summary() + k.notes:
        print(f"  - {line}")

    outputs: list[tuple[Path | None, Callable[[], bytes]]] = [
        (args.html, lambda: report.render(analysis).encode("utf-8")),
        (args.xlsx, lambda: export.workbook(analysis)),
        (args.csv, lambda: export.cleaned_csv(analysis)),
    ]
    for path, make in outputs:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(make())
            print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
