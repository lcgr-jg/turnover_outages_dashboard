r"""
seed_snapshot.py — One-time helper to bootstrap the "Last Week" snapshot.

Run this ONCE from the project root to create a backdated snapshot so the
very next daily_report.py run shows the "Last Week" reference line on the
charts.  After previewing, keep or delete this file as you see fit.

Usage:
    .venv\Scripts\python.exe dash_irr_database\seed_snapshot.py

The script loads today's live data, computes the capacity series, and saves
it as a snapshot dated 7 days ago for every scope that daily_report.py would
normally cover (GLOBAL + any regional bundles in report_geo.csv).
"""

import os
import sys

import pandas as pd

# ── Add project root to path ──
sys.path.insert(0, os.path.dirname(__file__))

from config import DEFAULT_UNIT_TYPES
from data import load_and_prepare
from classification import classify_events, enrich_events
from daily_report import (
    report_dates,
    filter_unit_types,
    load_report_region_groups,
    _scope_file_slug,
    _build_snapshot_data,
    save_snapshot,
    REPORT_GEO_PATH,
)

UNIT_TYPES = DEFAULT_UNIT_TYPES
SEED_OFFSET_DAYS = 7   # how many days to backdate the snapshot


def main():
    today, prev = report_dates()
    seed_date = today - pd.Timedelta(days=SEED_OFFSET_DAYS)

    print(f"Loading live data (as of {today.date()}) …")
    df = load_and_prepare()
    classified = classify_events(df, today, prev)
    classified = enrich_events(classified, today)

    if 'jg_region' not in classified.columns:
        print("Warning: jg_region column missing — only GLOBAL snapshot will be seeded.")
        bundles = {}
    else:
        try:
            bundles = load_report_region_groups(REPORT_GEO_PATH)
        except FileNotFoundError:
            print(f"Warning: {REPORT_GEO_PATH} not found — only GLOBAL snapshot will be seeded.")
            bundles = {}

    cf = filter_unit_types(classified, UNIT_TYPES)

    scopes = [('GLOBAL', cf)]
    for report_region, jg_list in bundles.items():
        sub = cf[cf['jg_region'].isin(jg_list)]
        if not sub.empty:
            scopes.append((report_region, sub))

    print(f"Seeding {len(scopes)} snapshot(s) backdated to {seed_date.date()} …\n")

    for tag, events_df in scopes:
        slug = _scope_file_slug(tag)
        daily_data, seasonal_data = _build_snapshot_data(events_df, today, UNIT_TYPES)
        save_snapshot(slug, seed_date, daily_data, seasonal_data)
        print(f"  [OK] {tag:40s}  slug={slug}")

    print(f"\nDone.  Run daily_report.py now to preview the 'Last Week' line.")
    print("The line will reflect today's data (unchanged from last week since this is a seed),")
    print("but it confirms the feature is wired up correctly.  Future runs will show real deltas.")


if __name__ == '__main__':
    main()
