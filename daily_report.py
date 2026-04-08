"""
daily_report.py — Generate HTML report and email to team.
Run via Task Scheduler: .venv\Scripts\python.exe dash_irr_database\daily_report.py

Schedule: Mon-Fri at 9:15 AM (before team arrives, after IIR overnight update)
"""

import os
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import pandas as pd

# ── Add project root to path ──
sys.path.insert(0, os.path.dirname(__file__))

from config import DEFAULT_UNIT_TYPES
from data import load_and_prepare
from classification import classify_events, enrich_events
from charts import build_seasonal_chart, build_daily_ops_chart, build_combined_monthly_chart
from tables import build_actions_table, build_change_summary, build_forward_matrix
from plotly.io import to_html


# ═══════════════════════════════════════════════════════════════════════
# CONFIG — edit these
# ═══════════════════════════════════════════════════════════════════════

RECIPIENTS = [
 #   'Yves.Mindren@jainglobal.com',
 #   'abderrahmane.mahoudi@jainglobal.com',
    'luiscarlos.gaitan@jainglobal.com',
    # add more as needed
]

# SMTP settings — ask your IT team for the correct server
# Option A: Outlook/Exchange
SMTP_SERVER = 'smtp.office365.com'
SMTP_PORT = 587
SMTP_USER = 'luiscarlos.gaitan@jainglobal.com'  # your email
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')  # set as environment variable, never hardcode

# Option B: If your firm uses an internal relay that doesn't need auth
#SMTP_SERVER = 'mail.jainglobal.com'
#SMTP_PORT = 25
#SMTP_USER = None
#SMTP_PASSWORD = None

UNIT_TYPES = DEFAULT_UNIT_TYPES
SAVE_COPY = True  # also save HTML to disk
SAVE_DIR = os.path.join(os.path.dirname(__file__), '..', 'scratch')


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

    chart1 = to_html(fig_p, full_html=False, include_plotlyjs='cdn')
    chart2 = to_html(fig_u, full_html=False, include_plotlyjs=False)
    chart_comb = to_html(fig_c, full_html=False, include_plotlyjs=False)
    chart_daily = to_html(fig_d, full_html=False, include_plotlyjs=False)

    # ── Tables ──
    summary_df = build_change_summary(cf)
    actions_df = build_actions_table(cf)
    matrix_config = {'unit_types': UNIT_TYPES, 'forward_months': 12}
    regional_df, ml = build_forward_matrix(cf, matrix_config, today, group_col='Region')

    summary_html = summary_df.to_html(index=False, na_rep='—', border=0) if not summary_df.empty else '<p>No changes.</p>'
    actions_html = actions_df.to_html(index=False, na_rep='—', border=0) if not actions_df.empty else ''
    regional_html = regional_df.to_html(index=False, na_rep='—', border=0) if not regional_df.empty else ''

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

</body></html>"""

    subject = f"IIR Maintenance Report — {today.strftime('%d %b %Y')} | {n_actions} actions ({n_new} new, {n_updated} updated)"

    return html, subject


# ═══════════════════════════════════════════════════════════════════════
# SEND EMAIL
# ═══════════════════════════════════════════════════════════════════════

def send_email(html_content, subject, recipients):
    """Send HTML email via SMTP."""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = SMTP_USER
    msg['To'] = ', '.join(recipients)

    # Plain text fallback
    text_part = MIMEText("Open this email in an HTML-capable client to see the report.", 'plain')
    html_part = MIMEText(html_content, 'html')

    msg.attach(text_part)
    msg.attach(html_part)

    try:
        if SMTP_USER and SMTP_PASSWORD:
            # Authenticated SMTP (Office 365, Gmail, etc.)
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            # Unauthenticated relay (internal mail server)
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.send_message(msg)

        print(f"Email sent to {len(recipients)} recipients")

    except Exception as e:
        print(f"ERROR sending email: {e}")
        raise


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    html, subject = generate_report()

    # Save a copy to disk
    if SAVE_COPY:
        os.makedirs(SAVE_DIR, exist_ok=True)
        fname = f"refinery_report_{datetime.now().strftime('%Y%m%d')}.html"
        path = os.path.join(SAVE_DIR, fname)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"Saved: {path}")

    # Send email
    if RECIPIENTS:
        send_email(html, subject, RECIPIENTS)
    else:
        print("No recipients configured — skipping email")

    print("Done.")