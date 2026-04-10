r"""
daily_report.py — Generate HTML report and email to team.
Run via Task Scheduler: .venv\Scripts\python.exe dash_irr_database\daily_report.py

Schedule: Mon-Fri at 9:15 AM (before team arrives, after IIR overnight update)
"""

import base64
import logging
import os
import re
import sys
from datetime import datetime

import pandas as pd

# ── Add project root to path ──
sys.path.insert(0, os.path.dirname(__file__))

from config import DEFAULT_UNIT_TYPES, TABLE_STYLES, SUMMARY_FMT
from export import style_actions
from data import load_and_prepare
from classification import classify_events, enrich_events
from charts import build_seasonal_chart, build_daily_ops_chart
from tables import build_actions_table, build_change_summary, build_forward_matrix

# ── Import the firm's email sender ──
# Adjust path if sender.py lives elsewhere
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sender import send_email


# ═══════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════

RECIPIENTS = (
    'luiscarlos.gaitan@jainglobal.com'#, Yves.Mindren@jainglobal.com, '
    #'abderrahmane.mahoudi@jainglobal.com'
)  # comma-separated string (do not use # inside quotes — it starts a comment)
RECIPIENTS_CC = None  # e.g. 'teammate1@jainglobal.com,teammate2@jainglobal.com'
FROM_ADDRESS = 'jg_research_desk@jainglobal.com'

UNIT_TYPES = DEFAULT_UNIT_TYPES
SAVE_COPY = True
SAVE_DIR = os.path.join(os.path.dirname(__file__), 'scratch')

REPORT_GEO_PATH = os.path.join(os.path.dirname(__file__), 'reference', 'report_geo.csv')


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


def generate_report_html(events_df: pd.DataFrame, today, prev, region_scope_label: str):
    """
    Build HTML + subject from an already-filtered event frame (unit types and optional region).
    region_scope_label: e.g. 'GLOBAL' or 'Europe (NEW + MED)' for chart titles and subject.
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

    fig_p = build_seasonal_chart(cf, {**config, 'event_type': 'Planned'}, today)
    fig_u = build_seasonal_chart(cf, {**config, 'event_type': 'Unplanned'}, today)
    fig_c = build_seasonal_chart(cf, {**config, 'event_type': None}, today)
    fig_d = build_daily_ops_chart(cf, today, config)

    chart1 = fig_to_img_tag(fig_p)
    chart2 = fig_to_img_tag(fig_u)
    chart_comb = fig_to_img_tag(fig_c, width=900)
    chart_daily = fig_to_img_tag(fig_d, width=900)

    summary_df = build_change_summary(cf)
    actions_df = build_actions_table(cf)
    matrix_config = {'unit_types': UNIT_TYPES, 'forward_months': 12}
    regional_df, ml = build_forward_matrix(cf, matrix_config, today, group_col='Region')

    summary_html = (
        summary_df.style.format(SUMMARY_FMT, na_rep='—')
        .set_table_styles(TABLE_STYLES)
        .hide(axis='index').to_html()
    ) if not summary_df.empty else ''

    actions_html = (
        style_actions(actions_df, config['unit_types'], today).to_html(
            index=False, na_rep='—', classes='styled-table')
    ) if not actions_df.empty else ''

    regional_html = regional_df.to_html(index=False, na_rep='—', border=0) if not regional_df.empty else ''

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

<div class="charts">
  <div class="chart">{chart1}</div>
  <div class="chart">{chart2}</div>
</div>

<div class="tw">{chart_comb}</div>

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
  <h3>Forward Impact by Region (kbd)</h3>
  {regional_html}
</div>
    {collapse_js}

</body></html>"""

    subject = (
        f"TAR Report — {today.strftime('%d %b %Y')}")
        
    if region_scope_label.upper() != 'GLOBAL':
        subject += f" | {region_scope_label}"
        
    subject += f"| {n_actions} actions ({n_new} new, {n_updated} updated)"
    
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
        html_body, subject = generate_report_html(events_df, today, prev, scope_label)

        saved_path = ''
        if SAVE_COPY:
            os.makedirs(SAVE_DIR, exist_ok=True)
            slug = _scope_file_slug(tag)
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
