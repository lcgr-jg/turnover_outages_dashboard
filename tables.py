"""
Table builders — actions, change summary, forward matrix.
Returns DataFrames (not styled — Dash DataTable handles styling).
"""
import pandas as pd
import numpy as np
import calendar

from core import safe_ne, safe_ne_numeric, fmt_date, filter_event_type, days_in_month
from config import ACTION_COLORS

INSTALLED_CAP_COL = 'Installed (kbd)'
MATRIX_CAP_COLS = [INSTALLED_CAP_COL]


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

    keep = ['Action', 'EVENT_TYPE', 'Category', 'COUNTRY', 'PLANT_NAME', 'PARENTNAME', 'UNIT_TYPE',
            'PREV_START', 'PREV_END', 'START_DATE', 'END_DATE',
            'CAP_OFFLINE_STD', 'CAP_UOM_STD', 'Cap_Change_kbd',
            'Fwd_Impact_kb', 'Fwd_Impact_Delta_kb', 'Utilisation_pct',
            'Change_Detail', 'COMMENTS', 'EVENT_ID']
    out = df[[c for c in keep if c in df.columns]].copy()
    out = out.rename(columns={
        'EVENT_TYPE': 'Event Type',
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


def _filter_units_for_nameplate(classified, ut_list):
    """Units in scope with a usable nameplate capacity."""
    base = classified.copy()
    if ut_list:
        uc = next((c for c in ['UNIT_TYPE', 'UTYPE_DESC'] if c in base.columns), None)
        if uc:
            base = base[base[uc].isin(ut_list)]
    cap = pd.to_numeric(base.get('U_CAPACITY_STD'), errors='coerce')
    base = base[cap.notna() & (cap > 0)].copy()
    if base.empty:
        return base
    if 'UNIT_ID' in base.columns:
        return base.sort_values('U_CAPACITY_STD').drop_duplicates('UNIT_ID', keep='last')
    return base.drop_duplicates(subset=['PLANT_NAME', 'UNIT_NAME'], keep='last')


def _nameplate_scope_total(classified, ut_list):
    units = _filter_units_for_nameplate(classified, ut_list)
    if units.empty:
        return 0.0
    return round(float(units['U_CAPACITY_STD'].sum()), 1)


def _resolve_group_col(df, group_col):
    if group_col in df.columns:
        return group_col
    if group_col == 'Region':
        if 'jg_region' in df.columns:
            return 'jg_region'
        return next((c for c in ['WORLD_REG', 'WORLD_REGION'] if c in df.columns), None)
    if group_col == 'Country':
        return next((c for c in ['COUNTRY', 'P_COUNTRY'] if c in df.columns), None)
    return None


def _nameplate_by_group(classified, ut_list, group_col):
    units = _filter_units_for_nameplate(classified, ut_list)
    col = _resolve_group_col(units, group_col)
    if units.empty or not col:
        return {}
    return (
        units.groupby(col)['U_CAPACITY_STD']
        .sum()
        .round(1)
        .to_dict()
    )


def _installed_cap_val(row):
    val = pd.to_numeric(row.get('U_CAPACITY_STD'), errors='coerce')
    return round(float(val), 1) if pd.notna(val) else None


def _installed_col_for_row(nameplate_sum=None):
    if nameplate_sum is None:
        return {}
    return {INSTALLED_CAP_COL: round(float(nameplate_sum), 1)}


def build_forward_matrix(classified, config, report_date, group_col=None,
                         subgroup_col=None, aggregate_subgroup: bool = False):
    """Forward matrix in kbd (avg per month). Returns (df, month_labels).

    First column is the previous calendar month (all non-cancelled events that
    overlapped it). Remaining columns are forward months (Future/Ongoing only).
    Installed (kbd) is unit nameplate capacity; month columns are avg daily offline.
    """
    ps = report_date.to_period('M').to_timestamp()
    n = config.get('forward_months', 12)
    pe = ps + pd.DateOffset(months=n) - pd.Timedelta(days=1)
    ut_list = config.get('unit_types')

    prev_ps = ps - pd.DateOffset(months=1)
    prev_pe = prev_ps + pd.offsets.MonthEnd(0)
    prev_ml = f"{prev_ps.strftime('%b %y')} (prev)"
    prev_dim = days_in_month(prev_ps.year, prev_ps.month)

    months = pd.date_range(ps, periods=n, freq='MS')
    ml_fwd = [m.strftime('%b %y') for m in months]
    ml = [prev_ml] + ml_fwd
    dim_fwd = [days_in_month(m.year, m.month) for m in months]
    sort_col = ml_fwd[0] if ml_fwd else ml[0]

    base = classified[classified['CAP_OFFLINE_STD'].notna()].copy()
    if ut_list:
        uc = next((c for c in ['UNIT_TYPE', 'UTYPE_DESC'] if c in base.columns), None)
        if uc:
            base = base[base[uc].isin(ut_list)]

    not_cancelled = base['E_STATUS_RESEARCH'] != 'Cancelled'
    prev_overlap = (
        (base['START_DATE'] <= prev_pe) &
        (base['END_DATE'].fillna(prev_pe) >= prev_ps)
    )
    fwd_status = base['E_STATUS_RESEARCH'].isin(['Future', 'Ongoing'])
    fwd_overlap = (
        (base['START_DATE'] <= pe) &
        (base['END_DATE'].fillna(pe) >= ps)
    )
    candidates = base[(not_cancelled & prev_overlap) | (fwd_status & fwd_overlap)].copy()

    def prev_impact_kbd(start, end, cap):
        ed = end if pd.notna(end) else prev_pe
        overlap = max((min(ed, prev_pe) - max(start, prev_ps)).days + 1, 0)
        return round(float(cap) * overlap / prev_dim, 1) if overlap > 0 else 0.0

    def fwd_impact_kbd(start, end, cap):
        ed = end if pd.notna(end) else pe
        return [
            round(float(cap) * max((min(ed, ms + pd.offsets.MonthEnd(0)) - max(start, ms)).days + 1, 0) / dim_fwd[i], 1)
            for i, ms in enumerate(months)
        ]

    cc = next((c for c in ['COUNTRY', 'P_COUNTRY'] if c in candidates.columns), None)
    rows = []
    for _, r in candidates.iterrows():
        st = r['E_STATUS_RESEARCH']
        prev_v = prev_impact_kbd(r['START_DATE'], r['END_DATE'], r['CAP_OFFLINE_STD']) if st != 'Cancelled' else 0.0
        fwd_v = fwd_impact_kbd(r['START_DATE'], r['END_DATE'], r['CAP_OFFLINE_STD']) if st in ('Future', 'Ongoing') else [0.0] * n
        if prev_v == 0 and sum(fwd_v) == 0:
            continue
        rows.append({
            'Category': str(r.get('Category') or '—'),
            'Country': str(r[cc]) if cc else '—',
            'Refinery': str(r.get('PLANT_NAME', '')),
            'Parent': str(r.get('PARENTNAME', '') or ''),
            'Unit': str(r.get('UNIT_NAME', '')),
            'Unit Type': str(r.get('UNIT_TYPE', '')),
            'Region': str(r.get('jg_region', r.get('WORLD_REG', ''))),
            INSTALLED_CAP_COL: _installed_cap_val(r),
            prev_ml: prev_v,
            **dict(zip(ml_fwd, fwd_v)),
        })

    dm = pd.DataFrame(rows)
    if dm.empty:
        return dm, ml

    scope_nameplate = _nameplate_scope_total(classified, ut_list)
    group_nameplate = _nameplate_by_group(classified, ut_list, group_col or 'Region')

    if group_col and subgroup_col and group_col in dm.columns and subgroup_col in dm.columns:
        grand = dm[ml].sum().round(1)
        parts = [pd.DataFrame([{
            group_col: '▸ TOTAL', subgroup_col: '', 'Parent': '',
            **_installed_col_for_row(scope_nameplate),
            **grand.to_dict(),
        }])]
        for grp_val in sorted(dm[group_col].unique()):
            subset = dm[dm[group_col] == grp_val].sort_values(subgroup_col).reset_index(drop=True)
            sub = subset[ml].sum().round(1)
            grp_nameplate = group_nameplate.get(grp_val, 0.0)
            if aggregate_subgroup:
                detail = (
                    subset.groupby(subgroup_col)[ml + [INSTALLED_CAP_COL]].agg({
                        **{m: 'sum' for m in ml},
                        INSTALLED_CAP_COL: 'max',
                    }).round(1)
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
                detail = subset[[group_col, subgroup_col, 'Parent'] + MATRIX_CAP_COLS + ml].copy()
                label = f'▸ {grp_val} ({len(subset)} events)'
            parts.append(pd.DataFrame([{
                group_col: label, subgroup_col: '', 'Parent': '',
                **_installed_col_for_row(grp_nameplate),
                **sub.to_dict(),
            }]))
            cap_detail_cols = [group_col, subgroup_col, 'Parent'] + MATRIX_CAP_COLS + ml
            parts.append(detail[cap_detail_cols])
        return pd.concat(parts, ignore_index=True), ml

    if group_col and group_col in dm.columns:
        g = dm.groupby(group_col)[ml].sum().round(1)
        g[INSTALLED_CAP_COL] = g.index.map(lambda k: group_nameplate.get(k, 0.0))
        g = g.sort_values(sort_col, ascending=False)
        grand = g[ml].sum().round(1)
        grand[INSTALLED_CAP_COL] = scope_nameplate
        grand.name = 'TOTAL'
        g = pd.concat([pd.DataFrame([grand]), g])
        return g.reset_index(), ml

    grand = dm[ml].sum().round(1)
    total_row = pd.DataFrame([{
        'Category': '', 'Country': '', 'Refinery': '', 'Parent': '', 'Unit': '', 'Unit Type': '',
        'Region': '▸ TOTAL',
        **_installed_col_for_row(scope_nameplate),
        **grand.to_dict(),
    }])
    detail_parts = [total_row]
    for region in sorted(dm['Region'].unique()):
        rg = dm[dm['Region'] == region].sort_values(['Country', 'Refinery']).reset_index(drop=True)
        sub = rg[ml].sum().round(1)
        header = pd.DataFrame([{
            'Category': '', 'Country': '', 'Refinery': '', 'Parent': '', 'Unit': '', 'Unit Type': '',
            'Region': f'▸ {region} ({len(rg)} events)',
            **_installed_col_for_row(group_nameplate.get(region, 0.0)),
            **sub.to_dict(),
        }])
        detail_parts.append(header)
        detail_parts.append(rg)
    return pd.concat(detail_parts, ignore_index=True), ml
