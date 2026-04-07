"""
Chart builders — seasonal maintenance chart, daily ops chart.
"""
import numpy as np
import pandas as pd
import calendar
import plotly.graph_objects as go

from config import (MONTHS, CLR_PLANNED, CLR_UNPLANNED, CLR_TOTAL,
                    CLR_ESTIMATE, CLR_BAND, CLR_MEDIAN)
from core import filter_event_type, days_in_month


def monthly_capacity(df_ev, year, unit_types=None, countries=None,
                     world_regions=None, event_type=None):
    """
    12-element array: average daily offline capacity (mb/d) for each month.
    Uses overlap-weighted calculation: cap × overlap_days / days_in_month / 1000.
    """
    cap_col = 'CAP_OFFLINE_STD' if 'CAP_OFFLINE_STD' in df_ev.columns else 'CAP_OFFLINE'
    ev = df_ev.copy()
    if 'E_STATUS_RESEARCH' in ev.columns:
        ev = ev[ev['E_STATUS_RESEARCH'] != 'Cancelled']
    if unit_types:
        ut = next((c for c in ['UNIT_TYPE', 'UTYPE_DESC'] if c in ev.columns), None)
        if ut: ev = ev[ev[ut].isin(unit_types)]
    if countries:
        cc = next((c for c in ['COUNTRY', 'P_COUNTRY'] if c in ev.columns), None)
        if cc: ev = ev[ev[cc].isin(countries)]
    if world_regions:
        rc = next((c for c in ['WORLD_REG', 'WORLD_REGION'] if c in ev.columns), None)
        if rc: ev = ev[ev[rc].isin(world_regions)]
    if event_type:
        ev = filter_event_type(ev, event_type)

    yr_end = pd.Timestamp(year, 12, 31)
    ev['_end'] = ev['END_DATE'].fillna(yr_end).clip(upper=yr_end)
    ev = ev[ev['START_DATE'].notna() & (ev['START_DATE'] <= yr_end)]
    ev = ev[ev['_end'] >= pd.Timestamp(year, 1, 1)]

    monthly = np.zeros(12)
    for m in range(1, 13):
        ms = pd.Timestamp(year, m, 1)
        me = pd.Timestamp(year, m, days_in_month(year, m))
        d = (me - ms).days + 1
        act = ev[(ev['START_DATE'] <= me) & (ev['_end'] >= ms)]
        if act.empty: continue
        overlap = (act['_end'].clip(upper=me) - act['START_DATE'].clip(lower=ms)).dt.days + 1
        cap = pd.to_numeric(act[cap_col], errors='coerce').fillna(0)
        monthly[m - 1] = (cap * overlap.clip(lower=0) / d).sum() / 1_000
    return monthly


def build_seasonal_chart(df_ev, config, report_date):
    """
    JG-style seasonal chart with historical band, median, individual years,
    current year actual + estimate. Returns Plotly figure.

    config keys:
        unit_types, event_type, region, country,
        hist_years, exclude_years, show_years (list of individual years to plot)
    """
    ut = config.get('unit_types')
    et = config.get('event_type')
    countries = [config['country']] if config.get('country') else None
    regions = [config['region']] if config.get('region') else None
    hist_years = config.get('hist_years', [])
    exclude = config.get('exclude_years', [])
    show_years = config.get('show_years', [])  # individual years to plot as separate lines
    cur_year = report_date.year
    kw = dict(unit_types=ut, countries=countries, world_regions=regions, event_type=et)

    # Historical band
    if hist_years:
        hm = np.array([monthly_capacity(df_ev, y, **kw) for y in hist_years])
        bmin, bmax = hm.min(axis=0), hm.max(axis=0)
    else:
        bmin = bmax = np.zeros(12)

    # Median
    med_yrs = [y for y in hist_years if y not in exclude]
    med = np.median([monthly_capacity(df_ev, y, **kw) for y in med_yrs], axis=0) if med_yrs else None

    # Current year
    cur = monthly_capacity(df_ev, cur_year, **kw)
    rm = report_date.month - 1
    actual = [cur[i] if i <= rm else None for i in range(12)]
    fwd = [cur[i] if i >= rm else None for i in range(12)]

    fig = go.Figure()

    # Historical band
    if hist_years and bmax.sum() > 0:
        fig.add_trace(go.Scatter(
            x=MONTHS + MONTHS[::-1],
            y=np.concatenate([bmax, bmin[::-1]]).tolist(),
            fill='toself', fillcolor='rgba(180,180,180,0.35)',
            line=dict(color='rgba(0,0,0,0)'),
            name=f'{hist_years[0]}–{hist_years[-1]}', hoverinfo='skip'))

    # Median
    if med is not None and med.sum() > 0:
        fig.add_trace(go.Scatter(
            x=MONTHS, y=med.tolist(), mode='lines',
            line=dict(color=CLR_MEDIAN, width=1.5, dash='dash', shape='spline'),
            name='Median', connectgaps=True))

    # Individual years
    year_colors = ['#2196F3', '#4CAF50', '#FF9800', '#9C27B0', '#795548', '#607D8B']
    for idx, yr in enumerate(show_years):
        if yr == cur_year: continue  # skip current year, shown separately
        yr_data = monthly_capacity(df_ev, yr, **kw)
        if sum(yr_data) == 0: continue
        fig.add_trace(go.Scatter(
            x=MONTHS, y=yr_data.tolist(), mode='lines',
            line=dict(color=year_colors[idx % len(year_colors)], width=1.5, shape='spline'),
            name=str(yr), connectgaps=True))

    # Current year actual
    fig.add_trace(go.Scatter(
        x=MONTHS, y=actual, mode='lines',
        line=dict(color=CLR_PLANNED, width=2.5, shape='spline'),
        name=str(cur_year), connectgaps=False))

    # Forward estimate
    fig.add_trace(go.Scatter(
        x=MONTHS, y=fwd, mode='lines',
        line=dict(color=CLR_ESTIMATE, width=2, dash='dash', shape='spline'),
        name=f'{cur_year}E', connectgaps=False))

    rgn = (config.get('region') or 'GLOBAL').upper()
    etype = (et or 'ALL').upper()
    fig.update_layout(
        title=dict(
            text=f'{rgn} PETROLEUM MARKETS<br><sub>{etype} ATMOSPHERIC DISTILLATION MAINTENANCE</sub>',
            font=dict(size=13, color='#1a3a5c'), x=0.02),
        xaxis=dict(title='', showgrid=False),
        yaxis=dict(title='mb/d', showgrid=True, gridcolor='#ececec', rangemode='tozero'),
        legend=dict(orientation='h', x=0.5, xanchor='center', y=1.12),
        plot_bgcolor='white', paper_bgcolor='white', height=380,
        margin=dict(l=50, r=20, t=70, b=40),
        annotations=[dict(text='Source: IIR Energy, Jain Global Research Desk',
                          xref='paper', yref='paper', x=0, y=-0.12,
                          showarrow=False, font=dict(size=9, color='grey'))],
    )
    return fig


def build_daily_ops_chart(df_ev, report_date, config, lookback_days=90, forward_days=60):
    """
    Time-series chart of daily offline capacity (mb/d).
    Stacked Planned + Unplanned with Total line.
    """
    cap_col = 'CAP_OFFLINE_STD' if 'CAP_OFFLINE_STD' in df_ev.columns else 'CAP_OFFLINE'
    ev = df_ev.copy()
    if 'E_STATUS_RESEARCH' in ev.columns:
        ev = ev[ev['E_STATUS_RESEARCH'] != 'Cancelled']
    ut_list = config.get('unit_types')
    if ut_list:
        uc = next((c for c in ['UNIT_TYPE', 'UTYPE_DESC'] if c in ev.columns), None)
        if uc: ev = ev[ev[uc].isin(ut_list)]
    if config.get('region'):
        rc = next((c for c in ['WORLD_REG', 'WORLD_REGION'] if c in ev.columns), None)
        if rc: ev = ev[ev[rc] == config['region']]

    date_start = report_date - pd.Timedelta(days=lookback_days)
    date_end = report_date + pd.Timedelta(days=forward_days)
    date_range = pd.date_range(date_start, date_end, freq='D')

    ev = ev[ev['START_DATE'].notna()].copy()
    ev['_end'] = ev['END_DATE'].fillna(date_end)
    ev = ev[(ev['START_DATE'] <= date_end) & (ev['_end'] >= date_start)]

    planned = filter_event_type(ev, 'Planned')
    unplanned = filter_event_type(ev, 'Unplanned')

    def daily_sum(subset, dates):
        vals = []
        for d in dates:
            active = subset[(subset['START_DATE'] <= d) & (subset['_end'] >= d)]
            vals.append(pd.to_numeric(active[cap_col], errors='coerce').fillna(0).sum() / 1_000)
        return vals

    planned_daily = daily_sum(planned, date_range)
    unplanned_daily = daily_sum(unplanned, date_range)
    total_daily = [p + u for p, u in zip(planned_daily, unplanned_daily)]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=date_range, y=planned_daily, name='Planned',
                             fill='tozeroy', fillcolor='rgba(26,58,92,0.3)',
                             line=dict(color=CLR_PLANNED, width=1)))
    fig.add_trace(go.Scatter(x=date_range, y=unplanned_daily, name='Unplanned',
                             fill='tonexty', fillcolor='rgba(224,123,57,0.3)',
                             line=dict(color=CLR_UNPLANNED, width=1)))
    fig.add_trace(go.Scatter(x=date_range, y=total_daily, name='Total',
                             line=dict(color=CLR_TOTAL, width=2, dash='dot'), fill=None))

    fig.add_shape(type='line',
                  x0=str(report_date.date()), x1=str(report_date.date()),
                  y0=0, y1=1, yref='paper',
                  line=dict(dash='dash', color='red', width=1))
    fig.add_annotation(x=str(report_date.date()), y=1, yref='paper',
                       text='Today', showarrow=False,
                       font=dict(color='red', size=10),
                       xanchor='left', yanchor='bottom')

    rgn = (config.get('region') or 'Global').title()
    fig.update_layout(
        title=dict(text=f'{rgn} — Daily Offline Capacity',
                   font=dict(size=13, color='#1a3a5c'), x=0.02),
        xaxis=dict(title='', showgrid=False),
        yaxis=dict(title='mb/d', showgrid=True, gridcolor='#ececec', rangemode='tozero'),
        legend=dict(orientation='h', x=0.5, xanchor='center', y=1.08),
        plot_bgcolor='white', paper_bgcolor='white', height=350,
        margin=dict(l=50, r=20, t=60, b=40),
        annotations=[dict(text='Source: IIR Energy', xref='paper', yref='paper',
                          x=0, y=-0.1, showarrow=False, font=dict(size=9, color='grey'))],
    )
    return fig
