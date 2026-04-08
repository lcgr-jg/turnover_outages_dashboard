"""
daily_report.py — Generate HTML report and email to team.
Run via Task Scheduler: .venv\Scripts\python.exe dash_irr_database\daily_report.py

Schedule: Mon-Fri at 9:15 AM (before team arrives, after IIR overnight update)
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
import pandas as pd

# ── Add project root to path ──
sys.path.insert(0, os.path.dirname(__file__))

from config import DEFAULT_UNIT_TYPES, TABLE_STYLES, SUMMARY_FMT
from export import style_actions, style_matrix, fmt_kbd
from data import load_and_prepare
from classification import classify_events, enrich_events
from charts import build_seasonal_chart, build_daily_ops_chart, build_combined_monthly_chart
from tables import build_actions_table, build_change_summary, build_forward_matrix
from plotly.io import to_html

# ── Import the firm's email sender ──
# Adjust path if sender.py lives elsewhere
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sender import send_email


# ═══════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════

RECIPIENTS = 'luiscarlos.gaitan@jainglobal.com, Yves.Mindren@jainglobal.com, abderrahmane.mahoudi@jainglobal.com'  # comma-separated string
RECIPIENTS_CC = None  # e.g. 'teammate1@jainglobal.com,teammate2@jainglobal.com'
FROM_ADDRESS = 'jg_research_desk@jainglobal.com'

UNIT_TYPES = DEFAULT_UNIT_TYPES
SAVE_COPY = True
SAVE_DIR = os.path.join(os.path.dirname(__file__), 'scratch')


# ═══════════════════════════════════════════════════════════════════════
# GENERATE REPORT
# ═══════════════════════════════════════════════════════════════════════

def generate_report():
    """Load data, build charts/tables, return HTML string."""
    today = pd.Timestamp.today().normalize()
    prev = today - pd.Timedelta(days=1)
    # Skip weekends: if Monday, compare vs Friday
    if prev.weekday() >= 5:
        prev = prev - pd.Timedelta(days=(prev.weekday() - 4))

    print(f"Generating report: {today.date()} vs {prev.date()}")

    # ── Load & classify ──
    df = load_and_prepare()
    classified = classify_events(df, today, prev)
    classified = enrich_events(classified, today)

    # ── Filter to unit types ──
    ut_col = next((c for c in ['UNIT_TYPE', 'UTYPE_DESC'] if c in classified.columns), None)
    if ut_col and UNIT_TYPES:
        cf = classified[classified[ut_col].isin(UNIT_TYPES)].copy()
    else:
        cf = classified.copy()

    print(f"Events: {len(cf)}")

    # ── Charts ──
    config = {
        'unit_types': UNIT_TYPES, 'event_type': 'Planned',
        'region': None, 'country': None,
        'hist_years': list(range(2021, 2026)), 'exclude_years': [2020],
        'show_years': [],
    }

    fig_p = build_seasonal_chart(classified, {**config, 'event_type': 'Planned'}, today)
    fig_u = build_seasonal_chart(classified, {**config, 'event_type': 'Unplanned'}, today)
    fig_c = build_seasonal_chart(classified, {**config, 'event_type': None}, today)
    fig_d = build_daily_ops_chart(classified, today, config)

    import base64

    def fig_to_img_tag(fig, width=700, height=380):
        """Convert Plotly figure to an inline <img> tag for email."""
        img_bytes = fig.to_image(format='png', width=width, height=height, scale=2)
        b64 = base64.b64encode(img_bytes).decode('utf-8')
        return f'<img src="data:image/png;base64,{b64}" width="{width}" style="max-width:100%;">'


#    chart1 = to_html(fig_p, full_html=False, include_plotlyjs='cdn')
#    chart2 = to_html(fig_u, full_html=False, include_plotlyjs=False)
#    chart_comb = to_html(fig_c, full_html=False, include_plotlyjs=False)
#    chart_daily = to_html(fig_d, full_html=False, include_plotlyjs=False)

    # Replace:
    # chart1 = to_html(fig_p, full_html=False, include_plotlyjs='cdn')
    # With:
    chart1 = fig_to_img_tag(fig_p)
    chart2 = fig_to_img_tag(fig_u)
    chart_comb = fig_to_img_tag(fig_c, width=900)
    chart_daily = fig_to_img_tag(fig_d, width=900)


    # ── Tables ──
    summary_df = build_change_summary(cf)
    actions_df = build_actions_table(cf)
    matrix_config = {'unit_types': UNIT_TYPES, 'forward_months': 12}
    regional_df, ml = build_forward_matrix(cf, matrix_config, today, group_col='Region')

#    summary_html = summary_df.to_html(index=False, na_rep='—', border=0) if not summary_df.empty else '<p>No changes.</p>'
    summary_html = (summary_df.style.format(SUMMARY_FMT, na_rep='—')
    .set_table_styles(TABLE_STYLES)
#    .set_caption(f'Change Summary — {today.strftime("%d %b %Y")} vs {prev.strftime("%d %b %Y")}')
    .hide(axis='index').to_html()) if not summary_df.empty else ''

    actions_html = style_actions(actions_df, config['unit_types'], today).to_html(index=False, na_rep='—', classes='styled-table') if not actions_df.empty else ''
    #actions_html = actions_df.to_html(index=False, na_rep='—', border=0) if not actions_df.empty else ''
    regional_html = regional_df.to_html(index=False, na_rep='—', border=0) if not regional_df.empty else ''

    # Collapsible regions JS for the detail matrix
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
        // Toggle arrow
        var arrow = this.querySelector('.arrow');
        if (arrow) arrow.textContent = visible ? '▸' : '▾';
        });
        // Start collapsed
        var region = header.dataset.region;
        document.querySelectorAll('.region-detail[data-region="' + region + '"]').forEach(function(r) {
        r.style.display = 'none';
        });
    });
    });
    </script>
    """

    # Build collapsible detail matrix HTML manually
    def matrix_to_collapsible_html(df, ml, caption):
        if df.empty: return ''
        header_cols = ['Country','Refinery','Unit','Unit Type'] + ml
        cols = [c for c in header_cols if c in df.columns]
        h = f'<table style="border-collapse:collapse;width:100%;font-family:Calibri,sans-serif;font-size:12px">'
        h += f'<caption style="font-size:14px;font-weight:bold;text-align:left;padding:10px 0">{caption}</caption>'
        h += '<thead><tr>'
        for c in cols:
            h += f'<th style="background:#1a1a2e;color:white;font-size:11px;padding:8px 10px;text-align:center;white-space:nowrap">{c}</th>'
        h += '</tr></thead><tbody>'
        for _, row in df.iterrows():
            rgn = str(row.get('Region',''))
            if rgn.startswith('▸ TOTAL'):
                h += '<tr style="background:#d5dbe0;font-weight:bold">'
                for c in cols:
                    v = row.get(c,'')
                    if c in ml:
                        try: v = fmt_kbd(v)
                        except: pass
                    h += f'<td style="padding:6px 10px;border-bottom:2px solid #1a1a2e">{v}</td>'
                h += '</tr>'
            elif rgn.startswith('▸'):
                import re
                clean_rgn = re.sub(r'\s*\(\d+\s*events?\)\s*$', '', rgn.replace('▸ ', ''))
                #clean_rgn = rgn.replace('▸ ','').split(' (')[0]
                h += f'<tr class="region-header" data-region="{clean_rgn}" style="background:#e9ecef;font-weight:bold">'
                for c in cols:
                    v = row.get(c,'')
                    if c == cols[0]:
                        v = f'<span class="arrow">▸</span> {rgn.replace("▸ ","")}'
                    elif c in ml:
                        try: v = fmt_kbd(v)
                        except: pass
                    h += f'<td style="padding:6px 10px;border-bottom:1px solid #ccc">{v}</td>'
                h += '</tr>'
            else:
                clean_rgn = str(row.get('Region',''))
                h += f'<tr class="region-detail" data-region="{clean_rgn}">'
                for c in cols:
                    v = row.get(c,'')
                    if c in ml:
                        try: v = fmt_kbd(v)
                        except: pass
                    style = 'padding:5px 10px;border-bottom:1px solid #eee'
                    if c == 'Refinery':
                        style += ';min-width:160px;max-width:220px;white-space:normal;word-wrap:break-word'
                    h += f'<td style="{style}">{v}</td>'
                h += '</tr>'
        h += '</tbody></table>'
        return h

    #regional_html = matrix_to_collapsible_html(regional_df, ml, f'')

    # ── Count key metrics for subject line ──
    n_actions = len(actions_df) if not actions_df.empty else 0
    n_new = len(cf[cf['Action'] == 'New']) if 'Action' in cf.columns else 0
    n_updated = len(cf[cf['Action'] == 'Updated']) if 'Action' in cf.columns else 0

    dt = today.strftime('%d %B %Y')
    ut_label = ' & '.join(UNIT_TYPES)

    # ── Assemble HTML ──
    # Paste your full notebook HTML template here, or use this clean version:
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
<h2>Jain Global Research Desk &nbsp;|&nbsp; {dt} &nbsp;|&nbsp; {ut_label}</h2>

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

    subject = f"IIR Maintenance Report — {today.strftime('%d %b %Y')} | {n_actions} actions ({n_new} new, {n_updated} updated)"

    return html, subject


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    html_body, subject = generate_report()

    # Save a copy
    if SAVE_COPY:
        os.makedirs(SAVE_DIR, exist_ok=True)
        fname = f"refinery_report_{datetime.now().strftime('%Y%m%d')}.html"
        path = os.path.join(SAVE_DIR, fname)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html_body)
        print(f"Saved: {path}")

    # Send via Kafka email service
    send_email(
        recipients=RECIPIENTS,
        subject=subject,
        body=html_body,
        from_address=FROM_ADDRESS,
        recipients_cc=RECIPIENTS_CC,
    )

    print("Done.")