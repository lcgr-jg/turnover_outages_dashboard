"""
Core utility functions — deduplication, NaN-safe comparisons, helpers.
"""
import pandas as pd
import numpy as np
import calendar


def get_latest_snapshot(df, as_of):
    """Latest row per EVENT_ID where RELEASE_DT <= as_of."""
    return (df[df['RELEASE_DT'] <= as_of]
            .sort_values('TIME_STAMP')
            .groupby('EVENT_ID').last().reset_index())


def safe_ne(a, b):
    if pd.isna(a) and pd.isna(b): return False
    if pd.isna(a) or pd.isna(b): return True
    return a != b


def safe_ne_numeric(a, b, tol=0.001):
    if pd.isna(a) and pd.isna(b): return False
    if pd.isna(a) or pd.isna(b): return True
    return abs(a - b) > tol


def fmt_date(dt):
    return '—' if pd.isna(dt) else dt.strftime('%d %b %Y')


def filter_event_type(df, event_type):
    """Exact match on EVENT_TYPE. NOT str.contains."""
    if event_type is None: return df
    et = next((c for c in ['EVENT_TYPE', 'EVENT_KIND'] if c in df.columns), None)
    if et and event_type in df[et].values:
        return df[df[et] == event_type]
    return df


def compute_duration_days(start, end):
    if pd.isna(start) or pd.isna(end): return np.nan
    return max((end - start).days, 0)


def days_in_month(year, month):
    return calendar.monthrange(year, month)[1]
