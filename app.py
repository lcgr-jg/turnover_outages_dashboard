"""
IIR Refinery Maintenance Dashboard — Dash App
==============================================
Run:  python app.py
Then open http://127.0.0.1:8050 in your browser.

For Dash Enterprise: deploy this directory as-is.
"""

import os
import json
from datetime import date, datetime, timedelta

import pandas as pd
import numpy as np
import dash
from dash import dcc, html, dash_table, callback, Input, Output, State, ctx
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

# ── Local modules ───────────────────────────────────────────────────────
from config import DEFAULT_UNIT_TYPES, ACTION_COLORS, MONTHS
from config import TABLE_STYLES, SUMMARY_FMT
from data import load_and_prepare, check_for_updates
from classification import classify_events, enrich_events
from charts import build_seasonal_chart, build_daily_ops_chart
from tables import build_actions_table, build_change_summary, build_forward_matrix
from analytics import build_ta_cycle_analysis, build_reliability_scores, find_ta_announcement_gaps
from core import get_latest_snapshot
from export import style_actions, style_matrix, fmt_kbd

# ═══════════════════════════════════════════════════════════════════════
# DATA STORE (in-memory, shared across all users)
# ═══════════════════════════════════════════════════════════════════════
class DataStore:
    """In-memory data store. All users share the same data."""
    def __init__(self):
        self.df_raw = None          # Raw Snowflake data (all snapshots)
        self.classified = None      # Deduplicated + classified
        self.last_loaded = None     # Timestamp of last load
        self.available_years = []
        self.available_unit_types = []
        self.available_regions = []
        self.available_event_types = []
        self.report_date = None
        self.prev_report_date = None

    def load(self, report_date=None, prev_report_date=None):
        """Full data load + classification + enrichment."""
        today = pd.Timestamp.today().normalize()
        if report_date is None:
            report_date = today
        if prev_report_date is None:
            # Previous working day
            prev = today - pd.Timedelta(days=1)
            if prev.weekday() >= 5:  # Saturday/Sunday
                prev = prev - pd.Timedelta(days=(prev.weekday() - 4))
            prev_report_date = prev

        self.report_date = pd.Timestamp(report_date)
        self.prev_report_date = pd.Timestamp(prev_report_date)

        print(f"Loading data: {self.report_date.date()} vs {self.prev_report_date.date()}")
        self.df_raw = load_and_prepare()
        self.classified = classify_events(self.df_raw, self.report_date, self.prev_report_date)
        self.classified = enrich_events(self.classified, self.report_date)
        self.last_loaded = datetime.now()

        # Extract available filter values
        ut_col = next((c for c in ['UNIT_TYPE', 'UTYPE_DESC'] if c in self.classified.columns), None)
        if ut_col:
            self.available_unit_types = sorted(self.classified[ut_col].dropna().unique().tolist())

        rgn_col = 'jg_region' if 'jg_region' in self.classified.columns else 'WORLD_REG'
        self.available_regions = sorted(self.classified[rgn_col].dropna().unique().tolist())

        et_col = next((c for c in ['EVENT_TYPE'] if c in self.classified.columns), None)
        if et_col:
            self.available_event_types = sorted(self.classified[et_col].dropna().unique().tolist())

        # Years present in data
        years = self.classified['START_DATE'].dropna().dt.year.unique()
        self.available_years = sorted([int(y) for y in years if 2010 <= y <= 2040])

        print(f"Loaded: {len(self.classified)} events, {len(self.available_unit_types)} unit types, "
              f"{len(self.available_regions)} regions, years {self.available_years[0]}-{self.available_years[-1]}")

    def filter(self, unit_types=None, regions=None, event_type=None):
        """Return filtered classified DataFrame."""
        df = self.classified.copy()
        if unit_types:
            ut_col = next((c for c in ['UNIT_TYPE', 'UTYPE_DESC'] if c in df.columns), None)
            if ut_col:
                df = df[df[ut_col].isin(unit_types)]
        if regions:
            rgn_col = 'jg_region' if 'jg_region' in df.columns else 'WORLD_REG'
            df = df[df[rgn_col].isin(regions)]
        return df

    def filter_for_chart(self, regions=None):
        """Return filtered data for charts (pre-filtered by region at DataFrame level)."""
        df = self.classified.copy()
        if regions:
            rgn_col = 'jg_region' if 'jg_region' in df.columns else 'WORLD_REG'
            df = df[df[rgn_col].isin(regions)]
        return df


# Global store
store = DataStore()


# ═══════════════════════════════════════════════════════════════════════
# APP INIT
# ═══════════════════════════════════════════════════════════════════════
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    suppress_callback_exceptions=True,
    title="IIR Refinery Maintenance",
)
server = app.server  # for Dash Enterprise / gunicorn


# ═══════════════════════════════════════════════════════════════════════
# LAYOUT COMPONENTS
# ═══════════════════════════════════════════════════════════════════════

def make_filter_bar():
    today = date.today()
    prev = today - timedelta(days=1)
    if prev.weekday() >= 5:
        prev = prev - timedelta(days=(prev.weekday() - 4))

    return dbc.Card(dbc.CardBody([
        dbc.Row([
            dbc.Col([
                html.Label("Unit Types", className="small fw-bold"),
                dcc.Dropdown(
                    id='filter-unit-types',
                    options=[], value=DEFAULT_UNIT_TYPES,
                    multi=True, placeholder="All unit types",
                ),
            ], md=3),
            dbc.Col([
                html.Label("Regions", className="small fw-bold"),
                dcc.Dropdown(
                    id='filter-regions',
                    options=[], value=[], multi=True,
                    placeholder="All regions",
                ),
            ], md=3),
            dbc.Col([
                html.Label("Event Type", className="small fw-bold"),
                dcc.Dropdown(
                    id='filter-event-type',
                    options=[{'label': 'All', 'value': 'All'},
                             {'label': 'Planned', 'value': 'Planned'},
                             {'label': 'Unplanned', 'value': 'Unplanned'}],
                    value='All', clearable=False,
                ),
            ], md=2),
            dbc.Col([
                html.Label("Report Date", className="small fw-bold"),
                dcc.DatePickerSingle(id='filter-report-date', date=today,
                                     display_format='DD MMM YYYY'),
            ], md=2),
            dbc.Col([
                html.Label("Compare vs", className="small fw-bold"),
                dcc.DatePickerSingle(id='filter-prev-date', date=prev,
                                     display_format='DD MMM YYYY'),
            ], md=2),
            dbc.Col([
                html.Label(" ", className="small"),
                dbc.Button("▶ Apply", id='btn-apply', color='primary',
                           size='sm', className="w-100 mt-1"),
            ], md=1),
        ], className="mb-2"),
        # ── Chart controls (second row) ──
        dbc.Row([
            dbc.Col([
                html.Label("Overlay Years", className="small fw-bold"),
                dcc.Dropdown(id='chart-show-years', options=[], value=[],
                             multi=True, placeholder="Select years to overlay"),
            ], md=3),
            dbc.Col([
                html.Label("Band Range", className="small fw-bold"),
                dcc.RangeSlider(id='chart-band-range', min=2015, max=2026,
                                value=[2021, 2025], marks=None,
                                tooltip={"placement": "bottom", "always_visible": True}),
            ], md=4),
            dbc.Col([
                html.Label("Regional Drill-Down", className="small fw-bold"),
                dcc.Dropdown(id='chart-region-select', options=[],
                             value=None, placeholder="Global (all regions)"),
            ], md=3),
            dbc.Col([
                html.Label(" ", className="small"),  # spacer
                dbc.Button("📥 Export HTML", id='btn-export', color='secondary',
                           size='sm', className="w-100"),
            ], md=2),
        ]),
    ]), className="mb-3")



def make_actions_table():
    """Dash DataTable for Recent Event Actions with hover tooltips."""
    return dash_table.DataTable(
        id='actions-table',
        columns=[],
        data=[],
        sort_action='native',
        sort_mode='single',
        page_size=25,
        style_table={'overflowX': 'auto'},
        style_header={
            'backgroundColor': '#1a1a2e', 'color': 'white',
            'fontWeight': 'bold', 'fontSize': '11px',
            'textAlign': 'center', 'whiteSpace': 'nowrap',
        },
        style_cell={
            'fontSize': '12px', 'padding': '6px 10px',
            'textAlign': 'left', 'whiteSpace': 'normal',
            'maxWidth': '250px', 'overflow': 'hidden',
        },
        style_cell_conditional=[
            {'if': {'column_id': 'Refinery'}, 'minWidth': '180px', 'maxWidth': '250px'},
            {'if': {'column_id': 'Change Detail'}, 'minWidth': '200px', 'maxWidth': '300px', 'fontSize': '11px'},
        ] + [
            {'if': {'column_id': c}, 'textAlign': 'right'}
            for c in ['Capacity (kbd)', 'Cap Δ (kbd)', 'Fwd Impact (kb)',
                       'Fwd Impact Δ (kb)', 'Util %']
        ],
        style_data_conditional=[
            {'if': {'filter_query': '{Action} = "New"'}, 'backgroundColor': ACTION_COLORS['New']},
            {'if': {'filter_query': '{Action} = "Updated"'}, 'backgroundColor': ACTION_COLORS['Updated']},
            {'if': {'filter_query': '{Action} = "Cancelled"'}, 'backgroundColor': ACTION_COLORS['Cancelled']},
            {'if': {'filter_query': '{Action} = "Completed"'}, 'backgroundColor': ACTION_COLORS['Completed']},
            {'if': {'filter_query': '{Action} = "Past"'}, 'backgroundColor': ACTION_COLORS['Past']},
        ],
        # Tooltip: show COMMENTS on hover
        tooltip_data=[],
        tooltip_duration=None,
        tooltip_delay=0,
    )


def make_summary_table():
    return dash_table.DataTable(
        id='summary-table',
        columns=[], data=[],
        style_table={'overflowX': 'auto'},
        style_header={
            'backgroundColor': '#1a1a2e', 'color': 'white',
            'fontWeight': 'bold', 'fontSize': '11px',
        },
        style_cell={'fontSize': '12px', 'padding': '6px 10px'},
        style_data_conditional=[
            {'if': {'filter_query': '{Action} = "TOTAL"'},
             'fontWeight': 'bold', 'backgroundColor': '#e9ecef'},
        ],
    )



# ═══════════════════════════════════════════════════════════════════════
# MAIN LAYOUT
# ═══════════════════════════════════════════════════════════════════════

app.layout = dbc.Container([
    # ── Header ──
    dbc.Row([
        dbc.Col([
            html.H4("IIR REFINERY MAINTENANCE DASHBOARD",
                     className="mb-0", style={'color': '#1a1a2e'}),
            html.Small(id='header-subtitle', className="text-muted"),
        ], md=8),
        dbc.Col([
            dbc.Button("🔄 Refresh Data", id='btn-refresh', color='primary', size='sm',
                       className="float-end mt-1"),
            html.Small(id='last-refreshed', className="float-end me-2 mt-2 text-muted"),
        ], md=4),
    ], className="mb-3 pt-3"),

    # ── Filter Bar ──
    make_filter_bar(),

    # ── Tabs ──
    dbc.Tabs([
        dbc.Tab(label="Dashboard", tab_id="tab-dashboard"),
        dbc.Tab(label="Analytics", tab_id="tab-analytics"),
    ], id="tabs", active_tab="tab-dashboard", className="mb-3"),

    # ── Tab Content ──
    html.Div(id="tab-content"),

    # ── Auto-refresh check (every 5 min) ──
    dcc.Interval(id='interval-check', interval=5 * 60 * 1000, n_intervals=0),

    # ── Hidden store for data freshness ──
    dcc.Store(id='data-version', data=0),

    # ── Download component for HTML export ──
    dcc.Download(id='download-html'),

], fluid=True)


# ═══════════════════════════════════════════════════════════════════════
# TAB LAYOUTS
# ═══════════════════════════════════════════════════════════════════════

def tab_dashboard():
    return html.Div([

        # Combined monthly (NEW)
        dbc.Card(dbc.CardBody([
            dcc.Graph(id='chart-combined-monthly'),
        ]), className="mb-3"),

        # Seasonal charts
        dbc.Row([
            dbc.Col(dcc.Graph(id='chart-planned'), md=6),
            dbc.Col(dcc.Graph(id='chart-unplanned'), md=6),
        ]),

        # Daily ops
        dbc.Card(dbc.CardBody([
            html.H6("Daily Offline Capacity — Near-Term View", className="mb-2"),
            dcc.Graph(id='chart-daily'),
        ]), className="mb-3"),

        # Change Summary
        dbc.Card(dbc.CardBody([
            html.H6("Change Summary", className="mb-2"),
            make_summary_table(),
        ]), className="mb-3"),

        # Recent Event Actions
        dbc.Card(dbc.CardBody([
            html.H6("Recent Event Actions", className="mb-1"),
            html.Div([
                html.Span("■ ", style={'color': ACTION_COLORS['New']}), "New  ",
                html.Span("■ ", style={'color': ACTION_COLORS['Updated']}), "Updated  ",
                html.Span("■ ", style={'color': ACTION_COLORS['Cancelled']}), "Cancelled  ",
                html.Span("■ ", style={'color': ACTION_COLORS['Completed']}), "Completed  ",
                html.Span("■ ", style={'color': ACTION_COLORS['Past']}), "Past",
            ], className="small text-muted mb-2"),
            make_actions_table(),
        ]), className="mb-3"),

        # Forward Matrix (collapsible by region)
        dbc.Card(dbc.CardBody([
            html.H6("12-Month Forward Offline Capacity (kbd)", className="mb-2"),
            html.Div(id='forward-matrix-container'),
        ]), className="mb-3"),

        # Regional drill-down charts
        html.Div(id='regional-charts-container'),

    ])


def tab_analytics():
    return html.Div([
        dbc.Card(dbc.CardBody([
            html.H6("Turnaround Cycle Analysis — Units Most Likely to Announce Next"),
            dash_table.DataTable(id='ta-cycle-table', columns=[], data=[],
                                sort_action='native', page_size=30,
                                style_header={'backgroundColor': '#1a1a2e', 'color': 'white',
                                              'fontWeight': 'bold', 'fontSize': '11px'},
                                style_cell={'fontSize': '12px', 'padding': '5px 8px'}),
        ]), className="mb-3"),

        dbc.Card(dbc.CardBody([
            html.H6("Refinery Reliability Scoring — Unplanned Outage Risk"),
            dash_table.DataTable(id='reliability-table', columns=[], data=[],
                                sort_action='native', page_size=30,
                                style_header={'backgroundColor': '#1a1a2e', 'color': 'white',
                                              'fontWeight': 'bold', 'fontSize': '11px'},
                                style_cell={'fontSize': '12px', 'padding': '5px 8px'}),
        ]), className="mb-3"),

        dbc.Card(dbc.CardBody([
            html.H6("TA Announcement Gaps — Expected but Not Yet Announced"),
            dash_table.DataTable(id='ta-gaps-table', columns=[], data=[],
                                sort_action='native', page_size=30,
                                style_header={'backgroundColor': '#1a1a2e', 'color': 'white',
                                              'fontWeight': 'bold', 'fontSize': '11px'},
                                style_cell={'fontSize': '12px', 'padding': '5px 8px'}),
        ]), className="mb-3"),
    ])


# ═══════════════════════════════════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════════════════════════════════

@callback(
    Output('tab-content', 'children'),
    Input('tabs', 'active_tab'),
)
def render_tab(active_tab):
    if active_tab == 'tab-dashboard':
        return tab_dashboard()
    elif active_tab == 'tab-analytics':
        return tab_analytics()
    return html.Div("Select a tab")


@callback(
    [Output('filter-unit-types', 'options'),
     Output('filter-unit-types', 'value'),      # ← ADD this
     Output('filter-regions', 'options'),
     Output('chart-show-years', 'options'),
     Output('chart-region-select', 'options'),
     Output('header-subtitle', 'children'),
     Output('last-refreshed', 'children'),
     Output('data-version', 'data')],
    [Input('btn-refresh', 'n_clicks'),
     Input('filter-report-date', 'date'),
     Input('filter-prev-date', 'date')],
    [State('filter-unit-types', 'value')],       # ← ADD: read current value
    prevent_initial_call=False,
)
def load_data(n_clicks, report_date, prev_date, current_ut_value):
    """Load/reload data from Snowflake."""
    store.load(report_date, prev_date)

    ut_options = [{'label': u, 'value': u} for u in store.available_unit_types]
    for ut in DEFAULT_UNIT_TYPES:
        if ut not in store.available_unit_types:
            ut_options.append({'label': ut, 'value': ut})

    # Keep current selection if user has changed it, otherwise use defaults
    if current_ut_value:
        ut_value = current_ut_value
    else:
        ut_value = DEFAULT_UNIT_TYPES

    rgn_options = [{'label': r, 'value': r} for r in store.available_regions]
    yr_options = [{'label': str(y), 'value': y} for y in store.available_years]

    subtitle = (f"Jain Global Research Desk | {store.report_date.strftime('%d %B %Y')} | "
                f"{len(store.classified):,} events")
    refreshed = f"Last refreshed: {store.last_loaded.strftime('%H:%M')}"
    version = (n_clicks or 0) + 1

    return ut_options, ut_value, rgn_options, yr_options, rgn_options, subtitle, refreshed, version


@callback(
    [Output('chart-planned', 'figure'),
     Output('chart-unplanned', 'figure'),
     Output('chart-combined-monthly', 'figure'),
     Output('chart-daily', 'figure')],
    [Input('data-version', 'data'),
     Input('btn-apply', 'n_clicks')],
    [State('filter-unit-types', 'value'),
     State('filter-regions', 'value'),
     State('chart-show-years', 'value'),
     State('chart-band-range', 'value'),
     State('chart-region-select', 'value')],
)
def update_charts(version, n_clicks, unit_types, regions, show_years, band_range, chart_region):
    if store.classified is None:
        empty = go.Figure()
        return empty, empty, empty, empty

    # Pre-filter by region if selected in drill-down
    if chart_region:
        df_chart = store.filter_for_chart(regions=[chart_region])
        chart_config_region = None  # already filtered
    elif regions:
        df_chart = store.filter_for_chart(regions=regions)
        chart_config_region = None
    else:
        df_chart = store.classified
        chart_config_region = None

    hist_years = list(range(band_range[0], band_range[1] + 1)) if band_range else []

    config_base = {
        'unit_types': unit_types or None,
        'region': chart_config_region,
        'country': None,
        'hist_years': hist_years,
        'exclude_years': [2020],
        'show_years': show_years or [],
    }

    fig_p = build_seasonal_chart(df_chart, {**config_base, 'event_type': 'Planned'},
                                  store.report_date)
    fig_u = build_seasonal_chart(df_chart, {**config_base, 'event_type': 'Unplanned'},
                                  store.report_date)
    fig_c = build_seasonal_chart(df_chart, {**config_base, 'event_type': None},
                                  store.report_date)
    fig_d = build_daily_ops_chart(df_chart, store.report_date,
                                   {'unit_types': unit_types or None, 'region': None})

    # Override titles if region selected
    if chart_region:
        fig_p.update_layout(title_text=f'{chart_region.upper()}<br><sub>PLANNED MAINTENANCE</sub>')
        fig_u.update_layout(title_text=f'{chart_region.upper()}<br><sub>UNPLANNED MAINTENANCE</sub>')
        fig_c.update_layout(title_text=f'{chart_region.upper()}<br><sub>MONTHLY OFFLINE CAPACITY</sub>')
        fig_d.update_layout(title_text=f'{chart_region} — Daily Offline Capacity')

    return fig_p, fig_u, fig_c, fig_d


@callback(
    [Output('summary-table', 'columns'),
     Output('summary-table', 'data'),
     Output('actions-table', 'columns'),
     Output('actions-table', 'data'),
     Output('actions-table', 'tooltip_data'),
    ],
    [Input('data-version', 'data'),
     Input('btn-apply', 'n_clicks')],
    [State('filter-unit-types', 'value'),
     State('filter-regions', 'value')],
)
def update_tables(version, n_clicks, unit_types, regions):
    if store.classified is None:
        return [], [], [], [], [], [], []

    df = store.filter(unit_types=unit_types, regions=regions or None)

    # ── Change Summary ──
    summary = build_change_summary(df)
    sum_cols = [{'name': c, 'id': c, 'type': 'numeric' if c != 'Action' and c != 'Largest Event' else 'text'}
                for c in summary.columns] if not summary.empty else []
    sum_data = summary.to_dict('records') if not summary.empty else []

    # ── Actions Table ──
    actions = build_actions_table(df)
    # Columns to display (hide Comments — it goes in tooltip)
    display_cols = [c for c in actions.columns if c != 'Comments']
    act_cols = [{'name': c, 'id': c} for c in display_cols]
    act_data = actions[display_cols].to_dict('records')

    # Tooltips: show Comments on hover
    tooltips = []
    for _, row in actions.iterrows():
        comment = row.get('Comments', '')
        if pd.notna(comment) and str(comment).strip():
            tooltips.append({c: {'value': str(comment), 'type': 'markdown'} for c in display_cols})
        else:
            tooltips.append({c: {'value': '', 'type': 'text'} for c in display_cols})


    return sum_cols, sum_data, act_cols, act_data, tooltips


@callback(
    Output('forward-matrix-container', 'children'),
    [Input('data-version', 'data'),
     Input('btn-apply', 'n_clicks')],
    [State('filter-unit-types', 'value'),
     State('filter-regions', 'value')],
)
def update_forward_matrix(version, n_clicks, unit_types, regions):
    if store.classified is None:
        return html.P("No data loaded.")

    df = store.filter(unit_types=unit_types, regions=regions or None)
    matrix_config = {'unit_types': unit_types or None, 'forward_months': 12}

    # ── Regional summary (one row per region) ──
    rgn_col = 'jg_region' if 'jg_region' in df.columns else 'Region'
    regional_df, ml = build_forward_matrix(df, matrix_config, store.report_date, group_col='Region')

    regional_table = html.Div()
    if not regional_df.empty:
        # Style: heatmap on numeric columns
        num_max = regional_df[ml].max().max() if not regional_df[ml].empty else 1
        heat_conditions = []
        for m in ml:
            for threshold in [0.25, 0.5, 0.75]:
                heat_conditions.append({
                    'if': {'column_id': m,
                           'filter_query': f'{{{m}}} > {num_max * threshold}'},
                    'backgroundColor': f'rgba(255, {int(200 - 150 * threshold)}, {int(200 - 200 * threshold)}, 0.3)',
                })

        idx_col = regional_df.columns[0]  # 'index' or region col name
        display_cols = [idx_col] + ml
        regional_table = html.Div([
            html.H6("Regional Summary (kbd)", className="mb-2",
                     style={'color': '#1a1a2e'}),
            dash_table.DataTable(
                columns=[{'name': c, 'id': c} for c in display_cols],
                data=regional_df[display_cols].to_dict('records'),
                sort_action='native',
                style_table={'overflowX': 'auto'},
                style_header={'backgroundColor': '#1a1a2e', 'color': 'white',
                              'fontWeight': 'bold', 'fontSize': '11px',
                              'textAlign': 'center', 'padding': '6px 8px'},
                style_cell={'fontSize': '12px', 'padding': '5px 8px',
                            'textAlign': 'center', 'whiteSpace': 'nowrap'},
                style_cell_conditional=[
                    {'if': {'column_id': idx_col}, 'textAlign': 'left',
                     'fontWeight': 'bold', 'minWidth': '120px'},
                ],
                style_data_conditional=[
                    {'if': {'filter_query': f'{{{idx_col}}} = "TOTAL"'},
                     'fontWeight': 'bold', 'backgroundColor': '#d5dbe0'},
                ] + heat_conditions,
            ),
        ], className="mb-4")

    # ── Detail matrix (collapsible by region) ──
    detail_df, ml2 = build_forward_matrix(df, matrix_config, store.report_date)
    if detail_df.empty:
        return html.Div([regional_table, html.P("No detail events.")])

    # Parse into region groups
    current_region = None
    region_rows = {}
    for _, row in detail_df.iterrows():
        rgn = str(row.get('Region', ''))
        if rgn.startswith('▸') and 'TOTAL' not in rgn:
            current_region = rgn.replace('▸ ', '')
            region_rows[current_region] = {'header': row, 'detail': []}
        elif current_region and not rgn.startswith('▸'):
            region_rows[current_region]['detail'].append(row)

    # Build accordion
    items = []
    for region_label, data in sorted(region_rows.items(),
                                      key=lambda x: -sum(x[1]['header'].get(m, 0)
                                                         for m in ml2
                                                         if isinstance(x[1]['header'].get(m, 0), (int, float)))):
        detail_list = data['detail']
        if not detail_list:
            continue
        detail = pd.DataFrame(detail_list)

        # Clean event count from label
        import re
        match = re.search(r'\((\d+) events?\)', region_label)
        n_events = match.group(1) if match else len(detail_list)
        clean_name = re.sub(r'\s*\(\d+ events?\)', '', region_label)

        # First-month total for the title
        first_nonzero = next(
            (f"{data['header'].get(m, 0):.1f} kbd" for m in ml2
             if isinstance(data['header'].get(m, 0), (int, float)) and data['header'].get(m, 0) > 0.05),
            '—'
        )

        detail_cols = ['Country', 'Refinery', 'Unit'] + ml2
        detail_cols = [c for c in detail_cols if c in detail.columns]

        detail_table = dash_table.DataTable(
            columns=[{'name': c, 'id': c} for c in detail_cols],
            data=detail[detail_cols].to_dict('records'),
            sort_action='native',
            page_size=25,
            style_table={'overflowX': 'auto'},
            style_header={'backgroundColor': '#1a1a2e', 'color': 'white',
                          'fontWeight': 'bold', 'fontSize': '10px',
                          'textAlign': 'center', 'padding': '4px 6px'},
            style_cell={'fontSize': '11px', 'padding': '4px 6px',
                        'whiteSpace': 'nowrap', 'textAlign': 'center'},
            style_cell_conditional=[
                {'if': {'column_id': 'Refinery'}, 'minWidth': '150px', 'maxWidth': '200px',
                 'whiteSpace': 'normal', 'textAlign': 'left'},
                {'if': {'column_id': 'Country'}, 'textAlign': 'left', 'minWidth': '80px'},
                {'if': {'column_id': 'Unit'}, 'textAlign': 'left', 'minWidth': '120px'},
            ],
        )

        items.append(dbc.AccordionItem(
            detail_table,
            title=f"{clean_name}  ({n_events} events,  {ml2[0]}: {first_nonzero})",
        ))

    accordion = dbc.Accordion(items, start_collapsed=True, flush=True)

    return html.Div([
        regional_table,
        html.H6("Unit Detail — click region to expand", className="mb-2",
                 style={'color': '#1a1a2e'}),
        accordion,
    ])



@callback(
    [Output('ta-cycle-table', 'columns'),
     Output('ta-cycle-table', 'data'),
     Output('reliability-table', 'columns'),
     Output('reliability-table', 'data'),
     Output('ta-gaps-table', 'columns'),
     Output('ta-gaps-table', 'data')],
    Input('tabs', 'active_tab'),
    State('filter-unit-types', 'value'),
)
def update_analytics(active_tab, unit_types):
    """Only compute analytics when the tab is selected (lazy loading)."""
    if active_tab != 'tab-analytics' or store.df_raw is None:
        return [], [], [], [], [], []

    ut = unit_types or DEFAULT_UNIT_TYPES

    # TA Cycles
    ta = build_ta_cycle_analysis(store.df_raw, store.report_date, unit_types=ut)
    ta_display = ta[['PLANT_NAME', 'COUNTRY', 'UNIT_NAME', 'U_CAPACITY_STD',
                      'n_tas', 'avg_interval_months', 'days_since_last_ta',
                      'days_until_expected', 'overdue']].head(30).copy() if not ta.empty else pd.DataFrame()
    ta_display = ta_display.rename(columns={
        'PLANT_NAME': 'Refinery', 'COUNTRY': 'Country', 'UNIT_NAME': 'Unit',
        'U_CAPACITY_STD': 'Capacity (kbd)', 'n_tas': 'TAs',
        'avg_interval_months': 'Avg Cycle (mo)', 'days_since_last_ta': 'Days Since',
        'days_until_expected': 'Days Until', 'overdue': 'Overdue',
    })

    # Reliability
    rel = build_reliability_scores(store.df_raw, store.report_date, unit_types=ut)
    rel_display = rel[['PLANT_NAME', 'COUNTRY', 'plant_capacity', 'unplanned_n_events',
                        'events_per_year', 'unplanned_event_ratio',
                        'unplanned_avg_cap_offline', 'risk_score']].head(30).copy() if not rel.empty else pd.DataFrame()
    rel_display = rel_display.rename(columns={
        'PLANT_NAME': 'Refinery', 'COUNTRY': 'Country', 'plant_capacity': 'Capacity (kbd)',
        'unplanned_n_events': 'Unplanned', 'events_per_year': 'Per Year',
        'unplanned_event_ratio': 'Unplanned %', 'unplanned_avg_cap_offline': 'Avg Offline (kbd)',
        'risk_score': 'Risk Score',
    })

    # Gaps
    gaps = find_ta_announcement_gaps(ta, store.classified, store.report_date) if not ta.empty else pd.DataFrame()
    gaps_display = gaps[['PLANT_NAME', 'COUNTRY', 'UNIT_NAME', 'U_CAPACITY_STD',
                          'avg_interval_months', 'days_since_last_ta',
                          'overdue_by_days']].head(30).copy() if not gaps.empty else pd.DataFrame()
    gaps_display = gaps_display.rename(columns={
        'PLANT_NAME': 'Refinery', 'COUNTRY': 'Country', 'UNIT_NAME': 'Unit',
        'U_CAPACITY_STD': 'Capacity (kbd)', 'avg_interval_months': 'Avg Cycle (mo)',
        'days_since_last_ta': 'Days Since', 'overdue_by_days': 'Overdue By (d)',
    })

    def to_table(df):
        if df.empty: return [], []
        cols = [{'name': c, 'id': c} for c in df.columns]
        data = df.round(1).to_dict('records')
        return cols, data

    ta_c, ta_d = to_table(ta_display)
    rel_c, rel_d = to_table(rel_display)
    gap_c, gap_d = to_table(gaps_display)

    return ta_c, ta_d, rel_c, rel_d, gap_c, gap_d


@callback(
    Output('download-html', 'data'),
    Input('btn-export', 'n_clicks'),
    [State('filter-unit-types', 'value'),
     State('filter-regions', 'value'),
     State('chart-show-years', 'value'),
     State('chart-band-range', 'value'),
     State('chart-region-select', 'value')],
    prevent_initial_call=True,
)
def export_html(n_clicks, unit_types, regions, show_years, band_range, chart_region):
    """Export current dashboard state as standalone HTML — same format as notebook."""
    from plotly.io import to_html
    import re

    df = store.filter(unit_types=unit_types, regions=regions or None)
    if chart_region:
        rgn_col = 'jg_region' if 'jg_region' in df.columns else 'WORLD_REG'
        df = df[df[rgn_col] == chart_region]

    hist_years = list(range(band_range[0], band_range[1] + 1)) if band_range else list(range(2021, 2026))
    config_base = {
        'unit_types': unit_types or None,
        'region': None, 'country': None,
        'hist_years': hist_years,
        'exclude_years': [2020],
        'show_years': show_years or [],
    }

    # ── Build all charts ──
    fig_planned = build_seasonal_chart(df, {**config_base, 'event_type': 'Planned'}, store.report_date)
    fig_unplanned = build_seasonal_chart(df, {**config_base, 'event_type': 'Unplanned'}, store.report_date)
    fig_combined = build_seasonal_chart(df, {**config_base, 'event_type': None}, store.report_date)
    fig_daily = build_daily_ops_chart(df, store.report_date,
                                       {'unit_types': unit_types or None, 'region': None})

    chart1 = to_html(fig_planned, full_html=False, include_plotlyjs='cdn')
    chart2 = to_html(fig_unplanned, full_html=False, include_plotlyjs=False)
    chart_comb = to_html(fig_combined, full_html=False, include_plotlyjs=False)
    chart_daily = to_html(fig_daily, full_html=False, include_plotlyjs=False)

    # ── Build all tables ──
    summary_df = build_change_summary(df)
    actions_df = build_actions_table(df)
    matrix_config = {'unit_types': unit_types or None, 'forward_months': 12}
    #regional_df, ml = build_forward_matrix(df, matrix_config, store.report_date, group_col='Region')
    detail_df, ml2 = build_forward_matrix(df, matrix_config, store.report_date)

    # ── Style tables as HTML ──
    # (Paste your notebook's style_actions, style_matrix, etc. here
    #  or use the simpler pandas .to_html() approach below)

    actions_html = style_actions(actions_df, unit_types, store.report_date).to_html(index=False, na_rep='—', classes='styled-table') if not actions_df.empty else ''
    #summary_html = summary_df.to_html(index=False, na_rep='—', classes='styled-table') if not summary_df.empty else ''
    #regional_html = regional_df.to_html(index=False, na_rep='—', classes='styled-table') if not regional_df.empty else ''

    summary_html = (summary_df.style.format(SUMMARY_FMT, na_rep='—')
    .set_table_styles(TABLE_STYLES)
    .set_caption(f'Change Summary — {store.report_date.strftime("%d %b %Y")} vs {store.prev_report_date.strftime("%d %b %Y")}')
    .hide(axis='index').to_html()) if not summary_df.empty else ''

    # ── Collapsible detail matrix (same JS as notebook) ──
    # You can paste your matrix_to_collapsible_html() function here
    # and call it: detail_html = matrix_to_collapsible_html(detail_df, ml2, '...')

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

    detail_html = matrix_to_collapsible_html(detail_df, ml2, f'')
    
    #detail_html = detail_df.to_html(index=False, na_rep='—', classes='styled-table') if not detail_df.empty else ''

    # ── Assemble HTML (paste your notebook template here) ──
    REPORT_YEAR = store.report_date.year
    dt = store.report_date.strftime('%d %B %Y')
    ut_label = ' & '.join(unit_types) if unit_types else 'All'

    # This is where you paste the full HTML template from your notebook Cell 35
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Refinery Maintenance Report — {REPORT_YEAR}</title>
  <style>
    body {{ font-family: Calibri, Arial, sans-serif; margin: 30px; background: #f9f9f9; color: #1a1a2e; }}
    h1 {{ font-size: 18px; margin-bottom: 4px; }}
    h2 {{ font-size: 13px; color: #555; font-weight: normal; margin-top: 0; }}
    h3 {{ font-size: 14px; color: #1a1a2e; margin: 24px 0 8px 0; }}
    .charts {{ display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 30px; }}
    .chart {{ flex: 1; min-width: 400px; background: white; border-radius: 6px;
              box-shadow: 0 1px 4px rgba(0,0,0,0.1); padding: 10px; }}
    .tw {{ background: white; border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,0.1);
           padding: 20px; overflow-x: auto; margin-bottom: 24px; }}
    .legend {{ font-size: 12px; margin-bottom: 12px; }}
    .legend span {{ display: inline-block; width: 14px; height: 14px;
                    border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
    .styled-table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
    .styled-table th {{ background: #1a1a2e; color: white; padding: 8px 10px; text-align: center; font-size: 11px; }}
    .styled-table td {{ padding: 6px 10px; border-bottom: 1px solid #ddd; }}
    .region-header:hover {{ background-color: #dde3e8 !important; }}
  </style>
</head>
<body>
  <h1>DAILY IIR REPORTED MAINTENANCE ACTIVITY</h1>
  <h2>Jain Global Research Desk &nbsp;|&nbsp; {dt} &nbsp;|&nbsp; {ut_label}</h2>

  <div class="tw">
    <h3>Monthly Offline Capacity (Planned + Unplanned)</h3>
    {chart_comb}
  </div>

  <div class="charts">
    <div class="chart">{chart1}</div>
    <div class="chart">{chart2}</div>
  </div>

  <div class="tw">
    <h3>Daily Offline Capacity — Near-Term View</h3>
    {chart_daily}
  </div>

  <div class="tw">
    <h3>Change Summary</h3>
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
    <h3>12-Month Forward Offline Capacity (kbd) | Planned & Unplanned</h3>
    {detail_html}
  </div>

    {collapse_js}
</body></html>"""

    return dict(
        content=html_content,
        filename=f"refinery_report_{store.report_date.strftime('%Y%m%d')}.html"
    )


# ═══════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    # Initial load
    try:
        store.load()
    except NotImplementedError:
        print("⚠ Replace fetch_hist_events() in data.py with your Snowflake connection.")
        print("  The app will start but show empty data until you connect.")

    app.run(debug=True, host='0.0.0.0', port=8050)
