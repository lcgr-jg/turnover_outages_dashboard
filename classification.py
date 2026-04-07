"""
Event classification and enrichment.
"""
import pandas as pd
import numpy as np
from core import (get_latest_snapshot, safe_ne, safe_ne_numeric,
                  compute_duration_days)


def classify_events(df, report_date, prev_report_date):
    """
    Classify each EVENT_ID into exactly one action.
    Priority: Cancelled → Completed → Updated → New → Past → Unchanged.
    """
    latest = get_latest_snapshot(df, report_date)
    prev = get_latest_snapshot(df, prev_report_date)
    new_ids = set(latest['EVENT_ID']) - set(prev['EVENT_ID'])
    merged = latest.merge(
        prev[['EVENT_ID', 'START_DATE', 'END_DATE', 'CAP_OFFLINE_STD', 'E_STATUS_RESEARCH']],
        on='EVENT_ID', suffixes=('', '_prev'), how='left')

    def _c(r):
        st, stp = r['E_STATUS_RESEARCH'], r.get('E_STATUS_RESEARCH_prev')
        nw, hp = r['EVENT_ID'] in new_ids, pd.notna(r.get('PREV_START'))
        if st == 'Cancelled':
            if nw or (pd.notna(stp) and stp != 'Cancelled'): return 'Cancelled'
        if st == 'Past' and not nw and pd.notna(stp) and stp != 'Past':
            return 'Completed'
        if not nw and pd.notna(stp):
            if (safe_ne(r['START_DATE'], r.get('START_DATE_prev')) or
                safe_ne(r['END_DATE'], r.get('END_DATE_prev')) or
                safe_ne_numeric(r['CAP_OFFLINE_STD'], r.get('CAP_OFFLINE_STD_prev')) or
                safe_ne(st, stp)):
                return 'Updated'
        if nw and hp: return 'Updated'
        if nw and not hp and st not in ('Cancelled', 'Past'): return 'New'
        if st == 'Past':
            if nw and not hp: return 'Past'
        return 'Unchanged'

    merged['Action'] = merged.apply(_c, axis=1)
    assert len(merged) == merged['EVENT_ID'].nunique()
    return merged


def enrich_events(df, report_date):
    """Add derived columns: utilisation, cap change, forward impact."""
    df = df.copy()
    cap = pd.to_numeric(df['CAP_OFFLINE_STD'], errors='coerce')
    ucap = pd.to_numeric(df.get('U_CAPACITY_STD', pd.Series(dtype=float)), errors='coerce')
    cap_prev = pd.to_numeric(df.get('CAP_OFFLINE_STD_prev', pd.Series(dtype=float)), errors='coerce')

    df['_dur_now'] = df.apply(lambda r: compute_duration_days(r['START_DATE'], r['END_DATE']), axis=1)
    df['_dur_prev'] = df.apply(lambda r: compute_duration_days(r.get('START_DATE_prev'), r.get('END_DATE_prev')), axis=1)

    df['Utilisation_pct'] = (cap / ucap * 100).round(1)
    df['Cap_Change_kbd'] = (cap - cap_prev).round(1)
    df['Market_Impact_kb'] = (cap * df['_dur_now']).round(0)
    df['Market_Impact_kb_prev'] = (cap_prev * df['_dur_prev']).round(0)

    def forward_days(row):
        if pd.isna(row['END_DATE']) or pd.isna(row['START_DATE']): return 0
        if row['END_DATE'] <= report_date: return 0
        return max((row['END_DATE'] - max(row['START_DATE'], report_date)).days, 0)

    def forward_days_prev(row):
        end_p, start_p = row.get('END_DATE_prev'), row.get('START_DATE_prev')
        if pd.isna(end_p) or pd.isna(start_p): return 0
        if end_p <= report_date: return 0
        return max((end_p - max(start_p, report_date)).days, 0)

    df['_fwd_days'] = df.apply(forward_days, axis=1)
    df['_fwd_days_prev'] = df.apply(forward_days_prev, axis=1)
    df['Fwd_Impact_kb'] = (cap * df['_fwd_days']).round(0)
    df['Fwd_Impact_kb_prev'] = (cap_prev * df['_fwd_days_prev']).round(0)
    df['Fwd_Impact_Delta_kb'] = (df['Fwd_Impact_kb'] - df['Fwd_Impact_kb_prev']).round(0)

    cc_mask = df['Action'].isin(['Completed', 'Cancelled'])
    df.loc[cc_mask, 'Fwd_Impact_kb'] = 0
    df.loc[cc_mask, 'Fwd_Impact_Delta_kb'] = (
        0 - df.loc[cc_mask, 'Fwd_Impact_kb_prev'].fillna(0)).round(0)

    return df
