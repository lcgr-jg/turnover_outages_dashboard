"""
Shared configuration — constants, defaults, conversion factors.
"""

DEFAULT_UNIT_TYPES = ['Atmospheric Distillation', 'Condensate Splitter']

VGO_UNIT_TYPES = ['Vacuum Distillation', 'Vacuum (No Atmospheric)']

FCCU_UNIT_TYPES = ['FCCU (Fluid Catalytic Cracker)', 'RFCCU (Residue Fluid Catalytic Cracker)']

HYDROCRACKING_UNIT_TYPES = ['Distillate Hydrocracker', 'Residual Hydrocracker']

# Unit types whose source CAP_OFFLINE is NOT measured in BBL/d (i.e. they use
# Metric T/yr, M Lbs/yr, T/d, etc.).  data.apply_conversion() uses this set to
# decide whether a missing UOM can safely be defaulted to BBL/d: volumetric
# units (everything not in this set) get the BBL/d default; non-volumetric
# units are left with NaN CAP_OFFLINE_STD because we cannot safely guess
# between Metric T/yr, M Lbs/yr, etc.  Defaulting them to BBL/d would inflate
# values ~365x for Metric T/yr units, etc.  Add to this set if you discover
# new non-volumetric unit types that lack a UOM in the source data.
NON_VOLUMETRIC_UNIT_TYPES = frozenset({
    'Amine Treatment',
    'Benzene',
    'Hydrodealkylation',
    'Propylene Splitter',
    'Sulfur Recovery (SRU)',
})


# ── Email configuration ──
# ───────────────────────────────────────────────────────────────────────
RECIPIENTS = (
    'luiscarlos.gaitan@jainglobal.com, yves.Mindren@jainglobal.com, abderrahmane.mahoudi@jainglobal.com'
)  # comma-separated string (do not use # inside quotes — it starts a comment)
RECIPIENTS_CC = None  # e.g. 'teammate1@jainglobal.com,teammate2@jainglobal.com'
FROM_ADDRESS = 'oil.research.desk@jainglobal.com'

CONVERSION_FACTORS = {
    'BBL/d': 1 / 1_000, 'M Lbs/yr': 453.592 / 1_000 / 365,
    'Metric T/yr': 1 / 1_000 / 365, 'T/d': 1 / 1_000, 'MMSCFD': 1.0,
}
TARGET_UOM = {
    'BBL/d': 'kbd', 'M Lbs/yr': 'kT/d', 'Metric T/yr': 'kT/d',
    'T/d': 'kT/d', 'MMSCFD': 'MMSCFD',
}

PADD_LABELS = {
    'I': 'PADD I (East Coast)', 'II': 'PADD II (Midwest)',
    'III': 'PADD III (USGC)', 'IV': 'PADD IV (Rockies)',
    'V': 'PADD V (West Coast)',
}

MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
          'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

ACTION_COLORS = {
    'Cancelled': '#fce4ec', 'Completed': '#d1ecf1',
    'New': '#d4edda', 'Updated': '#fff3cd', 'Past': '#e8eaf6',
}

SUMMARY_FMT = {
    'Capacity (kbd)': '{:,.1f}',
    'Fwd Impact (kb)': '{:,.0f}',
    'Fwd Impact Δ (kb)': '{:+,.0f}',
    'Largest (kbd)': '{:,.1f}',
}


# ── Shared table styles ──
TABLE_STYLES = [
    {'selector':'table','props':[('border-collapse','collapse'),('width','100%'),('font-family','Calibri, sans-serif'),('font-size','12px')]},
    {'selector':'th','props':[('background-color','#1a1a2e'),('color','white'),('font-size','11px'),('padding','8px 10px'),('text-align','center'),('white-space','nowrap')]},
    {'selector':'td','props':[('padding','6px 10px'),('border-bottom','1px solid #ddd'),('vertical-align','top')]},
    {'selector':'caption','props':[('font-size','14px'),('font-weight','bold'),('text-align','left'),('padding','10px 0')]},
]

# Chart color palette
CLR_PLANNED = '#1a3a5c'
CLR_UNPLANNED = '#e07b39'
CLR_TOTAL = '#333333'
CLR_ESTIMATE = '#7b3f9e'
CLR_BAND = '#9e9e9e'
CLR_MEDIAN = '#9e9e9e'
