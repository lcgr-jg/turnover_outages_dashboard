"""
Table builders — actions, change summary, forward matrix.
Returns DataFrames (not styled — Dash DataTable handles styling).
"""
import pandas as pd
import numpy as np
import calendar

from core import safe_ne, safe_ne_numeric, fmt_date, filter_event_type, days_in_month
from config import ACTION_COLORS


def build_actions_table(classified):
    """Build the Recent Event Actions table. Sorted by |Fwd Impact Δ|."""
    mask = classified['Action'].isin(['New', 'Updated', 'Cancelled', 'Completed', 'Past'])
    df = classified[mask].copy()

    df['_abs_delta'] = pd.to_numeric(
        df.get('Fwd_Impact_Delta_kb', pd.Series(0, index=df.index)),
        errors='coerce').abs()
    df = df.sort_values('_abs_delta', ascending=False, na_position='last')

    def desc(row):
        if row['Action'] not in ('Updated', 'Cancelled', 'Completed', 'Past'): return ''
        p = []
        ps = row.get('START_DATE_prev') if pd.notna(row.get('START_DATE_prev')) else row.get('PREV_START')
        pe = row.get('END_DATE_prev') if pd.notna(row.get('END_DATE_prev')) else row.get('PREV_END')
        if pd.notna(ps) and safe_ne(row['START_DATE'], ps):
            p.append(f"Start: {fmt_date(ps)}→{fmt_date(row['START_DATE'])}")
        if pd.notna(pe) and safe_ne(row['END_DATE'], pe):
            p.append(f"End: {fmt_date(pe)}→{fmt_date(row['END_DATE'])}")
        sp = row.get('E_STATUS_RESEARCH_prev')
        if pd.notna(sp) and safe_ne(row['E_STATUS_RESEARCH'], sp):
            p.append(f"{sp}→{row['E_STATUS_RESEARCH']}")
        cc = row.get('Cap_Change_kbd', 0)
        if pd.notna(cc) and safe_ne_numeric(cc, 0):
            p.append(f"Cap:{cc:+.1f}")
        return '; '.join(p)

    df['Change_Detail'] = df.apply(desc, axis=1)

    keep = ['Action', 'Category', 'COUNTRY', 'PLANT_NAME', 'PARENTNAME', 'UNIT_TYPE',
            'PREV_START', 'PREV_END', 'START_DATE', 'END_DATE',
            'CAP_OFFLINE_STD', 'CAP_UOM_STD', 'Cap_Change_kbd',
            'Fwd_Impact_kb', 'Fwd_Impact_Delta_kb', 'Utilisation_pct',
            'Change_Detail', 'COMMENTS', 'EVENT_ID']
    out = df[[c for c in keep if c in df.columns]].copy()
    out = out.rename(columns={
        'COUNTRY': 'Country', 'PLANT_NAME': 'Refinery', 'PARENTNAME': 'Parent',
        'UNIT_TYPE': 'Unit Type',
        'PREV_START': 'Prev Start', 'PREV_END': 'Prev End',
        'START_DATE': 'Start', 'END_DATE': 'End',
        'CAP_OFFLINE_STD': 'Capacity (kbd)', 'CAP_UOM_STD': 'UOM',
        'Cap_Change_kbd': 'Cap Δ (kbd)',
        'Fwd_Impact_kb': 'Fwd Impact (kb)', 'Fwd_Impact_Delta_kb': 'Fwd Impact Δ (kb)',
        'Utilisation_pct': 'Offline %', 'Change_Detail': 'Change Detail',
        'COMMENTS': 'Comments', 'EVENT_ID': 'Event ID',
    })
    for c in ['Prev Start', 'Prev End', 'Start', 'End']:
        if c in out.columns:
            out[c] = out[c].apply(fmt_date)
    return out.reset_index(drop=True)


def build_change_summary(classified):
    """Summary of actionable changes — one row per action type + total."""
    act = classified[classified['Action'].isin(
        ['New', 'Updated', 'Cancelled', 'Completed', 'Past'])].copy()
    if act.empty:
        return pd.DataFrame()

    rows = []
    for action in ['Cancelled', 'Completed', 'New', 'Updated', 'Past']:
        sub = act[act['Action'] == action]
        if sub.empty: continue
        cap = pd.to_numeric(sub['CAP_OFFLINE_STD'], errors='coerce').fillna(0)
        fwd = pd.to_numeric(sub.get('Fwd_Impact_kb', pd.Series(0, index=sub.index)),
                            errors='coerce').fillna(0)
        fwd_d = pd.to_numeric(sub.get('Fwd_Impact_Delta_kb', pd.Series(0, index=sub.index)),
                              errors='coerce').fillna(0)
        idx = cap.idxmax() if cap.any() else None
        rows.append({
            'Action': action, 'Count': len(sub),
            'Capacity (kbd)': round(cap.sum(), 1),
            'Fwd Impact (kb)': round(fwd.sum(), 0),
            'Fwd Impact Δ (kb)': round(fwd_d.sum(), 0),
            'Largest Event': sub.loc[idx, 'PLANT_NAME'] if idx else '—',
            'Largest (kbd)': round(cap.max(), 1) if cap.any() else 0,
        })

    # Total
    c_a = pd.to_numeric(act['CAP_OFFLINE_STD'], errors='coerce').fillna(0)
    fwd_a = pd.to_numeric(act.get('Fwd_Impact_kb', pd.Series(0, index=act.index)),
                          errors='coerce').fillna(0)
    fwd_d_a = pd.to_numeric(act.get('Fwd_Impact_Delta_kb', pd.Series(0, index=act.index)),
                            errors='coerce').fillna(0)
    rows.append({
        'Action': 'TOTAL', 'Count': len(act),
        'Capacity (kbd)': round(c_a.sum(), 1),
        'Fwd Impact (kb)': round(fwd_a.sum(), 0),
        'Fwd Impact Δ (kb)': round(fwd_d_a.sum(), 0),
        'Largest Event': '', 'Largest (kbd)': None,
    })
    return pd.DataFrame(rows)


def build_forward_matrix(classified, config, report_date, group_col=None,
                         subgroup_col=None, aggregate_subgroup: bool = False):
    """Forward matrix in kbd (avg per month). Returns (df, month_labels)."""
    ps = report_date.to_period('M').to_timestamp()
    n = config.get('forward_months', 12)
    pe = ps + pd.DateOffset(months=n) - pd.Timedelta(days=1)
    ut_list = config.get('unit_types')

    mask = ((classified['START_DATE'] <= pe) &
            (classified['END_DATE'].fillna(pe) >= ps) &
            classified['CAP_OFFLINE_STD'].notna() &
            classified['E_STATUS_RESEARCH'].isin(['Future', 'Ongoing']))
    if ut_list:
        uc = next((c for c in ['UNIT_TYPE', 'UTYPE_DESC'] if c in classified.columns), None)
        if uc: mask &= classified[uc].isin(ut_list)
    fwd = classified[mask].copy()

    months = pd.date_range(ps, periods=n, freq='MS')
    ml = [m.strftime('%b %y') for m in months]
    dim = [days_in_month(m.year, m.month) for m in months]

    def impact_kbd(start, end, cap):
        ed = end if pd.notna(end) else pe
        return [round(float(cap) * max((min(ed, ms + pd.offsets.MonthEnd(0)) - max(start, ms)).days + 1, 0) / dim[i], 1)
                for i, ms in enumerate(months)]

    cc = next((c for c in ['COUNTRY', 'P_COUNTRY'] if c in fwd.columns), None)
    rows = []
    for _, r in fwd.iterrows():
        v = impact_kbd(r['START_DATE'], r['END_DATE'], r['CAP_OFFLINE_STD'])
        if sum(v) == 0: continue
        rows.append({
            'Category': str(r.get('Category') or '—'),
            'Country': str(r[cc]) if cc else '—',
            'Refinery': str(r.get('PLANT_NAME', '')),
            'Parent': str(r.get('PARENTNAME', '') or ''),
            'Unit': str(r.get('UNIT_NAME', '')),
            'Unit Type': str(r.get('UNIT_TYPE', '')),
            'Region': str(r.get('jg_region', r.get('WORLD_REG', ''))),
            **dict(zip(ml, v)),
        })

    dm = pd.DataFrame(rows)
    if dm.empty:
        return dm, ml

    if group_col and subgroup_col and group_col in dm.columns and subgroup_col in dm.columns:
        # Two-level hierarchy: group subtotal header rows + subgroup detail rows
        grand = dm[ml].sum().round(1)
        parts = [pd.DataFrame([{group_col: '▸ TOTAL', subgroup_col: '', 'Parent': '', **grand.to_dict()}])]
        for grp_val in sorted(dm[group_col].unique()):
            subset = dm[dm[group_col] == grp_val].sort_values(subgroup_col).reset_index(drop=True)
            sub = subset[ml].sum().round(1)
            if aggregate_subgroup:
                # One row per unique subgroup value (e.g. plant name), months summed;
                # join back unique parent names per refinery as a comma-separated string
                detail = (
                    subset.groupby(subgroup_col)[ml].sum().round(1)
                    .sort_index().reset_index()
                )
                parents = (
                    subset.groupby(subgroup_col)['Parent']
                    .apply(lambda x: ', '.join(sorted(x.dropna().unique())))
                    .rename('Parent')
                )
                detail = detail.join(parents, on=subgroup_col)
                detail.insert(0, group_col, grp_val)
                label = f'▸ {grp_val} ({len(detail)} refineries)'
            else:
                # One row per individual event/unit
                detail = subset[[group_col, subgroup_col, 'Parent'] + ml].copy()
                label = f'▸ {grp_val} ({len(subset)} events)'
            parts.append(pd.DataFrame([{group_col: label, subgroup_col: '', 'Parent': '', **sub.to_dict()}]))
            parts.append(detail[[group_col, subgroup_col, 'Parent'] + ml])
        return pd.concat(parts, ignore_index=True), ml

    if group_col and group_col in dm.columns:
        g = dm.groupby(group_col)[ml].sum().round(1).sort_values(ml[0], ascending=False)
        grand = g.sum().round(1)
        grand.name = 'TOTAL'
        g = pd.concat([pd.DataFrame([grand]), g])
        return g.reset_index(), ml

    # Detail with region subtotals
    grand = dm[ml].sum().round(1)
    total_row = pd.DataFrame([{
        'Category': '', 'Country': '', 'Refinery': '', 'Parent': '', 'Unit': '', 'Unit Type': '',
        'Region': '▸ TOTAL', **grand.to_dict()
    }])
    detail_parts = [total_row]
    for region in sorted(dm['Region'].unique()):
        rg = dm[dm['Region'] == region].sort_values(['Country', 'Refinery']).reset_index(drop=True)
        sub = rg[ml].sum().round(1)
        header = pd.DataFrame([{
            'Category': '', 'Country': '', 'Refinery': '', 'Parent': '', 'Unit': '', 'Unit Type': '',
            'Region': f'▸ {region} ({len(rg)} events)', **sub.to_dict()
        }])
        detail_parts.append(header)
        detail_parts.append(rg)
    return pd.concat(detail_parts, ignore_index=True), ml
