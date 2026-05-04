"""
AART — UI Components & Global Styling
======================================

Reusable Streamlit UI helpers for the 2026 enterprise design system,
dark theme only.

Public API
----------
apply_global_styles()
    Injects the global CSS design tokens, component styles, and light
    animations. Must be called once, at the top of ``main()`` (after
    ``st.set_page_config``). Always renders in dark mode.

render_app_header(title, subtitle=None, week=None, total_das=None,
                  data_loaded=False, breadcrumb=None)
    Full-width glassmorphism hero banner with animated gradient
    background, title, status pill, and optional breadcrumb.

render_kpi_card(title, value, delta=None, color='blue', icon='📊',
                caption=None)
    Single KPI card (use inside an ``st.columns`` layout). Returns a
    block of HTML to drop into ``st.markdown(..., unsafe_allow_html=True)``.

render_kpi_grid(cards)
    Convenience: renders a list of KPI card dicts as a responsive grid.

render_day_card(day, das_working, gap, demand)
    Horizontal-strip day card for the weekly breakdown.

render_day_strip(day_records)
    Renders a horizontally-scrollable strip of day cards.

render_status_badge(status, text)
    Inline status pill (``'success' | 'warning' | 'danger' | 'info' |
    'neutral'``).

render_empty_state(icon, title, description, cta_label=None)
    Dashed-border empty-state block.

render_section_header(icon, title, description=None)
    Styled sub-section header used throughout the app.

Theme
-----
Dark-only design. The brand accent (Amazon orange) is the primary
action colour; all surfaces use dark navy/slate tones. The sidebar
and hero banner share the same dark-navy palette for visual cohesion.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

import streamlit as st


# ---------------------------------------------------------------------------
# Global CSS — design tokens + component styles
# ---------------------------------------------------------------------------

_GLOBAL_CSS = """
<style>
/* =========================================================================
   AART Design Tokens — Dark Only
   ========================================================================= */
:root {
    /* Brand Palette */
    --brand-primary: #FF9900;
    --brand-primary-dark: #E88B00;
    --brand-primary-light: #FFB84D;
    --brand-secondary: #232F3E;
    --brand-accent: #00A8E1;

    /* Semantic */
    --success: #10B981;
    --warning: #F59E0B;
    --danger:  #EF4444;
    --info:    #3B82F6;
    --purple:  #8B5CF6;

    /* Typography */
    --font-sans: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
    --font-mono: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;

    /* Radii */
    --radius-sm: 6px;
    --radius-md: 12px;
    --radius-lg: 20px;
    --radius-xl: 28px;

    /* --- Dark theme tokens (always active) --- */
    --app-bg:        #0B1220;
    --app-bg-end:    #0F172A;
    --surface:       #1E293B;
    --surface-alt:   #172032;
    --surface-hover: #243247;
    --border:        #334155;
    --border-strong: #475569;
    --divider:       rgba(255,255,255,0.08);

    --text-primary:   #F1F5F9;
    --text-secondary: #CBD5E1;
    --text-muted:     #94A3B8;
    --text-inverse:   #0F172A;

    /* Soft tint backgrounds for badges */
    --tint-success-bg: rgba(16,185,129,0.18);  --tint-success-fg: #6EE7B7;
    --tint-warning-bg: rgba(245,158,11,0.18);  --tint-warning-fg: #FCD34D;
    --tint-danger-bg:  rgba(239,68,68,0.18);   --tint-danger-fg:  #FCA5A5;
    --tint-info-bg:    rgba(59,130,246,0.18);  --tint-info-fg:    #93C5FD;
    --tint-neutral-bg: rgba(148,163,184,0.16); --tint-neutral-fg: #CBD5E1;

    /* Shadows */
    --shadow-card:     0 4px 24px rgba(0,0,0,0.35), 0 1px 4px rgba(0,0,0,0.25);
    --shadow-elevated: 0 14px 44px rgba(0,0,0,0.55);

    /* Glass */
    --glass-bg:     rgba(255,255,255,0.06);
    --glass-border: rgba(255,255,255,0.10);

    /* Spark placeholder gradient */
    --spark-a: #1E293B;
    --spark-b: #334155;

    /* Button palette */
    --btn-primary-bg:    linear-gradient(135deg, #FF9900 0%, #E88B00 100%);
    --btn-primary-text:  #FFFFFF;
    --btn-primary-shadow: 0 2px 8px rgba(255,153,0,0.35);
    --btn-primary-hover-shadow: 0 4px 16px rgba(255,153,0,0.50);

    --btn-secondary-bg:    rgba(255,255,255,0.06);
    --btn-secondary-text:  #CBD5E1;
    --btn-secondary-border: rgba(255,255,255,0.14);
    --btn-secondary-hover-bg: rgba(255,153,0,0.10);
    --btn-secondary-hover-text: #FF9900;
    --btn-secondary-hover-border: #FF9900;
}

/* =========================================================================
   Base typography
   ========================================================================= */
html, body, [class*="css"] {
    font-family: var(--font-sans) !important;
}

.stApp {
    background: linear-gradient(180deg, var(--app-bg) 0%, var(--app-bg-end) 100%) !important;
    color: var(--text-primary);
}

/* Force Streamlit's own chrome to match our dark theme */
[data-testid="stAppViewContainer"],
[data-testid="stHeader"],
[data-testid="stToolbar"] {
    background: transparent !important;
}
header[data-testid="stHeader"] {
    background: rgba(11,18,32,0.85) !important;
    backdrop-filter: blur(12px) !important;
    border-bottom: 1px solid var(--divider) !important;
}

/* Make sure Streamlit's default text colors follow theme */
.stApp p, .stApp span, .stApp label, .stApp li,
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6 {
    color: var(--text-primary);
}
.stApp small, .stCaption, [data-testid="stCaptionContainer"] {
    color: var(--text-muted) !important;
}

/* =========================================================================
   App Header (hero banner)
   The hero is always dark-navy — brand choice, same in both themes.
   ========================================================================= */
.aart-hero {
    position: relative;
    padding: 22px 28px;
    margin: -2rem -1rem 1.5rem -1rem;
    border-radius: 0 0 var(--radius-xl) var(--radius-xl);
    background: linear-gradient(120deg,
        var(--brand-secondary) 0%,
        #2C3E50 40%,
        #1A2332 100%);
    background-size: 200% 200%;
    animation: aart-hero-shimmer 18s ease infinite;
    color: #FFFFFF;
    box-shadow: var(--shadow-elevated);
    overflow: hidden;
}
.aart-hero, .aart-hero * { color: #FFFFFF; }
.aart-hero::before {
    content: "";
    position: absolute; inset: 0;
    background: radial-gradient(circle at 85% 30%,
        rgba(255,153,0,0.25) 0%, transparent 50%),
                radial-gradient(circle at 15% 80%,
        rgba(0,168,225,0.18) 0%, transparent 55%);
    pointer-events: none;
}
@keyframes aart-hero-shimmer {
    0%, 100% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
}
.aart-hero-inner {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    flex-wrap: wrap;
}
.aart-hero-title {
    font-size: 1.85rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin: 0;
    background: linear-gradient(92deg, #FFFFFF 0%, #FFD199 55%, var(--brand-primary) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.aart-hero-subtitle {
    color: rgba(255,255,255,0.74) !important;
    font-size: 0.88rem;
    margin-top: 4px;
}
.aart-hero-pills {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
}
.aart-pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 14px;
    border-radius: 999px;
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    font-size: 0.78rem;
    font-weight: 500;
    color: #FFFFFF;
    backdrop-filter: blur(8px);
}
.aart-pill strong { font-weight: 700; color: #FFFFFF; }
.aart-pill .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--success);
    box-shadow: 0 0 0 0 rgba(16,185,129, 0.7);
    animation: aart-pulse 2.2s infinite;
}
.aart-pill.status-off .dot { background: #94A3B8; animation: none; }
@keyframes aart-pulse {
    0%   { box-shadow: 0 0 0 0 rgba(16,185,129,0.6); }
    70%  { box-shadow: 0 0 0 10px rgba(16,185,129,0); }
    100% { box-shadow: 0 0 0 0 rgba(16,185,129,0); }
}

.aart-breadcrumb {
    margin-top: 12px;
    font-size: 0.78rem;
    color: rgba(255,255,255,0.65);
}
.aart-breadcrumb span.sep { opacity: 0.45; padding: 0 6px; }
.aart-breadcrumb span.active { color: var(--brand-primary-light); font-weight: 600; }

/* =========================================================================
   KPI Cards
   ========================================================================= */
.aart-kpi {
    position: relative;
    padding: 18px 20px 16px 22px;
    background: var(--surface);
    border-radius: var(--radius-md);
    box-shadow: var(--shadow-card);
    border-left: 4px solid var(--border-strong);
    border-top: 1px solid var(--border);
    border-right: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    color: var(--text-primary);
    transition: transform 0.18s ease, box-shadow 0.18s ease;
    min-height: 108px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
}
.aart-kpi:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-elevated);
}
.aart-kpi.red    { border-left-color: var(--danger); }
.aart-kpi.green  { border-left-color: var(--success); }
.aart-kpi.blue   { border-left-color: var(--info); }
.aart-kpi.orange { border-left-color: var(--brand-primary); }
.aart-kpi.purple { border-left-color: var(--purple); }
.aart-kpi.amber  { border-left-color: var(--warning); }

.aart-kpi-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 4px;
}
.aart-kpi-title {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-muted);
}
.aart-kpi-icon { font-size: 1.1rem; opacity: 0.9; }

.aart-kpi-value {
    font-size: 1.85rem;
    font-weight: 700;
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.02em;
    line-height: 1.05;
}
.aart-kpi-delta {
    font-size: 0.78rem;
    font-weight: 600;
    margin-top: 2px;
}
.aart-kpi-delta.up   { color: var(--success); }
.aart-kpi-delta.down { color: var(--danger); }
.aart-kpi-delta.flat { color: var(--text-muted); }
.aart-kpi-caption {
    font-size: 0.72rem;
    color: var(--text-muted);
    margin-top: 2px;
}
.aart-spark {
    height: 14px;
    margin-top: 8px;
    background: linear-gradient(90deg,
        var(--spark-a) 0%, var(--spark-b) 50%, var(--spark-a) 100%);
    border-radius: 4px;
    opacity: 0.65;
}

/* =========================================================================
   Day strip (horizontal scroll)
   ========================================================================= */
.aart-day-strip {
    display: flex;
    gap: 12px;
    overflow-x: auto;
    padding: 4px 2px 14px 2px;
    scrollbar-width: thin;
}
.aart-day-strip::-webkit-scrollbar { height: 8px; }
.aart-day-strip::-webkit-scrollbar-thumb {
    background: var(--border-strong); border-radius: 4px;
}
.aart-day-card {
    flex: 0 0 160px;
    background: var(--surface);
    color: var(--text-primary);
    border-radius: var(--radius-md);
    padding: 14px 14px 12px 14px;
    box-shadow: var(--shadow-card);
    border: 1px solid var(--border);
    border-top: 3px solid var(--success);
    transition: transform 0.15s ease;
}
.aart-day-card:hover { transform: translateY(-2px); }
.aart-day-card.amber { border-top-color: var(--warning); }
.aart-day-card.red   { border-top-color: var(--danger); }
.aart-day-header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 8px;
}
.aart-day-name {
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-secondary);
}
.aart-day-gap {
    font-size: 0.72rem;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 999px;
    background: var(--tint-neutral-bg);
    color: var(--tint-neutral-fg);
}
.aart-day-gap.amber { background: var(--tint-warning-bg); color: var(--tint-warning-fg); }
.aart-day-gap.red   { background: var(--tint-danger-bg);  color: var(--tint-danger-fg); }
.aart-day-gap.green { background: var(--tint-success-bg); color: var(--tint-success-fg); }

.aart-day-count {
    font-size: 1.55rem;
    font-weight: 700;
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
}
.aart-day-count-sub {
    font-size: 0.72rem;
    color: var(--text-muted);
    margin-top: -2px;
}
.aart-day-ratio {
    margin-top: 8px;
    height: 6px;
    border-radius: 3px;
    background: var(--border);
    overflow: hidden;
}
.aart-day-ratio > div {
    height: 100%;
    background: linear-gradient(90deg,
        var(--brand-primary) 0%, var(--brand-primary-light) 100%);
    border-radius: 3px;
    transition: width 0.3s ease;
}

/* =========================================================================
   Status badges
   ========================================================================= */
.aart-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.74rem;
    font-weight: 600;
    letter-spacing: 0.01em;
}
.aart-badge .dot {
    width: 6px; height: 6px; border-radius: 50%; background: currentColor;
}
.aart-badge.success { background: var(--tint-success-bg); color: var(--tint-success-fg); }
.aart-badge.warning { background: var(--tint-warning-bg); color: var(--tint-warning-fg); }
.aart-badge.danger  { background: var(--tint-danger-bg);  color: var(--tint-danger-fg); }
.aart-badge.info    { background: var(--tint-info-bg);    color: var(--tint-info-fg); }
.aart-badge.neutral { background: var(--tint-neutral-bg); color: var(--tint-neutral-fg); }

/* =========================================================================
   Section headers
   ========================================================================= */
.aart-section-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 24px 0 12px 0;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--divider);
}
.aart-section-icon {
    font-size: 1.2rem;
    background: linear-gradient(135deg, var(--brand-primary) 0%, var(--brand-primary-dark) 100%);
    width: 32px; height: 32px;
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    color: #FFFFFF;
    box-shadow: 0 2px 8px rgba(255,153,0,0.3);
}
.aart-section-title {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--text-primary);
    letter-spacing: -0.01em;
}
.aart-section-desc {
    font-size: 0.82rem;
    color: var(--text-muted);
    margin-top: 1px;
}

/* =========================================================================
   Empty state
   ========================================================================= */
.aart-empty {
    border: 2px dashed var(--border-strong);
    border-radius: var(--radius-lg);
    padding: 36px 28px;
    text-align: center;
    background: var(--surface);
    color: var(--text-secondary);
}
.aart-empty-icon { font-size: 2.4rem; margin-bottom: 10px; }
.aart-empty-title {
    font-size: 1.05rem; font-weight: 700; color: var(--text-primary);
    margin-bottom: 6px;
}
.aart-empty-desc { font-size: 0.88rem; color: var(--text-muted); }

/* =========================================================================
   Streamlit widget overrides — dark theme
   ========================================================================= */

/* ---- ALL buttons base reset (catches every Streamlit button) ---- */
.stButton > button,
.stDownloadButton > button,
.stFormSubmitButton > button {
    border-radius: var(--radius-sm) !important;
    font-weight: 600 !important;
    font-family: var(--font-sans) !important;
    cursor: pointer !important;
    transition: all 0.15s ease !important;
    letter-spacing: 0.01em !important;
}

/* ---- Primary buttons (orange gradient) ---- */
.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"],
.stFormSubmitButton > button[kind="primary"] {
    background: var(--btn-primary-bg) !important;
    color: var(--btn-primary-text) !important;
    border: none !important;
    box-shadow: var(--btn-primary-shadow) !important;
}
.stButton > button[kind="primary"]:hover,
.stDownloadButton > button[kind="primary"]:hover,
.stFormSubmitButton > button[kind="primary"]:hover {
    transform: translateY(-1px) scale(1.02) !important;
    box-shadow: var(--btn-primary-hover-shadow) !important;
    filter: brightness(1.08) !important;
    color: var(--btn-primary-text) !important;
}
.stButton > button[kind="primary"]:active,
.stDownloadButton > button[kind="primary"]:active,
.stFormSubmitButton > button[kind="primary"]:active {
    transform: translateY(0) scale(0.99) !important;
    box-shadow: 0 1px 4px rgba(255,153,0,0.25) !important;
}

/* ---- Secondary / default buttons (glass on dark) ---- */
.stButton > button:not([kind="primary"]),
.stDownloadButton > button:not([kind="primary"]),
.stFormSubmitButton > button:not([kind="primary"]) {
    background: var(--btn-secondary-bg) !important;
    color: var(--btn-secondary-text) !important;
    border: 1px solid var(--btn-secondary-border) !important;
    box-shadow: none !important;
}
.stButton > button:not([kind="primary"]):hover,
.stDownloadButton > button:not([kind="primary"]):hover,
.stFormSubmitButton > button:not([kind="primary"]):hover {
    background: var(--btn-secondary-hover-bg) !important;
    color: var(--btn-secondary-hover-text) !important;
    border-color: var(--btn-secondary-hover-border) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 2px 8px rgba(255,153,0,0.15) !important;
}
.stButton > button:not([kind="primary"]):active,
.stDownloadButton > button:not([kind="primary"]):active,
.stFormSubmitButton > button:not([kind="primary"]):active {
    transform: translateY(0) !important;
    background: rgba(255,153,0,0.16) !important;
}

/* ---- Disabled buttons ---- */
.stButton > button:disabled,
.stDownloadButton > button:disabled,
.stFormSubmitButton > button:disabled {
    opacity: 0.38 !important;
    cursor: not-allowed !important;
    transform: none !important;
    box-shadow: none !important;
    filter: none !important;
}

/* ---- Link buttons ---- */
.stLinkButton > a {
    color: var(--brand-primary) !important;
    border: 1px solid var(--btn-secondary-border) !important;
    border-radius: var(--radius-sm) !important;
    background: var(--btn-secondary-bg) !important;
    font-weight: 600 !important;
    transition: all 0.15s ease !important;
}
.stLinkButton > a:hover {
    background: var(--btn-secondary-hover-bg) !important;
    border-color: var(--brand-primary) !important;
    color: var(--brand-primary-light) !important;
}

/* Tabs — dark theme */
.stTabs [data-baseweb="tab-list"] {
    gap: 6px !important;
    background: rgba(255,255,255,0.04);
    padding: 4px;
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
}
.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    border-radius: var(--radius-sm) !important;
    padding: 8px 16px !important;
    font-weight: 500 !important;
    color: var(--text-muted) !important;
    border: none !important;
    transition: all 0.15s ease !important;
}
.stTabs [data-baseweb="tab"]:hover {
    background: rgba(255,255,255,0.06) !important;
    color: var(--text-primary) !important;
}
.stTabs [aria-selected="true"] {
    background: linear-gradient(135deg, var(--brand-primary) 0%, var(--brand-primary-dark) 100%) !important;
    color: #FFFFFF !important;
    box-shadow: 0 2px 8px rgba(255,153,0,0.35) !important;
}
.stTabs [aria-selected="true"] * { color: #FFFFFF !important; }
.stTabs [data-baseweb="tab-highlight"] { display: none !important; }
.stTabs [data-baseweb="tab-border"] { display: none !important; }

/* Native st.metric polish */
div[data-testid="stMetric"] {
    background: var(--surface);
    padding: 14px 18px;
    border-radius: var(--radius-md);
    box-shadow: var(--shadow-card);
    border: 1px solid var(--border);
    border-left: 4px solid var(--brand-primary);
}
div[data-testid="stMetricLabel"] > div,
div[data-testid="stMetricLabel"] p {
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-muted) !important;
}
div[data-testid="stMetricValue"] {
    font-weight: 700 !important;
    color: var(--text-primary) !important;
}
div[data-testid="stMetricDelta"] {
    color: var(--text-secondary) !important;
}

/* Dataframes */
[data-testid="stDataFrame"] {
    border-radius: var(--radius-md);
    overflow: hidden;
    box-shadow: var(--shadow-card);
    border: 1px solid var(--border);
}

/* Inputs (selectbox, number input, text input) — dark theme */
.stTextInput > div > div,
.stNumberInput > div > div,
.stDateInput > div > div,
.stSelectbox > div > div,
.stMultiSelect > div > div,
.stTextArea > div > div {
    background: rgba(255,255,255,0.06) !important;
    color: var(--text-primary) !important;
    border-color: var(--border) !important;
}
.stTextInput input, .stNumberInput input, .stTextArea textarea,
.stDateInput input {
    color: var(--text-primary) !important;
    caret-color: var(--brand-primary) !important;
}
.stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus,
.stDateInput input:focus {
    border-color: var(--brand-primary) !important;
    box-shadow: 0 0 0 1px var(--brand-primary) !important;
}
/* Selectbox dropdown */
[data-baseweb="popover"] {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    box-shadow: var(--shadow-elevated) !important;
}
[data-baseweb="popover"] li {
    color: var(--text-primary) !important;
}
[data-baseweb="popover"] li:hover {
    background: rgba(255,153,0,0.10) !important;
}

/* Radio buttons & checkboxes */
.stRadio label, .stCheckbox label {
    color: var(--text-secondary) !important;
}
.stRadio [role="radiogroup"] label:hover,
.stCheckbox label:hover {
    color: var(--text-primary) !important;
}

/* Slider */
.stSlider label {
    color: var(--text-secondary) !important;
}
.stSlider [data-baseweb="slider"] div[role="slider"] {
    background: var(--brand-primary) !important;
    border-color: var(--brand-primary) !important;
}

/* =========================================================================
   Sidebar — always dark navy for brand consistency.
   We keep strong contrast inside, since the hero is also dark-navy.
   ========================================================================= */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1E293B 0%, #0F172A 100%) !important;
    border-right: 1px solid rgba(255,255,255,0.06);
}
section[data-testid="stSidebar"],
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] li,
section[data-testid="stSidebar"] label {
    color: rgba(255,255,255,0.88);
}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] h4 {
    color: #FFFFFF !important;
    letter-spacing: -0.01em;
}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
section[data-testid="stSidebar"] small {
    color: rgba(255,255,255,0.62) !important;
}
section[data-testid="stSidebar"] .stRadio label,
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] .stSlider label,
section[data-testid="stSidebar"] .stNumberInput label,
section[data-testid="stSidebar"] .stFileUploader label,
section[data-testid="stSidebar"] .stTextInput label {
    color: rgba(255,255,255,0.82) !important;
    font-weight: 500 !important;
}
section[data-testid="stSidebar"] [data-baseweb="select"] > div,
section[data-testid="stSidebar"] [data-baseweb="input"] > div,
section[data-testid="stSidebar"] .stTextInput > div > div,
section[data-testid="stSidebar"] .stNumberInput > div > div,
section[data-testid="stSidebar"] .stSelectbox > div > div {
    background: rgba(255,255,255,0.06) !important;
    border-color: rgba(255,255,255,0.14) !important;
    color: #FFFFFF !important;
}
section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] textarea {
    color: #FFFFFF !important;
    caret-color: #FFFFFF !important;
}
section[data-testid="stSidebar"] [data-testid="stFileUploadDropzone"] {
    background: rgba(255,255,255,0.04) !important;
    border: 1.5px dashed rgba(255,255,255,0.22) !important;
    border-radius: var(--radius-md) !important;
    transition: all 0.15s ease;
}
section[data-testid="stSidebar"] [data-testid="stFileUploadDropzone"] * {
    color: rgba(255,255,255,0.82) !important;
}
section[data-testid="stSidebar"] [data-testid="stFileUploadDropzone"]:hover {
    border-color: var(--brand-primary) !important;
    background: rgba(255,153,0,0.06) !important;
}
section[data-testid="stSidebar"] .stExpander,
section[data-testid="stSidebar"] [data-testid="stExpander"] {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: var(--radius-md);
    margin-bottom: 8px;
}
section[data-testid="stSidebar"] .stExpander summary,
section[data-testid="stSidebar"] [data-testid="stExpander"] summary {
    color: #FFFFFF !important;
    font-weight: 600 !important;
}
/* Sidebar buttons — consistent with main area dark buttons */
section[data-testid="stSidebar"] .stButton > button:not([kind="primary"]),
section[data-testid="stSidebar"] .stDownloadButton > button:not([kind="primary"]) {
    background: var(--btn-secondary-bg) !important;
    border: 1px solid var(--btn-secondary-border) !important;
    color: var(--btn-secondary-text) !important;
}
section[data-testid="stSidebar"] .stButton > button:not([kind="primary"]):hover,
section[data-testid="stSidebar"] .stDownloadButton > button:not([kind="primary"]):hover {
    background: var(--btn-secondary-hover-bg) !important;
    border-color: var(--btn-secondary-hover-border) !important;
    color: var(--btn-secondary-hover-text) !important;
}
section[data-testid="stSidebar"] .stButton > button[kind="primary"],
section[data-testid="stSidebar"] .stDownloadButton > button[kind="primary"] {
    background: var(--btn-primary-bg) !important;
    color: var(--btn-primary-text) !important;
    border: none !important;
    box-shadow: var(--btn-primary-shadow) !important;
}
section[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover,
section[data-testid="stSidebar"] .stDownloadButton > button[kind="primary"]:hover {
    box-shadow: var(--btn-primary-hover-shadow) !important;
    filter: brightness(1.08) !important;
}

/* =========================================================================
   Expanders (main area) — dark theme
   ========================================================================= */
.stApp [data-testid="stExpander"] {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
}
.stApp [data-testid="stExpander"] summary {
    color: var(--text-primary) !important;
    font-weight: 600 !important;
}
.stApp [data-testid="stExpander"] summary:hover {
    background: var(--surface-hover);
}

/* =========================================================================
   Alerts / notifications
   ========================================================================= */
div[data-baseweb="notification"] {
    border-radius: var(--radius-md) !important;
}
/* Streamlit alert backgrounds adapt to theme */
[data-testid="stAlert"] {
    border-radius: var(--radius-md) !important;
    border: 1px solid var(--border);
}

/* =========================================================================
   Progress bars
   ========================================================================= */
.stProgress > div > div > div > div {
    background: linear-gradient(90deg,
        var(--brand-primary) 0%, var(--brand-primary-light) 50%, var(--brand-primary) 100%) !important;
    background-size: 200% 100% !important;
    animation: aart-progress-shimmer 2s linear infinite !important;
}
.stProgress > div > div > div {
    background: var(--border) !important;
}
@keyframes aart-progress-shimmer {
    0%   { background-position: 200% 0; }
    100% { background-position: -200% 0; }
}

/* =========================================================================
   Code / monospace
   ========================================================================= */
code, pre, .stCode {
    font-family: var(--font-mono) !important;
    background: var(--surface-alt) !important;
    color: var(--text-primary) !important;
    border-radius: var(--radius-sm);
}

/* =========================================================================
   Dividers
   ========================================================================= */
hr, [data-testid="stMarkdownContainer"] hr {
    border: none;
    border-top: 1px solid var(--divider);
    margin: 1rem 0;
}

/* =========================================================================
   Mobile
   ========================================================================= */
@media (max-width: 768px) {
    .aart-hero { padding: 16px 18px; }
    .aart-hero-title { font-size: 1.35rem; }
    .aart-hero-pills { width: 100%; }
    .aart-kpi { min-height: 92px; padding: 14px; }
    .aart-kpi-value { font-size: 1.4rem; }
    .aart-day-card { flex: 0 0 140px; }
}
</style>
"""


def apply_global_styles() -> None:
    """Inject the global AART design-system CSS.

    Dark-only: forces a consistent dark palette regardless of
    Streamlit's theme setting. Idempotent: safe to call on every rerun.
    """
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(value) -> str:
    """Minimal HTML escaping for text injected into the template."""
    if value is None:
        return ""
    return (str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def render_app_header(
    title: str = "AART — AI Assisted Rostering Tool",
    subtitle: str | None = "Multi-week roster planning with priority tuning and network-wide optimization",
    week: str | None = None,
    total_das: int | None = None,
    data_loaded: bool = False,
    breadcrumb: Sequence[str] | None = None,
) -> None:
    """Render the full-width glassmorphism hero banner.

    Parameters
    ----------
    title : str
        App title (rendered with gradient text).
    subtitle : str, optional
        One-line supporting text shown under the title.
    week : str, optional
        Current week label (e.g. ``"WK17"``). Rendered as a pill.
    total_das : int, optional
        Total DAs in the current pool. Rendered as a pill.
    data_loaded : bool
        When ``True`` shows a pulsing green "Data loaded" status pill;
        otherwise a neutral "No data" pill.
    breadcrumb : sequence of str, optional
        Items like ``["Home", "Store Roster", "QRA1"]``. The final item
        is highlighted as the active crumb.
    """
    pills_html = []
    if week:
        pills_html.append(
            f'<span class="aart-pill">📆 <strong>{_esc(week)}</strong></span>'
        )
    if total_das is not None:
        pills_html.append(
            f'<span class="aart-pill">👥 <strong>{int(total_das)}</strong> DAs</span>'
        )
    if data_loaded:
        pills_html.append(
            '<span class="aart-pill"><span class="dot"></span> Data loaded</span>'
        )
    else:
        pills_html.append(
            '<span class="aart-pill status-off"><span class="dot"></span> Awaiting upload</span>'
        )

    crumb_html = ""
    if breadcrumb:
        items = list(breadcrumb)
        parts = []
        for i, item in enumerate(items):
            cls = "active" if i == len(items) - 1 else ""
            sep = '<span class="sep">›</span>' if i > 0 else ""
            parts.append(f'{sep}<span class="{cls}">{_esc(item)}</span>')
        crumb_html = f'<div class="aart-breadcrumb">{" ".join(parts)}</div>'

    subtitle_html = (
        f'<div class="aart-hero-subtitle">{_esc(subtitle)}</div>'
        if subtitle else ""
    )

    html = f"""
<div class="aart-hero">
  <div class="aart-hero-inner">
    <div>
      <h1 class="aart-hero-title">🚀 {_esc(title)}</h1>
      {subtitle_html}
      {crumb_html}
    </div>
    <div class="aart-hero-pills">
      {"".join(pills_html)}
    </div>
  </div>
</div>
"""
    st.markdown(html, unsafe_allow_html=True)


def render_section_header(icon: str, title: str, description: str | None = None) -> None:
    """Render a styled section header with an icon badge."""
    desc_html = (
        f'<div class="aart-section-desc">{_esc(description)}</div>'
        if description else ""
    )
    html = f"""
<div class="aart-section-header">
  <div class="aart-section-icon">{_esc(icon)}</div>
  <div>
    <div class="aart-section-title">{_esc(title)}</div>
    {desc_html}
  </div>
</div>
"""
    st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# KPI cards
# ---------------------------------------------------------------------------

_COLOR_CLASS = {
    "red": "red", "danger": "red",
    "green": "green", "success": "green",
    "blue": "blue", "info": "blue",
    "orange": "orange", "brand": "orange",
    "purple": "purple",
    "amber": "amber", "warning": "amber",
}


def render_kpi_card(
    title: str,
    value,
    delta: str | None = None,
    color: str = "blue",
    icon: str = "📊",
    caption: str | None = None,
    show_spark: bool = True,
) -> str:
    """Return the HTML for a single KPI card.

    Use inside an ``st.columns`` layout::

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(render_kpi_card("Gap", 42, "-8 vs last week",
                                        color="red", icon="🔻"),
                        unsafe_allow_html=True)

    Parameters
    ----------
    title : str
        Upper-case title text.
    value : Any
        Main metric value. Formatted as-is (call ``f"{x:,}"`` yourself
        for thousands separators).
    delta : str, optional
        Small delta line under the value. Prefix with ``+`` or ``-``
        for automatic up/down coloring, otherwise neutral grey.
    color : str
        ``red | green | blue | orange | purple | amber``.
    icon : str
        Emoji or short string shown in the top-right.
    caption : str, optional
        Sub-caption under the delta.
    show_spark : bool
        Whether to show a placeholder sparkline strip at the bottom.
    """
    klass = _COLOR_CLASS.get(color, "blue")

    delta_html = ""
    if delta:
        d = str(delta).strip()
        if d.startswith("+"):
            dcls = "up"
        elif d.startswith("-"):
            dcls = "down"
        else:
            dcls = "flat"
        delta_html = f'<div class="aart-kpi-delta {dcls}">{_esc(d)}</div>'

    caption_html = (
        f'<div class="aart-kpi-caption">{_esc(caption)}</div>'
        if caption else ""
    )
    spark_html = '<div class="aart-spark"></div>' if show_spark else ""

    return f"""
<div class="aart-kpi {klass}">
  <div class="aart-kpi-header">
    <div class="aart-kpi-title">{_esc(title)}</div>
    <div class="aart-kpi-icon">{_esc(icon)}</div>
  </div>
  <div class="aart-kpi-value">{_esc(value)}</div>
  {delta_html}
  {caption_html}
  {spark_html}
</div>
"""


def render_kpi_grid(cards: Sequence[Mapping]) -> None:
    """Render a list of KPI cards as a responsive Streamlit grid.

    Each card is a dict of keyword arguments accepted by
    :func:`render_kpi_card`.
    """
    if not cards:
        return
    cols = st.columns(len(cards))
    for col, card in zip(cols, cards):
        with col:
            st.markdown(render_kpi_card(**card), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Day cards
# ---------------------------------------------------------------------------

def render_day_card(day: str, das_working: int, gap: int, demand: int) -> str:
    """Return HTML for a single day card (used in the daily breakdown strip).

    Color rules:
      * gap == 0  → green
      * 0 < gap < 10 → amber
      * gap >= 10 → red
    """
    if gap <= 0:
        klass = "green"
        badge_cls = "green"
    elif gap < 10:
        klass = "amber"
        badge_cls = "amber"
    else:
        klass = "red"
        badge_cls = "red"

    ratio = 0.0
    if demand and demand > 0:
        rostered = max(0, demand - max(0, gap))
        ratio = min(1.0, rostered / demand)
    width_pct = round(ratio * 100, 1)

    return f"""
<div class="aart-day-card {klass}">
  <div class="aart-day-header">
    <div class="aart-day-name">{_esc(day)}</div>
    <div class="aart-day-gap {badge_cls}">Gap {int(gap)}</div>
  </div>
  <div class="aart-day-count">{int(das_working)}</div>
  <div class="aart-day-count-sub">DAs working · demand {int(demand)}</div>
  <div class="aart-day-ratio"><div style="width:{width_pct}%"></div></div>
</div>
"""


def render_day_strip(day_records: Iterable[Mapping]) -> None:
    """Render a horizontally-scrollable strip of day cards.

    Each record must contain: ``day``, ``das_working``, ``gap``, ``demand``.
    """
    cards = "".join(
        render_day_card(
            r.get("day", ""),
            int(r.get("das_working", 0) or 0),
            int(r.get("gap", 0) or 0),
            int(r.get("demand", 0) or 0),
        )
        for r in day_records
    )
    st.markdown(f'<div class="aart-day-strip">{cards}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Status badges and empty state
# ---------------------------------------------------------------------------

def render_status_badge(status: str, text: str) -> str:
    """Return a small pill-shaped status badge.

    ``status`` is one of: ``success | warning | danger | info | neutral``.
    Returns a raw HTML snippet — wrap it in ``st.markdown(..., unsafe_allow_html=True)``.
    """
    valid = {"success", "warning", "danger", "info", "neutral"}
    cls = status if status in valid else "neutral"
    return (
        f'<span class="aart-badge {cls}">'
        f'<span class="dot"></span>{_esc(text)}</span>'
    )


def render_empty_state(
    icon: str,
    title: str,
    description: str,
    cta_label: str | None = None,
) -> None:
    """Render a dashed-border empty state block."""
    cta = (
        f'<div style="margin-top:12px;"><em>{_esc(cta_label)}</em></div>'
        if cta_label else ""
    )
    html = f"""
<div class="aart-empty">
  <div class="aart-empty-icon">{_esc(icon)}</div>
  <div class="aart-empty-title">{_esc(title)}</div>
  <div class="aart-empty-desc">{_esc(description)}</div>
  {cta}
</div>
"""
    st.markdown(html, unsafe_allow_html=True)
