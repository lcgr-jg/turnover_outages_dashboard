r"""
daily_report.py — Generate HTML report and email to team.
Run via Task Scheduler: .venv\Scripts\python.exe dash_irr_database\daily_report.py

Schedule: Mon-Fri at 9:00 AM (before team arrives, after IIR overnight update)
"""

import base64
import json
import logging
import os
import re
import sys
import sqlite3
from datetime import datetime

import pandas as pd

# ── Add project root to path ──
sys.path.insert(0, os.path.dirname(__file__))

from config import DEFAULT_UNIT_TYPES, TABLE_STYLES, SUMMARY_FMT
from export import style_actions, style_matrix
from data import load_and_prepare
from classification import classify_events, enrich_events
from charts import build_seasonal_chart, build_daily_ops_chart, monthly_capacity
from core import filter_event_type
from tables import build_actions_table, build_change_summary, build_forward_matrix

# ── Import the firm's email sender ──
# Adjust path if sender.py lives elsewhere
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sender import send_email


# ═══════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════

RECIPIENTS = (
    'luiscarlos.gaitan@jainglobal.com, yves.Mindren@jainglobal.com, abderrahmane.mahoudi@jainglobal.com'
)  # comma-separated string (do not use # inside quotes — it starts a comment)
RECIPIENTS_CC = None  # e.g. 'teammate1@jainglobal.com,teammate2@jainglobal.com'
FROM_ADDRESS = 'oil.research.desk@jainglobal.com'

UNIT_TYPES = DEFAULT_UNIT_TYPES
SAVE_COPY = True
SAVE_DIR = os.path.join(os.path.dirname(__file__), 'scratch')

REPORT_GEO_PATH = os.path.join(os.path.dirname(__file__), 'reference', 'report_geo.csv')


# ═══════════════════════════════════════════════════════════════════════
# SNAPSHOT HELPERS  — SQLite primary, JSON fallback
#
# DB:  data/snapshots.db  (project root, outside scratch/ so it persists)
# JSON fallback: scratch/snapshots/  (written only when SQLite fails)
#
# Records are never deleted — every run is stored indefinitely so any
# historical date can be replayed on demand via load_snapshot_for_date().
# ═══════════════════════════════════════════════════════════════════════

SNAPSHOT_DB  = os.path.join(os.path.dirname(__file__), 'data', 'snapshots.db')
SNAPSHOT_DIR = os.path.join(SAVE_DIR, 'snapshots')   # JSON fallback directory

_CREATE_DAILY = """
CREATE TABLE IF NOT EXISTS daily_capacity (
    scope       TEXT    NOT NULL,
    run_date    TEXT    NOT NULL,
    metric_date TEXT    NOT NULL,
    planned     REAL    NOT NULL DEFAULT 0,
    unplanned   REAL    NOT NULL DEFAULT 0,
    total       REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, run_date, metric_date)
)"""

_CREATE_SEASONAL = """
CREATE TABLE IF NOT EXISTS seasonal_capacity (
    scope      TEXT    NOT NULL,
    run_date   TEXT    NOT NULL,
    month_idx  INTEGER NOT NULL,
    planned    REAL    NOT NULL DEFAULT 0,
    unplanned  REAL    NOT NULL DEFAULT 0,
    combined   REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, run_date, month_idx)
)"""


def _get_db_conn() -> sqlite3.Connection:
    """Open data/snapshots.db, create tables if needed, return connection."""
    os.makedirs(os.path.dirname(SNAPSHOT_DB), exist_ok=True)
    conn = sqlite3.connect(SNAPSHOT_DB)
    conn.execute(_CREATE_DAILY)
    conn.execute(_CREATE_SEASONAL)
    conn.commit()
    return conn


def _json_fallback_path(scope_slug: str, run_date) -> str:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    fname = f"snapshot_{pd.Timestamp(run_date).strftime('%Y%m%d')}_{scope_slug}.json"
    return os.path.join(SNAPSHOT_DIR, fname)


def _write_json_fallback(scope_slug: str, run_date, daily_data: dict,
                         seasonal_data: dict) -> None:
    """Write JSON backup when SQLite is unavailable."""
    payload = {
        "run_date":          pd.Timestamp(run_date).strftime('%Y-%m-%d'),
        "daily_dates":       daily_data.get("dates", []),
        "daily_planned":     daily_data.get("planned", []),
        "daily_unplanned":   daily_data.get("unplanned", []),
        "daily_total":       daily_data.get("total", []),
        "seasonal_planned":  seasonal_data.get("planned", []),
        "seasonal_unplanned": seasonal_data.get("unplanned", []),
        "seasonal_combined": seasonal_data.get("combined", []),
    }
    with open(_json_fallback_path(scope_slug, run_date), 'w', encoding='utf-8') as fh:
        json.dump(payload, fh)


def save_snapshot(scope_slug: str, run_date, daily_data: dict,
                  seasonal_data: dict) -> None:
    """
    Persist capacity series for this run (SQLite primary, JSON fallback).

    daily_data   — {"dates": [...ISO str], "planned": [...], "unplanned": [...], "total": [...]}
    seasonal_data — {"planned": [12 floats], "unplanned": [12 floats], "combined": [12 floats]}

    Uses INSERT OR REPLACE so re-running for the same date is safe.
    Records are never deleted — use load_snapshot_for_date() to replay any date.
    """
    run_date_str = pd.Timestamp(run_date).strftime('%Y-%m-%d')
    dates     = daily_data.get("dates", [])
    planned   = daily_data.get("planned", [])
    unplanned = daily_data.get("unplanned", [])
    total     = daily_data.get("total", [])
    s_planned   = seasonal_data.get("planned", [])
    s_unplanned = seasonal_data.get("unplanned", [])
    s_combined  = seasonal_data.get("combined", [])

    try:
        conn = _get_db_conn()
        conn.executemany(
            "INSERT OR REPLACE INTO daily_capacity "
            "(scope, run_date, metric_date, planned, unplanned, total) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(scope_slug, run_date_str, d, p, u, t)
             for d, p, u, t in zip(dates, planned, unplanned, total)],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO seasonal_capacity "
            "(scope, run_date, month_idx, planned, unplanned, combined) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(scope_slug, run_date_str, i, float(s_planned[i]),
              float(s_unplanned[i]), float(s_combined[i]))
             for i in range(min(12, len(s_planned)))],
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"  [snapshot] SQLite write failed ({exc}); writing JSON fallback.", flush=True)
        try:
            _write_json_fallback(scope_slug, run_date, daily_data, seasonal_data)
        except Exception as exc2:
            print(f"  [snapshot] JSON fallback also failed: {exc2}", flush=True)


def _rows_to_snapshot_dict(daily_rows, seasonal_rows) -> dict:
    """Convert raw DB rows into the dict expected by chart builders."""
    daily_rows = sorted(daily_rows, key=lambda r: r[0])
    return {
        "daily_dates":       [r[0] for r in daily_rows],
        "daily_planned":     [r[1] for r in daily_rows],
        "daily_unplanned":   [r[2] for r in daily_rows],
        "daily_total":       [r[3] for r in daily_rows],
        "seasonal_planned":  [r[1] for r in sorted(seasonal_rows, key=lambda r: r[0])],
        "seasonal_unplanned":[r[2] for r in sorted(seasonal_rows, key=lambda r: r[0])],
        "seasonal_combined": [r[3] for r in sorted(seasonal_rows, key=lambda r: r[0])],
    }


def load_last_week_snapshot(scope_slug: str, today) -> dict | None:
    """
    Return the most recent snapshot for scope that is 5–10 calendar days old.
    Queries SQLite first; falls back to JSON files if no DB record found.
    """
    today_str = pd.Timestamp(today).strftime('%Y-%m-%d')

    # ── SQLite primary ──
    try:
        conn = _get_db_conn()

        # Find the best run_date (closest to 5 days ago within the 5-10 day window)
        run_row = conn.execute(
            "SELECT run_date FROM daily_capacity "
            "WHERE scope = ? "
            "  AND run_date BETWEEN date(?, '-10 days') AND date(?, '-5 days') "
            "ORDER BY run_date DESC LIMIT 1",
            (scope_slug, today_str, today_str),
        ).fetchone()

        if run_row:
            best_run = run_row[0]
            daily_rows = conn.execute(
                "SELECT metric_date, planned, unplanned, total "
                "FROM daily_capacity WHERE scope = ? AND run_date = ? "
                "ORDER BY metric_date",
                (scope_slug, best_run),
            ).fetchall()
            seasonal_rows = conn.execute(
                "SELECT month_idx, planned, unplanned, combined "
                "FROM seasonal_capacity WHERE scope = ? AND run_date = ? "
                "ORDER BY month_idx",
                (scope_slug, best_run),
            ).fetchall()
            conn.close()
            if daily_rows:
                snap = _rows_to_snapshot_dict(daily_rows, seasonal_rows)
                snap["run_date"] = best_run
                return snap

        conn.close()
    except Exception as exc:
        print(f"  [snapshot] SQLite read failed ({exc}); trying JSON fallback.", flush=True)

    # ── JSON fallback ──
    if not os.path.isdir(SNAPSHOT_DIR):
        return None
    today_ts = pd.Timestamp(today).normalize()
    best_fname, best_age = None, None
    for fname in os.listdir(SNAPSHOT_DIR):
        if not fname.endswith('.json'):
            continue
        parts = fname[len('snapshot_'):].split('_', 1)
        if len(parts) < 2:
            continue
        date_str, rest = parts
        if rest != f"{scope_slug}.json":
            continue
        try:
            age = (today_ts - pd.Timestamp(date_str)).days
        except Exception:
            continue
        if 5 <= age <= 10 and (best_age is None or age < best_age):
            best_age, best_fname = age, fname
    if best_fname is None:
        return None
    try:
        with open(os.path.join(SNAPSHOT_DIR, best_fname), 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None


def load_snapshot_for_date(scope_slug: str, target_date) -> dict | None:
    """
    Return the snapshot whose run_date is closest to target_date.
    Useful for generating on-demand historical reports for any past date.
    Returns None if no records exist for this scope.
    """
    target_str = pd.Timestamp(target_date).strftime('%Y-%m-%d')
    try:
        conn = _get_db_conn()
        run_row = conn.execute(
            "SELECT run_date FROM daily_capacity "
            "WHERE scope = ? "
            "ORDER BY abs(julianday(run_date) - julianday(?)) LIMIT 1",
            (scope_slug, target_str),
        ).fetchone()
        if not run_row:
            conn.close()
            return None
        best_run = run_row[0]
        daily_rows = conn.execute(
            "SELECT metric_date, planned, unplanned, total "
            "FROM daily_capacity WHERE scope = ? AND run_date = ? "
            "ORDER BY metric_date",
            (scope_slug, best_run),
        ).fetchall()
        seasonal_rows = conn.execute(
            "SELECT month_idx, planned, unplanned, combined "
            "FROM seasonal_capacity WHERE scope = ? AND run_date = ? "
            "ORDER BY month_idx",
            (scope_slug, best_run),
        ).fetchall()
        conn.close()
        if not daily_rows:
            return None
        snap = _rows_to_snapshot_dict(daily_rows, seasonal_rows)
        snap["run_date"] = best_run
        return snap
    except Exception as exc:
        print(f"  [snapshot] load_snapshot_for_date failed: {exc}", flush=True)
        return None


def _silence_noisy_loggers():
    """Keep chart/export and HTTP client libraries from flooding stdout during batch runs."""
    for name in (
        'plotly',
        'kaleido',
        'choreographer',
        'urllib3',
        'httpcore',
        'httpx',
        'snowflake',
        'botocore',
        'boto3',
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


# ═══════════════════════════════════════════════════════════════════════
# Report region bundles (report_geo.csv)
# ═══════════════════════════════════════════════════════════════════════

def _jg_cell_blank(val) -> bool:
    """True when CSV marks no jg_region (Global row uses None / empty / 'None')."""
    if pd.isna(val):
        return True
    s = str(val).strip()
    return s == '' or s.lower() == 'none'


def load_report_region_groups(csv_path: str) -> dict[str, list[str]]:
    """
    report_region -> distinct jg_region values to plot/send together.
    Omits the Global bundle (no per-region filter).
    """
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    if 'report_region' not in df.columns or 'jg_region' not in df.columns:
        raise ValueError(f'{csv_path} must have columns report_region, jg_region')
    out: dict[str, list[str]] = {}
    for report_region, grp in df.groupby('report_region', sort=False):
        name = str(report_region).strip()
        if name.lower() == 'global':
            continue
        vals = sorted({str(v).strip() for v in grp['jg_region'] if not _jg_cell_blank(v)})
        if vals:
            out[name] = vals
    return out


def report_dates():
    """Today's report anchor and comparison date (skip weekend gap)."""
    today = pd.Timestamp.today().normalize()
    prev = today - pd.Timedelta(days=1)
    if prev.weekday() >= 5:
        prev = prev - pd.Timedelta(days=(prev.weekday() - 4))
    return today, prev


def filter_unit_types(classified: pd.DataFrame, unit_types) -> pd.DataFrame:
    ut_col = next((c for c in ['UNIT_TYPE', 'UTYPE_DESC'] if c in classified.columns), None)
    if ut_col and unit_types:
        return classified[classified[ut_col].isin(unit_types)].copy()
    return classified.copy()


def fig_to_img_tag(fig, width=700, height=380):
    """Convert Plotly figure to an inline <img> tag for email."""
    img_bytes = fig.to_image(format='png', width=width, height=height, scale=2)
    b64 = base64.b64encode(img_bytes).decode('utf-8')
    return f'<img src="data:image/png;base64,{b64}" width="{width}" style="max-width:100%;">'


def generate_report_html(events_df: pd.DataFrame, today, prev, region_scope_label: str,
                         prev_week_snapshot: dict | None = None,
                         is_regional: bool = False):
    """
    Build HTML + subject from an already-filtered event frame (unit types and optional region).
    region_scope_label: e.g. 'GLOBAL' or 'Europe (NEW + MED)' for chart titles and subject.

    prev_week_snapshot: optional dict returned by load_last_week_snapshot; when supplied,
        a dashed grey "Last Week" line is overlaid on each chart for quick visual comparison.
    """
    cf = events_df
    current_year = today.year
    last_year = current_year - 1
    years = list(range(2021, last_year + 1))
    config = {
        'unit_types': UNIT_TYPES,
        'event_type': 'Planned',
        'region': None,
        'country': None,
        'hist_years': years,
        'exclude_years': [2020],
        'show_years': [current_year-1], # last year
        'chart_scope_label': region_scope_label,
    }

    # Extract last-week data from snapshot (gracefully handles None or missing keys)
    pw = prev_week_snapshot or {}
    pw_planned   = pw.get('seasonal_planned')
    pw_unplanned = pw.get('seasonal_unplanned')
    pw_combined  = pw.get('seasonal_combined')

    fig_p = build_seasonal_chart(cf, {**config, 'event_type': 'Planned'}, today,
                                 prev_week_monthly=pw_planned)
    fig_u = build_seasonal_chart(cf, {**config, 'event_type': 'Unplanned'}, today,
                                 prev_week_monthly=pw_unplanned)
    fig_c = build_seasonal_chart(cf, {**config, 'event_type': None}, today,
                                 prev_week_monthly=pw_combined)
    fig_d = build_daily_ops_chart(cf, today, config, prev_week=pw or None)

    chart1 = fig_to_img_tag(fig_p)
    chart2 = fig_to_img_tag(fig_u)
    chart_comb = fig_to_img_tag(fig_c, width=900)
    chart_daily = fig_to_img_tag(fig_d, width=900)

    summary_df = build_change_summary(cf)
    actions_df = build_actions_table(cf)
    matrix_config = {'unit_types': UNIT_TYPES, 'forward_months': 12}
    if is_regional:
        matrix_group_col = 'Country'
        matrix_title = 'Forward Impact by Country (kbd)'
    else:
        matrix_group_col = 'Region'
        matrix_title = 'Forward Impact by Region (kbd)'
    sub_col = 'Refinery' if is_regional else None
    regional_df, ml = build_forward_matrix(cf, matrix_config, today,
                                            group_col=matrix_group_col,
                                            subgroup_col=sub_col,
                                            aggregate_subgroup=is_regional)

    summary_html = (
        summary_df.style.format(SUMMARY_FMT, na_rep='—')
        .set_table_styles(TABLE_STYLES)
        .hide(axis='index').to_html()
    ) if not summary_df.empty else ''

    actions_html = (
        style_actions(actions_df, config['unit_types'], today).to_html(
            index=False, na_rep='—', classes='styled-table')
    ) if not actions_df.empty else ''

    regional_html = style_matrix(regional_df, ml, matrix_title).to_html(index=False, na_rep='—', border=0) if not regional_df.empty else ''

    collapse_js = """
    <script>
    document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.region-header').forEach(function(header) {
        header.style.cursor = 'pointer';
        header.addEventListener('click', function() {
        var region = this.dataset.region;
        var rows = document.querySelectorAll('.region-detail[data-region="' + region + '"]');
        var visible = rows[0] && rows[0].style.display !== 'none';
        rows.forEach(function(r) { r.style.display = visible ? 'none' : ''; });
        var arrow = this.querySelector('.arrow');
        if (arrow) arrow.textContent = visible ? '▸' : '▾';
        });
        var region = header.dataset.region;
        document.querySelectorAll('.region-detail[data-region="' + region + '"]').forEach(function(r) {
        r.style.display = 'none';
        });
    });
    });
    </script>
    """

    n_actions = len(actions_df) if not actions_df.empty else 0
    n_new = len(cf[cf['Action'] == 'New']) if 'Action' in cf.columns else 0
    n_updated = len(cf[cf['Action'] == 'Updated']) if 'Action' in cf.columns else 0

    dt = today.strftime('%d %B %Y')
    ut_label = ' & '.join(UNIT_TYPES)
    scope_html = '' if region_scope_label.upper() == 'GLOBAL' else f' &nbsp;|&nbsp; {region_scope_label}'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>IIR Maintenance Report — {dt}</title>
<style>
body {{ font-family: Calibri, Arial, sans-serif; margin: 20px; background: #ffffff; color: #1a1a2e; font-size: 13px; }}
h1 {{ font-size: 16px; margin-bottom: 2px; color: #1a1a2e; }}
h2 {{ font-size: 12px; color: #666; font-weight: normal; margin-top: 0; }}
h3 {{ font-size: 13px; color: #1a1a2e; margin: 20px 0 6px 0; }}
.charts {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }}
.chart {{ flex: 1; min-width: 380px; background: white; padding: 6px; }}
.tw {{ background: white; margin-bottom: 16px; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
th {{ background: #1a1a2e; color: white; padding: 6px 8px; text-align: center; font-size: 11px; white-space: nowrap; }}
td {{ padding: 5px 8px; border-bottom: 1px solid #ddd; white-space: nowrap; }}
.legend {{ font-size: 11px; margin-bottom: 8px; }}
.legend span {{ display: inline-block; width: 12px; height: 12px; border-radius: 2px; margin-right: 3px; vertical-align: middle; }}
</style>
</head>
<body>
<h1>DAILY IIR REPORTED MAINTENANCE ACTIVITY</h1>
<h2>Jain Global Research Desk &nbsp;|&nbsp; {dt} &nbsp;|&nbsp; {ut_label}{scope_html}</h2>

<div class="tw">{chart_comb}</div>

<div class="charts">
  <div class="chart">{chart1}</div>
  <div class="chart">{chart2}</div>
</div>

<div class="tw">
  <h3>Daily Offline Capacity</h3>
  {chart_daily}
</div>

<div class="tw">
  <h3>Change Summary — {today.strftime('%d %b')} vs {prev.strftime('%d %b')}</h3>
  {summary_html}
</div>

<div class="tw">
  <h3>Recent Event Actions</h3>
  <div class="legend">
    <span style="background:#d4edda"></span>New &nbsp;
    <span style="background:#fff3cd"></span>Updated &nbsp;
    <span style="background:#fce4ec"></span>Cancelled &nbsp;
    <span style="background:#d1ecf1"></span>Completed &nbsp;
    <span style="background:#e8eaf6"></span>Past
  </div>
  {actions_html}
</div>

<div class="tw">
  <h3>{matrix_title}</h3>
  {regional_html}
</div>
    {collapse_js}

</body></html>"""

    subject = (
        f"TAR Report ")
        
    if region_scope_label.upper() != 'GLOBAL':
        subject += f"| {region_scope_label} "
        
    subject += f"| — {today.strftime('%d %b %Y')} — {n_actions} actions ({n_new} new, {n_updated} updated)"
    
    return html, subject


def generate_report():
    """Load data, build global report only (backward-compatible wrapper)."""
    today, prev = report_dates()
    print(f"Generating report: {today.date()} vs {prev.date()}")
    df = load_and_prepare()
    classified = classify_events(df, today, prev)
    classified = enrich_events(classified, today)
    cf = filter_unit_types(classified, UNIT_TYPES)
    print(f"Events: {len(cf)}")
    return generate_report_html(cf, today, prev, 'GLOBAL')


def _scope_file_slug(scope_label: str) -> str:
    s = re.sub(r'[^\w\-]+', '_', scope_label, flags=re.UNICODE).strip('_')
    return s[:80] if s else 'report'


def _build_snapshot_data(events_df: pd.DataFrame, today, unit_types,
                         lookback_days=90, forward_days=120) -> tuple[dict, dict]:
    """
    Compute the raw capacity series needed for the snapshot JSON without
    constructing a full Plotly figure.

    Returns (daily_data, seasonal_data) suitable for save_snapshot().
    """
    cap_col = 'CAP_OFFLINE_STD' if 'CAP_OFFLINE_STD' in events_df.columns else 'CAP_OFFLINE'
    ev = events_df.copy()
    if 'E_STATUS_RESEARCH' in ev.columns:
        ev = ev[ev['E_STATUS_RESEARCH'] != 'Cancelled']
    if unit_types:
        uc = next((c for c in ['UNIT_TYPE', 'UTYPE_DESC'] if c in ev.columns), None)
        if uc:
            ev = ev[ev[uc].isin(unit_types)]

    # ── Daily series ──
    date_start = today - pd.Timedelta(days=lookback_days)
    date_end   = today + pd.Timedelta(days=forward_days)
    date_range = pd.date_range(date_start, date_end, freq='D')

    ev2 = ev[ev['START_DATE'].notna()].copy()
    ev2['_end'] = ev2['END_DATE'].fillna(date_end)
    ev2 = ev2[(ev2['START_DATE'] <= date_end) & (ev2['_end'] >= date_start)]

    planned_ev   = filter_event_type(ev2, 'Planned')
    unplanned_ev = filter_event_type(ev2, 'Unplanned')

    def _daily_sum(subset, dates):
        vals = []
        for d in dates:
            active = subset[(subset['START_DATE'] <= d) & (subset['_end'] >= d)]
            vals.append(float(pd.to_numeric(active[cap_col], errors='coerce').fillna(0).sum() / 1_000))
        return vals

    planned_daily   = _daily_sum(planned_ev, date_range)
    unplanned_daily = _daily_sum(unplanned_ev, date_range)
    total_daily     = [p + u for p, u in zip(planned_daily, unplanned_daily)]

    daily_data = {
        'dates':     [d.strftime('%Y-%m-%d') for d in date_range],
        'planned':   planned_daily,
        'unplanned': unplanned_daily,
        'total':     total_daily,
    }

    # ── Seasonal (monthly) series for current year ──
    cur_year = today.year
    kw = dict(unit_types=unit_types, countries=None, world_regions=None)
    seasonal_data = {
        'planned':   monthly_capacity(events_df, cur_year, event_type='Planned',   **kw).tolist(),
        'unplanned': monthly_capacity(events_df, cur_year, event_type='Unplanned', **kw).tolist(),
        'combined':  monthly_capacity(events_df, cur_year, event_type=None,        **kw).tolist(),
    }

    return daily_data, seasonal_data


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    _silence_noisy_loggers()

    today, prev = report_dates()
    print(f"Generating report: {today.date()} vs {prev.date()}")

    df = load_and_prepare()
    classified = classify_events(df, today, prev)
    classified = enrich_events(classified, today)

    if 'jg_region' not in classified.columns:
        print('Warning: jg_region missing after load — regional bundles skipped; geography join may have failed.')
        cf = filter_unit_types(classified, UNIT_TYPES)
        bundles = {}
    else:
        cf = filter_unit_types(classified, UNIT_TYPES)
        try:
            bundles = load_report_region_groups(REPORT_GEO_PATH)
        except FileNotFoundError:
            print(f'Warning: {REPORT_GEO_PATH} not found — regional emails skipped.')
            bundles = {}

    print(f"Events (global scope): {len(cf)}")

    sends = [('GLOBAL', cf, 'GLOBAL')]

    for report_region, jg_list in bundles.items():
        sub = cf[cf['jg_region'].isin(jg_list)]
        if sub.empty:
            print(f"  Skip regional '{report_region}': no events for jg_regions {jg_list}")
            continue
        sends.append((report_region, sub, report_region))

    total_sends = len(sends)
    run_rows = []

    for i, (tag, events_df, scope_label) in enumerate(sends, start=1):
        print(f"[{i}/{total_sends}] {tag} — {len(events_df)} events, building…", flush=True)
        slug = _scope_file_slug(tag)

        # Load last-week snapshot (5–10 days old) for the "Last Week" reference line
        prev_week_snap = load_last_week_snapshot(slug, today)
        if prev_week_snap:
            snap_date = prev_week_snap.get('run_date', '?')
            print(f"  Last-week snapshot found: {snap_date}", flush=True)
        else:
            print(f"  No last-week snapshot for '{tag}' — chart will not show reference line.", flush=True)

        html_body, subject = generate_report_html(events_df, today, prev, scope_label,
                                                  prev_week_snapshot=prev_week_snap,
                                                  is_regional=(tag != 'GLOBAL'))

        # Save today's snapshot so next week's run can draw the "Last Week" line
        daily_data, seasonal_data = _build_snapshot_data(events_df, today, UNIT_TYPES)
        save_snapshot(slug, today, daily_data, seasonal_data)

        saved_path = ''
        if SAVE_COPY:
            os.makedirs(SAVE_DIR, exist_ok=True)
            fname = f"refinery_report_{datetime.now().strftime('%Y%m%d')}_{slug}.html"
            saved_path = os.path.join(SAVE_DIR, fname)
            with open(saved_path, 'w', encoding='utf-8') as f:
                f.write(html_body)

        ok, kafka_detail = send_email(
            recipients=RECIPIENTS,
            subject=subject,
            body=html_body,
            from_address=FROM_ADDRESS,
            recipients_cc=RECIPIENTS_CC,
        )
        subj_short = subject if len(subject) <= 100 else subject[:97] + '…'
        status = 'OK' if ok else 'FAIL'
        print(
            f"[{i}/{total_sends}] {status}  kafka {kafka_detail}"
            + (f"  saved={os.path.basename(saved_path)}" if saved_path else ''),
            flush=True,
        )
        print(f"         subject: {subj_short}", flush=True)
        run_rows.append((tag, ok, kafka_detail, subj_short))

    print()
    print('--- Email run summary (Kafka broker accepted message) ---')
    ok_count = sum(1 for _, o, _, _ in run_rows if o)
    print(f"  Handed off: {ok_count}/{total_sends} OK")
    if ok_count < total_sends:
        print('  If OK count matches run but inbox is short, check the fo_tools_alerts consumer / mail gateway.')
    for tag, ok, detail, subj_short in run_rows:
        line = f"  [{'OK' if ok else 'FAIL'}] {tag}: {detail}"
        if not ok:
            line += f" | {subj_short}"
        print(line, flush=True)

    print('Done.')
