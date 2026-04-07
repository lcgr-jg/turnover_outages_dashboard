"""
Data loading — Snowflake, unit conversion, geography mapping, refresh detection.
"""
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from _snowflake import SnowFlake
    
from config import (CONVERSION_FACTORS, TARGET_UOM, PADD_LABELS,
                    DEFAULT_UNIT_TYPES)


# ── Snowflake ──────────────────────────────────────────────────────────

def fetch_hist_events():
    """
    Fetch all events from Snowflake.
    Replace this with your actual connection logic.
    """
    # TODO: Replace with your Snowflake connection
    conn = SnowFlake(database='COMMOD_UAT', schema='IIR', role='FR_COMMOD_PM_EU')
    cur = conn._conn.cursor()
    cur.execute("SELECT * FROM COMMOD.IIR.OFFLINE_EVENTS")
    df = cur.fetch_pandas_all()
    
    return df
    r#aise NotImplementedError("Replace fetch_hist_events() with your Snowflake query")


def check_for_updates(last_loaded_ts):
    """
    Quick check: is there new data in Snowflake since last_loaded_ts?
    Returns (bool, latest_timestamp).
    """
    # TODO: Replace with your Snowflake connection
    cur.execute("SELECT MAX(TIME_STAMP) FROM COMMOD.IIR.OFFLINE_EVENTS")
    latest = cur.fetchone()[0]
    return latest > last_loaded_ts, latest
    #return False, last_loaded_ts


# ── Unit Conversion ────────────────────────────────────────────────────

def apply_conversion(df, cap_col, uom_col, out_cap, out_uom):
    if out_cap in df.columns and df[out_cap].notna().any():
        return df
    if cap_col not in df.columns or uom_col not in df.columns:
        return df
    cap_num = pd.to_numeric(df[cap_col], errors='coerce')
    factors = df[uom_col].map(CONVERSION_FACTORS)
    df[out_cap] = cap_num * factors
    unmapped = factors.isna() & cap_num.notna()
    df.loc[unmapped, out_cap] = cap_num[unmapped]
    df[out_uom] = df[uom_col].map(TARGET_UOM).fillna(df[uom_col])
    return df


# ── Geography Mapping ──────────────────────────────────────────────────

GEO_PATH = os.path.join(os.path.dirname(__file__), 'reference', 'asset_geographies.csv')

def apply_geography(df, geo_path=GEO_PATH):
    """Join custom region mapping. USA/Canada use PADD_REG."""
    try:
        geo = pd.read_csv(geo_path, encoding='utf-8-sig')
    except FileNotFoundError:
        print(f'  Geography file not found: {geo_path} — skipping')
        return df
    geo_country = (geo[['country', 'micro_region', 'region', 'macro_region']]
                   .drop_duplicates('country')
                   .rename(columns={'country': '_geo_country',
                                    'micro_region': 'jg_micro_region',
                                    'region': 'jg_region',
                                    'macro_region': 'jg_macro_region'}))
    cc = next((c for c in ['COUNTRY', 'P_COUNTRY'] if c in df.columns), None)
    if cc is None:
        return df
    df = df.merge(geo_country, left_on=cc, right_on='_geo_country', how='left')
    df.drop(columns=['_geo_country'], inplace=True, errors='ignore')
    for col in ['jg_micro_region', 'jg_region', 'jg_macro_region']:
        df[col] = df[col].fillna('Other')

    # USA & Canada: override with PADD_REG
    padd_countries = ['U.S.A.', 'United States', 'USA', 'Canada']
    if 'PADD_REG' in df.columns:
        padd_mask = df[cc].isin(padd_countries) & df['PADD_REG'].notna() & (df['PADD_REG'] != '')
        df.loc[padd_mask, 'jg_region'] = (df.loc[padd_mask, 'PADD_REG']
                                           .map(PADD_LABELS)
                                           .fillna(df.loc[padd_mask, 'PADD_REG']))
        df.loc[padd_mask, 'jg_micro_region'] = df.loc[padd_mask, 'PADD_REG']

    return df


# ── Main Load Pipeline ─────────────────────────────────────────────────

def load_and_prepare(geo_path=GEO_PATH):
    """Full data loading pipeline: Snowflake → conversion → geography."""
    df = fetch_hist_events()
    for c in ['TIME_STAMP', 'RELEASE_DT', 'LIVE_DATE', 'START_DATE', 'END_DATE',
              'PREV_START', 'PREV_END']:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors='coerce')
    uom = next((c for c in ['CAP_UOM', 'UOM'] if c in df.columns), None)
    if uom and 'CAP_OFFLINE' in df.columns:
        df = apply_conversion(df, 'CAP_OFFLINE', uom, 'CAP_OFFLINE_STD', 'CAP_UOM_STD')
    if uom and 'U_CAPACITY' in df.columns:
        df = apply_conversion(df, 'U_CAPACITY', uom, 'U_CAPACITY_STD', 'U_CAP_UOM_STD')
    df = apply_geography(df, geo_path)
    return df
