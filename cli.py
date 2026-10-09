"""
Command line for the invoice checker. Everything here is also available in the web app (app.py).

    python cli.py import invoices.xlsx [--dayfirst | --monthfirst]   load company records (CSV/Excel)
    python cli.py audit                               score all records
    python cli.py check new_invoices.xlsx [--out results.csv]   check invoices before paying them
    python cli.py backup                              copy the database to data/backups/ (keeps the last 30)
    python cli.py demo                                load ~530 real receipts as sample records (downloads 670 MB once)
    python cli.py train-tamper                        retrain the image-tamper model (downloads 670 MB once)
"""
import argparse
import getpass
import os

from engine import checks, db, importer, tamper


def _load(path: str, dayfirst: bool):
    rows, problems = importer.normalize(importer.read_table(path), dayfirst=dayfirst)
    for p in problems:
        print("  !", p)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("import", "check"):
        p = sub.add_parser(name)
        p.add_argument("file", help="CSV or Excel file")
        order = p.add_mutually_exclusive_group()  # default: detected from the file
        order.add_argument("--dayfirst", action="store_const", const=True, dest="dayfirst", help="dates are dd/mm/yyyy")
        order.add_argument("--monthfirst", action="store_const", const=False, dest="dayfirst", help="dates are mm/dd/yyyy")
    sub.choices["check"].add_argument("--out", default="check_results.csv", help="where to write results")
    sub.add_parser("audit")
    sub.add_parser("backup")
    sub.add_parser("demo")
    sub.add_parser("train-tamper")
    args = ap.parse_args()

    if args.command == "train-tamper":
        for k, v in tamper.train().items():
            print(f"  {k:<16} {v:.3f}" if isinstance(v, float) else f"  {k:<16} {v}")
        return

    conn = db.connect()
    if args.command == "import":
        rows = _load(args.file, args.dayfirst)
        n = importer.import_records(conn, rows, source=os.path.basename(args.file))
        print(f"Imported {n:,} invoices ({db.count_invoices(conn):,} records in total). Next: python cli.py audit"
              + (f"\n  Skipped {len(rows) - n:,} already in the records." if len(rows) > n else ""))
    elif args.command == "demo":
        n = importer.import_records(conn, tamper.demo_history(), source="demo")
        print(f"Imported {n:,} demo receipts. Next: python cli.py audit")
    elif args.command == "backup":
        print("Backed up to", db.backup(conn))
    elif args.command == "audit":
        summary = checks.audit(conn)
        if not summary["records"]:
            print("No records yet. Import some first: python cli.py import your_invoices.xlsx")
        for k, v in summary.items():
            print(f"  {k:<26} {v}")
    elif args.command == "check":
        res, _ = checks.check(conn, _load(args.file, args.dayfirst))
        checks.log_checks(conn, res, source="cli", file_name=os.path.basename(args.file), checked_by=getpass.getuser())
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            f.write(checks.to_csv(res.drop(columns=["id"])))
        print(res["verdict"].value_counts().to_string(), f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
