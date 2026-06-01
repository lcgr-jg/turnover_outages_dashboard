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


def matrix_prev_col(ml):
    """First month label when it is the historical reference column."""
    return ml[0] if ml and str(ml[0]).endswith('(prev)') else None


def matrix_forward_cols(ml):
    prev = matrix_prev_col(ml)
    return ml[1:] if prev else ml


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
    styled = (df.style.apply(rs, axis=1).format(fmt, na_rep='—')
        .set_table_styles(TABLE_STYLES)
        .set_properties(subset=['Refinery'], **{'min-width': '180px', 'max-width': '250px', 'white-space': 'normal', 'word-wrap': 'break-word'})
        .set_properties(subset=['Change Detail'], **{'min-width': '200px', 'max-width': '320px', 'white-space': 'normal', 'word-wrap': 'break-word', 'font-size': '11px'})
        .set_caption(f'IIR Refinery Offline Events — {report_date.year} | {' & '.join(unit_types)} | Generated {report_date.date()}')
        .hide(axis='index'))
    if 'Parent' in df.columns:
        styled = styled.set_properties(subset=['Parent'], **{'min-width': '150px', 'max-width': '220px', 'white-space': 'normal', 'word-wrap': 'break-word'})
    if 'Event Type' in df.columns:
        styled = styled.map(
            lambda v: 'background-color:#e65100;color:white;font-weight:bold' if v == 'Unplanned' else '',
            subset=['Event Type']
        )
    return styled


def style_matrix(df, ml, caption=''):
    if df.empty: return None
    prev_col = matrix_prev_col(ml)

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
    if 'Parent' in df.columns:
        s = s.set_properties(subset=['Parent'], **{'min-width':'140px','max-width':'200px','white-space':'normal','word-wrap':'break-word'})
    # Previous month reads as historical reference, not forward impact
    if prev_col and prev_col in df.columns:
        s = s.set_properties(subset=[prev_col], **{
            'background-color': '#f0f2f4',
            'color': '#607D8B',
            'font-style': 'italic',
            'border-right': '2px solid #adb5bd',
        })
        col_idx = list(df.columns).index(prev_col) + 1
        s = s.set_table_styles([{
            'selector': f'thead th:nth-child({col_idx})',
            'props': [
                ('background-color', '#607D8B'),
                ('font-style', 'italic'),
                ('border-right', '2px solid #adb5bd'),
            ],
        }], overwrite=False)
    return s