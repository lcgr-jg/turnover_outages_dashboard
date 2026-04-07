# IIR Refinery Maintenance Dashboard

## Setup

```bash
pip install -r requirements.txt
```

## Configuration

1. **Snowflake connection**: Edit `data.py` → `fetch_hist_events()` with your credentials.
2. **Geography mapping**: Place `asset_geographies.csv` in `../reference/` or update `GEO_PATH` in `data.py`.
3. **Default filters**: Edit `config.py` → `DEFAULT_UNIT_TYPES`.

## Run locally

```bash
python app.py
# Open http://127.0.0.1:8050
```

## Deploy to Dash Enterprise

The app is structured for Dash Enterprise deployment:
- `app.py` exposes `server` (Flask) for gunicorn
- No filesystem dependencies (Snowflake + in-memory)
- All config in `config.py`

## Project Structure

```
refinery_dashboard/
├── app.py              # Main Dash app — layout + callbacks
├── config.py           # Constants, defaults
├── data.py             # Snowflake connection, unit conversion, geography
├── classification.py   # Event classification + enrichment
├── charts.py           # Plotly chart builders
├── tables.py           # DataFrame table builders
├── analytics.py        # TA cycle, reliability, announcement gaps
├── requirements.txt
└── README.md
```

## Features

### Tab 1: Dashboard
- **Filter bar**: Unit types, regions (multi-select), event type, date picker
- **Seasonal charts**: Planned + Unplanned with historical band, median, individual year overlays
- **Daily ops chart**: Stacked area (Planned + Unplanned + Total line)
- **Change summary**: Impact delta by action type
- **Actions table**: Sorted by |Fwd Impact Δ|, hover for COMMENTS
- **Forward matrix**: 12-month detail with collapsible region grouping
- **Regional drill-down**: Select region to see its charts
- **HTML export**: Download current view as standalone HTML

### Tab 2: Analytics
- **TA cycle analysis**: Overdue units ranked by expected next TA
- **Reliability scoring**: Unplanned outage risk per refinery
- **Announcement gaps**: Units due for TA but not yet announced

### Data Refresh
- Auto-checks Snowflake every 5 minutes for new data
- Manual refresh button
- In-memory store shared across all users
