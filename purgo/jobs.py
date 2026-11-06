"""Periodic jobs. Run from cron, e.g. daily:

    python -m purgo.jobs            # deadlines + due re-scans
    python -m purgo.jobs --sync     # also re-sync brokers.yaml
"""

from __future__ import annotations

import argparse

from . import db
from .brokers import sync_brokers
from .config import settings
from .workflow import process_deadlines, rescan_due


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Purgo periodic jobs")
    parser.add_argument("--sync", action="store_true", help="sync broker registry first")
    parser.add_argument("--no-scan", action="store_true", help="skip re-scans")
    args = parser.parse_args(argv)

    db.init_db()
    with db.SessionLocal() as session:
        if args.sync:
            print(f"brokers synced: {sync_brokers(session, settings.brokers_file)}")
        print(f"deadlines: {process_deadlines(session)}")
        if not args.no_scan:
            print(f"profiles re-scanned: {rescan_due(session)}")


if __name__ == "__main__":
    main()
