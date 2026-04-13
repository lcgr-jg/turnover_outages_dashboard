from config import TABLE_STYLES, DEFAULT_UNIT_TYPES
from datetime import datetime

import pandas as pd
import numpy as np


REPORT_YEAR = datetime.now().year
REPORT_DATE = datetime.now()


def fmt_kbd(v):
    try:
        n = float(v)
        return '—' if abs(n)<0.05 else f'{n:,.1f}'
    except: return ''


def style_actions(df, unit_types, report_date):
    cm = {'Cancelled': 'background-color:#fce4ec', 'Completed': 'background-color:#d1ecf1',
          'New': 'background-color:#d4edda', 'Updated': 'background-color:#fff3cd',
          'Past': 'background-color:#e8eaf6'}
    def rs(row): return [cm.get(row['Action'], '')] * len(row)
    fmt = {}
    if 'Capacity (kbd)' in df.columns: fmt['Capacity (kbd)'] = '{:.1f}'
    if 'Cap Δ (kbd)' in df.columns: fmt['Cap Δ (kbd)'] = '{:+.1f}'
    if 'Fwd Impact (kb)' in df.columns: fmt['Fwd Impact (kb)'] = '{:,.0f}'
    if 'Fwd Impact Δ (kb)' in df.columns: fmt['Fwd Impact Δ (kb)'] = '{:+,.0f}'
    if 'Offline %' in df.columns: fmt['Offline %'] = '{:.1f}%'
    return (df.style.apply(rs, axis=1).format(fmt, na_rep='—')
        .set_table_styles(TABLE_STYLES)
        .set_properties(subset=['Refinery'], **{'min-width': '180px', 'max-width': '250px', 'white-space': 'normal', 'word-wrap': 'break-word'})
        .set_properties(subset=['Change Detail'], **{'min-width': '200px', 'max-width': '320px', 'white-space': 'normal', 'word-wrap': 'break-word', 'font-size': '11px'})
        .set_caption(f'IIR Refinery Offline Events — {report_date.year} | {' & '.join(unit_types)} | Generated {report_date.date()}')
        .hide(axis='index'))


def style_matrix(df, ml, caption=''):
    if df.empty: return None
    def rs(row):
        rgn = str(row.get('Region','')) if 'Region' in row.index else str(row.iloc[0])
        if rgn.startswith('▸') or rgn=='TOTAL':
            return ['background-color:#e9ecef;font-weight:bold']*len(row)
        return ['']*len(row)
    s = (df.style.apply(rs, axis=1)
        .format({m:fmt_kbd for m in ml if m in df.columns})
        .set_table_styles(TABLE_STYLES)
        .set_caption(caption).hide(axis='index'))
    if 'Refinery' in df.columns:
        s = s.set_properties(subset=['Refinery'], **{'min-width':'160px','max-width':'220px','white-space':'normal','word-wrap':'break-word'})
    return s