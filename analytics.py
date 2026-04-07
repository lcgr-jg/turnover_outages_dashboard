"""
Analytics — TA cycle prediction, reliability scoring, announcement gaps.
"""
import pandas as pd
import numpy as np
from core import filter_event_type, fmt_date


def build_ta_cycle_analysis(df, report_date, unit_types=None,
                            event_type='Planned', unit_status=None):
    """Turnaround cycle analysis — flag overdue units."""
    if unit_status is None:
        unit_status = ['Operational']
    ev = df.sort_values('TIME_STAMP').groupby('EVENT_ID').last().reset_index()
    if 'E_STATUS_RESEARCH' in ev.columns:
        ev = ev[ev['E_STATUS_RESEARCH'].isin(['Past', 'Ongoing'])]
    if unit_types:
        ut = next((c for c in ['UNIT_TYPE', 'UTYPE_DESC'] if c in ev.columns), None)
        if ut: ev = ev[ev[ut].isin(unit_types)]
    if unit_status:
        us = next((c for c in ['U_STATUS', 'UNIT_STATUS'] if c in ev.columns), None)
        if us: ev = ev[ev[us].isin(unit_status)]
    if event_type:
        ev = filter_event_type(ev, event_type)

    ev = ev[ev['START_DATE'].notna() & ev['END_DATE'].notna()].copy()
    ev = ev.sort_values(['UNIT_ID', 'START_DATE'])
    ev['prev_end'] = ev.groupby('UNIT_ID')['END_DATE'].shift(1)
    ev['interval_days'] = (ev['START_DATE'] - ev['prev_end']).dt.days

    unit_stats = ev.groupby('UNIT_ID').agg(
        n_tas=('EVENT_ID', 'nunique'),
        last_ta_start=('START_DATE', 'max'),
        last_ta_end=('END_DATE', 'max'),
        avg_interval=('interval_days', 'mean'),
        std_interval=('interval_days', 'std'),
        avg_duration=('E_DURATION', 'mean'),
        total_cap=('CAP_OFFLINE_STD', 'max'),
    ).reset_index()

    info_cols = ['UNIT_ID', 'UNIT_NAME', 'PLANT_NAME', 'COUNTRY', 'WORLD_REG',
                 'U_CAPACITY_STD', 'UNIT_TYPE']
    info = ev[[c for c in info_cols if c in ev.columns]].drop_duplicates('UNIT_ID')
    unit_stats = unit_stats.merge(info, on='UNIT_ID', how='left')

    unit_stats['days_since_last_ta'] = (report_date - unit_stats['last_ta_end']).dt.days
    unit_stats['expected_next_ta'] = (unit_stats['last_ta_end'] +
                                      pd.to_timedelta(unit_stats['avg_interval'].fillna(0), unit='D'))
    unit_stats['days_until_expected'] = (unit_stats['expected_next_ta'] - report_date).dt.days
    unit_stats['overdue'] = unit_stats['days_since_last_ta'] > unit_stats['avg_interval']
    unit_stats['overdue_by_days'] = (unit_stats['days_since_last_ta'] - unit_stats['avg_interval']).clip(lower=0).round(0)
    unit_stats = unit_stats[unit_stats['n_tas'] >= 2].copy()
    unit_stats = unit_stats.sort_values('days_until_expected', ascending=True)
    unit_stats['avg_interval_months'] = (unit_stats['avg_interval'] / 30.44).round(1)
    return unit_stats


def build_reliability_scores(df, report_date, unit_types=None, lookback_years=3,
                             unit_status=None):
    """Score refineries by unplanned outage frequency and severity."""
    if unit_status is None:
        unit_status = ['Operational']
    ev = df.sort_values('TIME_STAMP').groupby('EVENT_ID').last().reset_index()
    cutoff = report_date - pd.DateOffset(years=lookback_years)
    ev = ev[ev['START_DATE'].notna() & (ev['START_DATE'] >= cutoff)].copy()
    if unit_types:
        ut = next((c for c in ['UNIT_TYPE', 'UTYPE_DESC'] if c in ev.columns), None)
        if ut: ev = ev[ev[ut].isin(unit_types)]
    if unit_status:
        us = next((c for c in ['U_STATUS', 'UNIT_STATUS'] if c in ev.columns), None)
        if us: ev = ev[ev[us].isin(unit_status)]

    unplanned = filter_event_type(ev, 'Unplanned')
    all_events = ev.copy()

    def plant_stats(events, label):
        if events.empty: return pd.DataFrame()
        dur = pd.to_numeric(events['E_DURATION'], errors='coerce').fillna(0)
        stats = events.groupby('PLANT_ID').agg(
            n_events=('EVENT_ID', 'nunique'),
            total_offline_days=('E_DURATION', 'sum'),
            avg_cap_offline=('CAP_OFFLINE_STD', 'mean'),
            max_cap_offline=('CAP_OFFLINE_STD', 'max'),
        ).reset_index()
        stats.columns = ['PLANT_ID'] + [f'{label}_{c}' for c in stats.columns[1:]]
        return stats

    up_stats = plant_stats(unplanned, 'unplanned')
    all_stats = plant_stats(all_events, 'all')
    if up_stats.empty: return pd.DataFrame()

    scores = all_stats.merge(up_stats, on='PLANT_ID', how='left').fillna(0)
    scores['unplanned_event_ratio'] = (scores['unplanned_n_events'] / scores['all_n_events'].clip(lower=1) * 100).round(1)

    plant_info = ev[['PLANT_ID', 'PLANT_NAME', 'COUNTRY', 'WORLD_REG']].drop_duplicates('PLANT_ID')
    scores = scores.merge(plant_info, on='PLANT_ID', how='left')
    plant_cap = ev.groupby('PLANT_ID')['U_CAPACITY_STD'].max().reset_index()
    plant_cap.columns = ['PLANT_ID', 'plant_capacity']
    scores = scores.merge(plant_cap, on='PLANT_ID', how='left')

    scores['events_per_year'] = (scores['unplanned_n_events'] / lookback_years).round(1)
    scores['risk_score'] = (scores['events_per_year'] * scores['unplanned_avg_cap_offline']).round(0)
    return scores.sort_values('risk_score', ascending=False)


def find_ta_announcement_gaps(ta_cycles, classified, report_date, window_months=6):
    """Units due for TA but no Future event announced."""
    window_end = report_date + pd.DateOffset(months=window_months)
    due_soon = ta_cycles[
        (ta_cycles['expected_next_ta'] <= window_end) &
        (ta_cycles['expected_next_ta'] >= report_date - pd.DateOffset(months=3))
    ].copy()
    if due_soon.empty: return pd.DataFrame()

    future_units = classified[
        (classified['E_STATUS_RESEARCH'] == 'Future') &
        (classified['EVENT_TYPE'] == 'Planned')
    ]['UNIT_ID'].unique()
    due_soon['has_announced_ta'] = due_soon['UNIT_ID'].isin(future_units)
    return due_soon[~due_soon['has_announced_ta']].copy()
