"""
DA ROSTERING ENGINE - WEEKLY PLANNER
=====================================
Multi-week roster planning with interactive slot priority adjustment.

Based on v12.2 Network Optimizer with added week selection for:
- Ramadan planning (WK7-WK12)
- Multi-week capacity planning
- Week-by-week DA ramp-up

Features:
- Week selector (WK7-WK12)
- Slot priority sliders (0-10) for each hour
- Live heatmap showing coverage changes
- Day-by-day and hour-by-hour visualization
- Auto-optimizer (Genetic & Quick algorithms)
- Working rules controls
- DAs Needed & Utilization metrics
- All Stores Optimizer (network-wide)
- DA Transfer Module with JSON persistence
- Excess DAs calculation
- Smart transfer recommendations
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import io
import json
import os
from datetime import datetime

# Import V12.2 engine (Flexible Shifts)
from roster_engine_v12_2 import (
    DAYS, DEFAULT_PARAMS,
    get_params as engine_get_params,
    load_demand as engine_load_demand,
    load_available_das as engine_load_available_das,
    build_da_list as engine_build_da_list,
    build_demand_matrix as engine_build_demand_matrix,
    assign_shifts as engine_assign_shifts,
    generate_hourly_roster as engine_generate_hourly_roster,
    generate_da_summary as engine_generate_da_summary
)

# Import Fixed Shifts engine
from roster_engine_fixed_shifts import (
    FIXED_SHIFTS,
    get_params as fixed_get_params,
    build_da_list as fixed_build_da_list,
    assign_shifts_fixed,
    generate_hourly_roster as fixed_generate_hourly_roster,
    generate_da_summary as fixed_generate_da_summary,
    get_shift_distribution_summary,
    calculate_optimal_shift_distribution,
    detect_violations,
    fix_violations,
    validate_and_fix_shifts,
    calculate_valid_break_hour,
    get_valid_break_positions
)

# Import Proportional engine (v12.3) — same API as v12.2, prefixed v13_
from roster_engine_v12_3_proportional import (
    get_params as v13_get_params,
    load_demand as v13_load_demand,
    load_available_das as v13_load_available_das,
    build_da_list as v13_build_da_list,
    build_demand_matrix as v13_build_demand_matrix,
    assign_shifts as v13_assign_shifts,
    generate_hourly_roster as v13_generate_hourly_roster,
    generate_da_summary as v13_generate_da_summary,
)

# Import Demand-Driven engine (v12.4)
from roster_engine_v12_4_original_2 import (
    get_params as v14_get_params,
    build_da_list as v14_build_da_list,
    assign_shifts as v14_assign_shifts,
    generate_hourly_roster as v14_generate_hourly_roster,
    generate_da_summary as v14_generate_da_summary,
    reshuffle_das_for_carryover as v14_reshuffle,
)

# Import Demand-Driven Ultimate engine (v12.4_ultimate)
from roster_engine_v12_4_ultimate import (
    get_params as v14u_get_params,
    build_da_list as v14u_build_da_list,
    build_demand_matrix as v14u_build_demand_matrix,
    assign_shifts as v14u_assign_shifts,
    generate_hourly_roster as v14u_generate_hourly_roster,
    generate_da_summary as v14u_generate_da_summary,
    generate_carryover as v14u_generate_carryover,
    find_optimal_shifts as v14u_find_optimal_shifts,
)

# Import Tunable engine (v12.5)
from roster_engine_v12_5_tunable import (
    get_params as v15_get_params,
    build_da_list as v15_build_da_list,
    assign_shifts as v15_assign_shifts,
    generate_hourly_roster as v15_generate_hourly_roster,
    generate_da_summary as v15_generate_da_summary,
)

# v12.7 Overnight Shift engine removed

# AART UI + Continuity helpers (2026 redesign)
from ui_components import (
    apply_global_styles,
    render_app_header,
    render_kpi_grid,
    render_kpi_card,
    render_day_strip,
    render_status_badge,
    render_section_header,
    render_empty_state,
)
from week_continuity import (
    parse_previous_week_roster,
    build_continuity_constraints,
    build_continuity_report_df,
    build_store_detail_df,
    build_week_continuity_sheet,
    STRATEGY_FULL,
    STRATEGY_COVERAGE,
    STRATEGY_FLEX,
)


st.set_page_config(
    page_title="AART - AI Assisted Rostering Tool",
    page_icon="🚀",
    layout="wide"
)

# =============================================================================
# UNDO/REDO STATE MANAGEMENT
# =============================================================================
MAX_HISTORY = 50  # Maximum number of undo states to keep

def init_undo_redo():
    """Initialize undo/redo state tracking."""
    if 'undo_stack' not in st.session_state:
        st.session_state.undo_stack = []
    if 'redo_stack' not in st.session_state:
        st.session_state.redo_stack = []
    if 'last_saved_state' not in st.session_state:
        st.session_state.last_saved_state = None

def get_current_state():
    """Capture current tuning state including optimizer results."""
    state = {
        'store_priorities': {k: dict(v) for k, v in st.session_state.get('store_priorities', {}).items()},
        'day_multipliers': dict(st.session_state.get('day_multipliers', {})),
        'selected_store': st.session_state.get('selected_store', None),
        'optimized_shifts': {}  # NEW: capture optimizer state
    }
    
    # Capture all optimized_shifts_{store} states
    for key in st.session_state.keys():
        if key.startswith('optimized_shifts_'):
            store = key.replace('optimized_shifts_', '')
            shifts_df = st.session_state[key]
            if shifts_df is not None:
                # Store as dict for JSON serialization
                state['optimized_shifts'][store] = shifts_df.to_dict('records')
    
    return state

def restore_state(state):
    """Restore a saved state including optimizer results."""
    if state is None:
        return
    if 'store_priorities' in state:
        st.session_state.store_priorities = {k: dict(v) for k, v in state['store_priorities'].items()}
    if 'day_multipliers' in state:
        st.session_state.day_multipliers = dict(state['day_multipliers'])
    if 'selected_store' in state and state['selected_store']:
        st.session_state.selected_store = state['selected_store']
    
    # NEW: Restore optimizer shifts
    # First, clear all existing optimized_shifts
    keys_to_remove = [k for k in st.session_state.keys() if k.startswith('optimized_shifts_')]
    for key in keys_to_remove:
        del st.session_state[key]
    
    # Then restore from saved state
    if 'optimized_shifts' in state:
        for store, shifts_records in state['optimized_shifts'].items():
            if shifts_records:
                st.session_state[f'optimized_shifts_{store}'] = pd.DataFrame(shifts_records)

def save_state_for_undo():
    """Save current state to undo stack (call before making changes)."""
    current = get_current_state()
    # Only save if state actually changed
    if st.session_state.last_saved_state != current:
        st.session_state.undo_stack.append(current)
        # Limit stack size
        if len(st.session_state.undo_stack) > MAX_HISTORY:
            st.session_state.undo_stack.pop(0)
        # Clear redo stack when new action is taken
        st.session_state.redo_stack = []
        st.session_state.last_saved_state = current

def undo():
    """Undo last change."""
    if st.session_state.undo_stack:
        # Save current state to redo stack
        current = get_current_state()
        st.session_state.redo_stack.append(current)
        # Restore previous state
        previous = st.session_state.undo_stack.pop()
        restore_state(previous)
        st.session_state.last_saved_state = previous
        return True
    return False

def redo():
    """Redo last undone change."""
    if st.session_state.redo_stack:
        # Save current state to undo stack
        current = get_current_state()
        st.session_state.undo_stack.append(current)
        # Restore redo state
        next_state = st.session_state.redo_stack.pop()
        restore_state(next_state)
        st.session_state.last_saved_state = next_state
        return True
    return False

def can_undo():
    """Check if undo is available."""
    return len(st.session_state.get('undo_stack', [])) > 0

def can_redo():
    """Check if redo is available."""
    return len(st.session_state.get('redo_stack', [])) > 0

# Initialize undo/redo on app load
init_undo_redo()

# =============================================================================
# PERSISTENT AUTO-SAVE SYSTEM
# =============================================================================
ROSTER_SAVES_DIR = 'roster_saves'
MAX_PERSISTENT_HISTORY = 15  # Max history entries per store

def ensure_saves_dir():
    """Ensure the roster_saves directory exists."""
    if not os.path.exists(ROSTER_SAVES_DIR):
        os.makedirs(ROSTER_SAVES_DIR)

def get_save_filepath(week):
    """Get the filepath for a week's save file."""
    ensure_saves_dir()
    return os.path.join(ROSTER_SAVES_DIR, f'{week}_roster_saves.json')

def load_persistent_saves(week):
    """Load persistent saves for a specific week."""
    filepath = get_save_filepath(week)
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        'week': week,
        'last_updated': None,
        'stores': {}
    }

def save_persistent_data(week, data):
    """Save persistent data for a week."""
    data['last_updated'] = datetime.now().isoformat()
    filepath = get_save_filepath(week)
    try:
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    except PermissionError:
        pass

def get_store_history(week, store):
    """Get the history for a specific store."""
    data = load_persistent_saves(week)
    if store not in data['stores']:
        data['stores'][store] = {
            'current_index': -1,
            'history': [],
            'checkpoints': []
        }
    return data['stores'][store]

def save_to_persistent_history(week, store, shifts_df, action_name, gap=None):
    """
    Save current state to persistent history for a store.
    This is called after every optimization action.
    """
    if shifts_df is None:
        return
    
    data = load_persistent_saves(week)
    
    if store not in data['stores']:
        data['stores'][store] = {
            'current_index': -1,
            'history': [],
            'checkpoints': []
        }
    
    store_data = data['stores'][store]
    current_idx = store_data['current_index']
    
    # If we're not at the end of history, truncate forward history (new branch)
    if current_idx >= 0 and current_idx < len(store_data['history']) - 1:
        store_data['history'] = store_data['history'][:current_idx + 1]
    
    # Create new history entry
    entry = {
        'shifts': shifts_df.to_dict('records'),
        'gap': int(gap) if gap is not None else None,
        'action': action_name,
        'timestamp': datetime.now().isoformat()
    }
    
    store_data['history'].append(entry)
    store_data['current_index'] = len(store_data['history']) - 1
    
    # Limit history size
    if len(store_data['history']) > MAX_PERSISTENT_HISTORY:
        # Remove oldest entries
        excess = len(store_data['history']) - MAX_PERSISTENT_HISTORY
        store_data['history'] = store_data['history'][excess:]
        store_data['current_index'] = len(store_data['history']) - 1
    
    data['stores'][store] = store_data
    save_persistent_data(week, data)

def persistent_undo(week, store):
    """
    Undo to previous state in persistent history.
    Returns the shifts DataFrame or None if can't undo.
    """
    data = load_persistent_saves(week)
    
    if store not in data['stores']:
        return None, "No history for this store"
    
    store_data = data['stores'][store]
    current_idx = store_data['current_index']
    
    if current_idx <= 0:
        return None, "Already at oldest state"
    
    # Move back one step
    store_data['current_index'] = current_idx - 1
    data['stores'][store] = store_data
    save_persistent_data(week, data)
    
    # Return the previous state
    prev_entry = store_data['history'][store_data['current_index']]
    shifts_df = pd.DataFrame(prev_entry['shifts'])
    return shifts_df, f"Restored: {prev_entry['action']} (Gap: {prev_entry['gap']})"

def persistent_redo(week, store):
    """
    Redo to next state in persistent history.
    Returns the shifts DataFrame or None if can't redo.
    """
    data = load_persistent_saves(week)
    
    if store not in data['stores']:
        return None, "No history for this store"
    
    store_data = data['stores'][store]
    current_idx = store_data['current_index']
    
    if current_idx >= len(store_data['history']) - 1:
        return None, "Already at newest state"
    
    # Move forward one step
    store_data['current_index'] = current_idx + 1
    data['stores'][store] = store_data
    save_persistent_data(week, data)
    
    # Return the next state
    next_entry = store_data['history'][store_data['current_index']]
    shifts_df = pd.DataFrame(next_entry['shifts'])
    return shifts_df, f"Restored: {next_entry['action']} (Gap: {next_entry['gap']})"

def persistent_jump_to(week, store, index):
    """
    Jump to a specific index in history.
    Returns the shifts DataFrame or None if invalid.
    """
    data = load_persistent_saves(week)
    
    if store not in data['stores']:
        return None, "No history for this store"
    
    store_data = data['stores'][store]
    
    if index < 0 or index >= len(store_data['history']):
        return None, "Invalid history index"
    
    store_data['current_index'] = index
    data['stores'][store] = store_data
    save_persistent_data(week, data)
    
    entry = store_data['history'][index]
    shifts_df = pd.DataFrame(entry['shifts'])
    return shifts_df, f"Jumped to: {entry['action']} (Gap: {entry['gap']})"

def save_checkpoint(week, store, shifts_df, name, gap=None):
    """Save a named checkpoint that won't be auto-deleted."""
    if shifts_df is None:
        return False
    
    data = load_persistent_saves(week)
    
    if store not in data['stores']:
        data['stores'][store] = {
            'current_index': -1,
            'history': [],
            'checkpoints': []
        }
    
    checkpoint = {
        'name': name,
        'shifts': shifts_df.to_dict('records'),
        'gap': int(gap) if gap is not None else None,
        'timestamp': datetime.now().isoformat()
    }
    
    data['stores'][store]['checkpoints'].append(checkpoint)
    save_persistent_data(week, data)
    return True

def load_checkpoint(week, store, checkpoint_index):
    """Load a checkpoint by index."""
    data = load_persistent_saves(week)
    
    if store not in data['stores']:
        return None, "No data for this store"
    
    checkpoints = data['stores'][store].get('checkpoints', [])
    
    if checkpoint_index < 0 or checkpoint_index >= len(checkpoints):
        return None, "Invalid checkpoint index"
    
    checkpoint = checkpoints[checkpoint_index]
    shifts_df = pd.DataFrame(checkpoint['shifts'])
    return shifts_df, f"Loaded checkpoint: {checkpoint['name']}"

def delete_checkpoint(week, store, checkpoint_index):
    """Delete a checkpoint by index."""
    data = load_persistent_saves(week)
    
    if store not in data['stores']:
        return False
    
    checkpoints = data['stores'][store].get('checkpoints', [])
    
    if checkpoint_index < 0 or checkpoint_index >= len(checkpoints):
        return False
    
    data['stores'][store]['checkpoints'].pop(checkpoint_index)
    save_persistent_data(week, data)
    return True

def clear_store_history(week, store):
    """Clear all history for a store (keeps checkpoints)."""
    data = load_persistent_saves(week)
    
    if store in data['stores']:
        data['stores'][store]['history'] = []
        data['stores'][store]['current_index'] = -1
        save_persistent_data(week, data)
    return True

def load_current_from_persistent(week, store):
    """
    Load the current state from persistent storage.
    Called on app startup or when switching stores.
    Returns shifts_df or None.
    """
    data = load_persistent_saves(week)
    
    if store not in data['stores']:
        return None
    
    store_data = data['stores'][store]
    current_idx = store_data['current_index']
    
    if current_idx < 0 or current_idx >= len(store_data['history']):
        return None
    
    entry = store_data['history'][current_idx]
    return pd.DataFrame(entry['shifts'])

def can_persistent_undo(week, store):
    """Check if persistent undo is available."""
    data = load_persistent_saves(week)
    if store not in data['stores']:
        return False
    return data['stores'][store]['current_index'] > 0

def can_persistent_redo(week, store):
    """Check if persistent redo is available."""
    data = load_persistent_saves(week)
    if store not in data['stores']:
        return False
    store_data = data['stores'][store]
    return store_data['current_index'] < len(store_data['history']) - 1

def get_persistent_history_display(week, store):
    """Get history entries formatted for display."""
    data = load_persistent_saves(week)
    
    if store not in data['stores']:
        return [], -1
    
    store_data = data['stores'][store]
    history = store_data['history']
    current_idx = store_data['current_index']
    
    display_items = []
    for i, entry in enumerate(history):
        timestamp = entry.get('timestamp', '')[:16].replace('T', ' ')
        gap_str = f"Gap: {entry['gap']}" if entry['gap'] is not None else ""
        marker = "● " if i == current_idx else "  "
        display_items.append({
            'index': i,
            'label': f"{marker}[{i+1}] {entry['action']} - {gap_str} ({timestamp})",
            'action': entry['action'],
            'gap': entry['gap'],
            'timestamp': timestamp,
            'is_current': i == current_idx
        })
    
    return display_items, current_idx

def get_checkpoints_display(week, store):
    """Get checkpoints formatted for display."""
    data = load_persistent_saves(week)
    
    if store not in data['stores']:
        return []
    
    checkpoints = data['stores'][store].get('checkpoints', [])
    
    display_items = []
    for i, cp in enumerate(checkpoints):
        timestamp = cp.get('timestamp', '')[:16].replace('T', ' ')
        gap_str = f"Gap: {cp['gap']}" if cp['gap'] is not None else ""
        display_items.append({
            'index': i,
            'label': f"🔒 {cp['name']} - {gap_str} ({timestamp})",
            'name': cp['name'],
            'gap': cp['gap'],
            'timestamp': timestamp
        })
    
    return display_items

# =============================================================================
# STORE PARAMETERS
# =============================================================================

VALID_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

OVERRIDABLE_KEYS = [
    'shift_hours', 'break_hours', 'max_continuous', 'min_rest',
    'working_days', 'night_shift_enabled', 'operating_start',
    'operating_end', 'operating_days', 'max_shifts',
]

_INTEGER_COLUMNS = [
    'shift_hours', 'break_hours', 'max_continuous', 'min_rest',
    'working_days', 'operating_start', 'operating_end', 'max_shifts',
]


def parse_store_parameters_sheet(df):
    """Parse a Store Parameters DataFrame into a store_configs dict.

    Parameters
    ----------
    df : pandas.DataFrame
        DataFrame with columns like 'Store', 'shift_hours', etc.

    Returns
    -------
    (store_configs, warnings) : tuple[dict, list[str]]
    """
    store_configs = {}
    warnings = []
    seen_stores = set()

    for row_idx, row in df.iterrows():
        # --- Store name validation ---
        store_val = row.get('Store')
        if store_val is None or (isinstance(store_val, float) and pd.isna(store_val)):
            warnings.append(f"Row {row_idx + 1}: blank Store name — skipped.")
            continue
        store_name = str(store_val).strip()
        if store_name == '' or ' ' in store_name:
            warnings.append(f"Row {row_idx + 1}: blank Store name — skipped.")
            continue

        # Duplicate detection
        if store_name in seen_stores:
            warnings.append(
                f"Store '{store_name}' appears multiple times; last row wins."
            )
        seen_stores.add(store_name)

        entry = {}

        # --- Integer columns ---
        for col in _INTEGER_COLUMNS:
            if col not in df.columns:
                continue
            val = row.get(col)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            # Try to convert to int
            try:
                fval = float(val)
            except (ValueError, TypeError):
                warnings.append(
                    f"Store '{store_name}', {col}: '{val}' is not a valid integer."
                )
                continue
            if pd.isna(fval):
                continue
            if fval != int(fval):
                warnings.append(
                    f"Store '{store_name}', {col}: '{val}' is not a valid integer."
                )
                continue
            entry[col] = int(fval)

        # --- night_shift_enabled ---
        if 'night_shift_enabled' in df.columns:
            val = row.get('night_shift_enabled')
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                if isinstance(val, bool):
                    entry['night_shift_enabled'] = val
                elif isinstance(val, (np.bool_,)):
                    entry['night_shift_enabled'] = bool(val)
                elif isinstance(val, str):
                    upper = val.strip().upper()
                    if upper == 'TRUE':
                        entry['night_shift_enabled'] = True
                    elif upper == 'FALSE':
                        entry['night_shift_enabled'] = False
                    else:
                        warnings.append(
                            f"Store '{store_name}', night_shift_enabled: "
                            f"'{val}' is not valid (expected TRUE/FALSE)."
                        )
                else:
                    warnings.append(
                        f"Store '{store_name}', night_shift_enabled: "
                        f"'{val}' is not valid (expected TRUE/FALSE)."
                    )

        # --- operating_days ---
        if 'operating_days' in df.columns:
            val = row.get('operating_days')
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                if isinstance(val, str) and val.strip():
                    parts = [d.strip() for d in val.split(',')]
                    invalid = [d for d in parts if d not in VALID_DAYS]
                    if invalid:
                        warnings.append(
                            f"Store '{store_name}', operating_days: "
                            f"invalid day(s) {invalid}."
                        )
                    else:
                        entry['operating_days'] = parts

        if entry:
            store_configs[store_name] = entry

    return store_configs, warnings


def store_label(store_name, store_configs):
    """Return a display label for a store (plain name)."""
    return store_name


def store_configs_to_dataframe(store_configs):
    """Convert a store_configs dict back to a DataFrame."""
    rows = []
    for store_name, entry in store_configs.items():
        row = {'Store': store_name}
        for key, val in entry.items():
            if key == 'operating_days' and isinstance(val, list):
                row[key] = ','.join(val)
            else:
                row[key] = val
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=['Store'] + OVERRIDABLE_KEYS)
    return pd.DataFrame(rows)


def resolve_params(global_params, store, store_configs):
    """Merge global parameters with store-level overrides.

    Returns a new dict with all global keys, overridden by any matching
    keys in store_configs[store].  Also computes 'effective_hours'.
    """
    result = dict(global_params)
    overrides = store_configs.get(store, {}) if store_configs else {}
    for key in OVERRIDABLE_KEYS:
        if key in overrides:
            result[key] = overrides[key]
    result['effective_hours'] = result.get('shift_hours', 10) - result.get('break_hours', 1)
    return result


def validate_resolved_params(resolved, store):
    """Warn when shift + rest > 24 h."""
    warnings = []
    shift = resolved.get('shift_hours', 0)
    rest = resolved.get('min_rest', 0)
    if shift + rest > 24:
        warnings.append(
            f"Store '{store}': shift_hours ({shift}) + min_rest ({rest}) = "
            f"{shift + rest} exceeds 24 h."
        )
    return warnings


def is_within_window(hour, start, end):
    """Return True if *hour* falls within [start, end)."""
    return start <= hour < end


def get_valid_start_times(params, store_configs, store):
    """Return list of valid shift start hours for *store*.

    A start hour is valid when the entire shift (start .. start+shift_hours)
    fits within [operating_start, operating_end].  If no operating window is
    configured, all 24 hours are valid.
    """
    cfg = store_configs.get(store, {}) if store_configs else {}
    op_start = cfg.get('operating_start')
    op_end = cfg.get('operating_end')
    shift_hours = params.get('shift_hours', 10)
    if op_start is None or op_end is None:
        return list(range(24))
    return [h for h in range(op_start, op_end) if h + shift_hours <= op_end]


def get_non_operating_day_indices(store_configs, store):
    """Return list of day indices (0=Sun … 6=Sat) that are NOT operating days."""
    cfg = store_configs.get(store, {}) if store_configs else {}
    op_days = cfg.get('operating_days')
    if op_days is None:
        return []
    return [i for i, d in enumerate(DAYS) if d not in op_days]


def inject_operating_window_params(engine_params, store, store_configs):
    """Add valid_start_times and non_operating_day_indices to *engine_params*."""
    cfg = store_configs.get(store, {}) if store_configs else {}
    if 'operating_start' in cfg and 'operating_end' in cfg:
        engine_params['valid_start_times'] = get_valid_start_times(
            engine_params, store_configs, store
        )
    if 'operating_days' in cfg:
        engine_params['non_operating_day_indices'] = get_non_operating_day_indices(
            store_configs, store
        )


def apply_operating_window(demand_df, store, store_configs):
    """Zero out demand outside the store's operating window (in-place).

    Handles both hour-of-day restrictions (operating_start/operating_end)
    and day-of-week restrictions (operating_days).
    """
    cfg = store_configs.get(store, {}) if store_configs else {}
    if not cfg:
        return

    op_start = cfg.get('operating_start')
    op_end = cfg.get('operating_end')
    op_days = cfg.get('operating_days')

    mask = demand_df['Store'] == store

    # Zero hours outside [op_start, op_end)
    if op_start is not None and op_end is not None:
        hour_outside = ~demand_df['Slot'].between(op_start, op_end - 1)
        if op_days is not None:
            # Only zero hours on operating days (non-op days handled below)
            on_op_day = demand_df['Day'].isin(op_days)
            demand_df.loc[mask & hour_outside & on_op_day, 'DA Required'] = 0
        else:
            demand_df.loc[mask & hour_outside, 'DA Required'] = 0

    # Zero entire non-operating days
    if op_days is not None:
        non_op_day = ~demand_df['Day'].isin(op_days)
        demand_df.loc[mask & non_op_day, 'DA Required'] = 0


# =============================================================================
# POST-PROCESSING OPTIMIZERS
# =============================================================================
# All optimizers respect working rules from sliders:
# 1. Min rest between shifts (from slider)
# 2. Max continuous work before break (from slider)
# 3. Working days per week (from slider)
# 4. Shift hours and break hours (from sliders)
# =============================================================================

def safe_optimize(optimizer_func, shifts_df, demand_df, store, params):
    """
    Guardrail wrapper for ANY optimizer.
    Runs the optimizer, validates the result, and reverts if violations exist
    that weren't already present in the input.
    Returns: (result_shifts, changes) — guaranteed no NEW violations.
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0
    
    result = optimizer_func(shifts_df, demand_df, store, params)
    
    # Handle both 2-tuple (shifts, changes) and 3-tuple (shifts, changes, iterations) returns
    if isinstance(result, tuple):
        result_shifts = result[0]
        changes = result[1]
    else:
        return shifts_df, 0
    
    if changes == 0:
        return shifts_df, 0
    
    # Compare violations before and after — only reject if optimizer ADDED violations
    _, violations_before = validate_sacred_rules(shifts_df, params)
    is_valid, violations_after = validate_sacred_rules(result_shifts, params)
    
    if is_valid or len(violations_after) <= len(violations_before):
        return result_shifts, changes
    else:
        # Optimizer added new violations — revert
        return shifts_df, 0

def validate_sacred_rules(shifts_df, params, store_configs=None):
    """
    Validate that shifts don't violate sacred rules.
    Returns: (is_valid, violations_list)
    """
    violations = []

    # If store_configs provided, resolve per-store params
    if store_configs:
        stores_in_df = shifts_df['Store'].unique() if 'Store' in shifts_df.columns else []
    else:
        stores_in_df = []

    def _get_params_for_store(store_name):
        if store_configs and store_name:
            return resolve_params(params, store_name, store_configs)
        return params

    min_rest = params.get('min_rest', 12)
    max_continuous = params.get('max_continuous', 5)
    working_days = params.get('working_days', 6)
    shift_hours = params.get('shift_hours', 10)
    night_shift_enabled = params.get('night_shift_enabled', True)
    
    # Group by DA
    for da_id in shifts_df['DA_ID'].unique():
        da_shifts = shifts_df[shifts_df['DA_ID'] == da_id].sort_values('Day_Index')

        # Resolve per-store params if store_configs provided
        da_store = da_shifts['Store'].iloc[0] if 'Store' in da_shifts.columns else None
        sp = _get_params_for_store(da_store)
        min_rest = sp.get('min_rest', 12)
        max_continuous = sp.get('max_continuous', 5)
        working_days = sp.get('working_days', 6)
        shift_hours = sp.get('shift_hours', 10)
        night_shift_enabled = sp.get('night_shift_enabled', True)
        
        # Check working days count
        working_count = len(da_shifts[~da_shifts['Is_Day_Off']])
        if working_count > working_days:
            violations.append(f"{da_id}: Works {working_count} days (max {working_days})")
        
        # Check rest between consecutive working shifts
        # (off-days between working days provide guaranteed rest, but we still
        # track the last working shift to validate rest across off-day gaps)
        prev_start = None
        prev_day_idx = None
        prev_was_overnight = False
        for _, shift in da_shifts.iterrows():
            if shift['Is_Day_Off']:
                # Don't reset — keep tracking last working shift for rest calc
                continue
            
            curr_start = int(shift['Shift_Start'])
            curr_day_idx = shift['Day_Index']
            
            # Check overnight when night shift is disabled
            if not night_shift_enabled and curr_start + shift_hours > 24:
                violations.append(f"{da_id} on {shift['Day']}: Overnight shift (start {curr_start:02d}:00) but night shift is OFF")
            
            if prev_start is not None and prev_day_idx is not None:
                prev_end = (prev_start + shift_hours) % 24
                
                # Use engine-compatible rest calculation
                if prev_was_overnight:
                    end_day = (prev_day_idx + 1) % 7
                else:
                    end_day = prev_day_idx
                
                if prev_end == 0:
                    end_day = (end_day + 1) % 7
                    effective_prev_end = 0
                else:
                    effective_prev_end = prev_end
                
                day_gap = (curr_day_idx - end_day) % 7
                
                if day_gap == 0:
                    rest = curr_start - effective_prev_end
                elif day_gap == 1:
                    rest = (24 - effective_prev_end) + curr_start
                else:
                    rest = (24 - effective_prev_end) + (day_gap - 1) * 24 + curr_start
                
                if rest < min_rest:
                    violations.append(f"{da_id} on {shift['Day']}: Only {rest}h rest (min {min_rest}h)")
            
            prev_start = curr_start
            prev_day_idx = curr_day_idx
            prev_was_overnight = (curr_start + shift_hours) >= 24 and ((curr_start + shift_hours) % 24) != 0 and ((curr_start + shift_hours) % 24) < curr_start
        
        # Check wrap-around rest: last working day → first working day (weekly cycle)
        working_shifts = [(idx, s) for idx, s in da_shifts.iterrows() if not s['Is_Day_Off'] and pd.notna(s.get('Shift_Start'))]
        if len(working_shifts) >= 2:
            last_idx, last_shift = working_shifts[-1]
            first_idx, first_shift = working_shifts[0]
            last_start = int(last_shift['Shift_Start'])
            last_end = (last_start + shift_hours) % 24
            last_day_idx = last_shift['Day_Index']
            first_start = int(first_shift['Shift_Start'])
            first_day_idx = first_shift['Day_Index']
            
            # Check wrap-around: from last working day through the weekend to first working day
            last_overnight = (last_end != 0 and last_end < last_start)
            if last_overnight:
                end_day = (last_day_idx + 1) % 7
            else:
                end_day = last_day_idx
            if last_end == 0:
                end_day = (end_day + 1) % 7
                effective_end = 0
            else:
                effective_end = last_end
            day_gap = (first_day_idx + 7 - end_day) % 7
            if day_gap == 0:
                rest = first_start - effective_end
            elif day_gap == 1:
                rest = (24 - effective_end) + first_start
            else:
                rest = (24 - effective_end) + (day_gap - 1) * 24 + first_start
            if rest < min_rest:
                violations.append(f"{da_id} {VALID_DAYS[last_day_idx]}→{VALID_DAYS[first_day_idx]} wrap: Only {rest}h rest (min {min_rest}h)")
        
        # Check break placement (must respect max_continuous rule)
        for _, shift in da_shifts.iterrows():
            if shift['Is_Day_Off'] or pd.isna(shift['Break_Hour']):
                continue
            start = int(shift['Shift_Start'])
            brk = int(shift['Break_Hour'])
            break_hours_param = sp.get('break_hours', 1)
            
            if break_hours_param <= 1:
                # Single break: check segments before and after
                hours_before_break = (brk - start) % 24
                if hours_before_break > shift_hours:
                    hours_before_break = shift_hours
                hours_after_break = shift_hours - hours_before_break - 1
                if hours_after_break < 0:
                    hours_after_break = 0
                
                if hours_before_break > max_continuous:
                    violations.append(f"{da_id} on {shift['Day']}: {hours_before_break}h before break > {max_continuous}h max continuous")
                if hours_after_break > max_continuous:
                    violations.append(f"{da_id} on {shift['Day']}: {hours_after_break}h after break > {max_continuous}h max continuous")
            else:
                # 2 breaks: check all 3 segments
                brk2 = int(shift['Break_Hour_2']) if pd.notna(shift.get('Break_Hour_2')) else None
                if brk2 is not None:
                    breaks_sorted = sorted([(brk - start) % 24, (brk2 - start) % 24])
                    seg1 = breaks_sorted[0]
                    seg2 = breaks_sorted[1] - breaks_sorted[0] - 1
                    seg3 = shift_hours - breaks_sorted[1] - 1
                    for seg_idx, seg_len in enumerate([seg1, seg2, seg3], 1):
                        if seg_len > max_continuous:
                            violations.append(f"{da_id} on {shift['Day']}: segment {seg_idx} is {seg_len}h > {max_continuous}h max continuous")
                else:
                    # Only 1 break provided but 2 expected — check single break
                    hours_before_break = (brk - start) % 24
                    if hours_before_break > shift_hours:
                        hours_before_break = shift_hours
                    hours_after_break = shift_hours - hours_before_break - 1
                    if hours_after_break < 0:
                        hours_after_break = 0
                    if hours_before_break > max_continuous:
                        violations.append(f"{da_id} on {shift['Day']}: {hours_before_break}h before break > {max_continuous}h max continuous")
                    if hours_after_break > max_continuous:
                        violations.append(f"{da_id} on {shift['Day']}: {hours_after_break}h after break > {max_continuous}h max continuous")
    
    return len(violations) == 0, violations

def optimize_break_placement(shifts_df, demand_df, store, params):
    """
    Optimize break placement for each DA shift.
    Breaks can be at hour 4 or 5 of the shift (SACRED RULE).
    For each DA, tries both break positions and keeps the one that reduces total gap.
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0
    
    optimized_shifts = shifts_df.copy()
    store_demand = demand_df[demand_df['Store'] == store].copy()
    shift_hours = params.get('shift_hours', 10)
    max_cont = params.get('max_continuous', 5)
    
    # Day order for overnight shift handling
    DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    day_to_idx = {d: i for i, d in enumerate(DAYS)}
    
    # Helper to calculate total gap for current shifts
    def calc_total_gap(shifts):
        # Build coverage per day/hour
        coverage = {}
        for _, shift in shifts.iterrows():
            if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
                continue
            day = shift['Day']
            start = int(shift['Shift_Start'])
            brk = int(shift['Break_Hour']) if pd.notna(shift['Break_Hour']) else calculate_valid_break_hour(start, shift_hours, max_cont)
            end = int(shift['Shift_End']) if pd.notna(shift['Shift_End']) else (start + shift_hours) % 24
            
            # Check if overnight shift
            is_overnight = end < start
            
            for h in range(shift_hours):
                hour = (start + h) % 24
                if hour == brk:
                    continue
                
                # Determine which day this hour belongs to
                if is_overnight and hour < start:
                    # This hour is on the next day
                    day_idx = day_to_idx.get(day, 0)
                    next_day = DAYS[(day_idx + 1) % 7]
                    key = (next_day, hour)
                else:
                    key = (day, hour)
                coverage[key] = coverage.get(key, 0) + 1
        
        # Calculate gap (demand - coverage, only negative = gap)
        total_gap = 0
        for _, row in store_demand.iterrows():
            day = str(row['Day'])[:3]
            slot = int(row['Slot'])
            required = row['DA Required']
            rostered = coverage.get((day, slot), 0)
            if rostered < required:
                total_gap += (required - rostered)
        
        return total_gap
    
    # Get baseline gap
    baseline_gap = calc_total_gap(optimized_shifts)
    changes_made = 0
    max_continuous = params.get('max_continuous', 5)
    
    # Try each DA one by one
    for idx, shift in optimized_shifts.iterrows():
        if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
            continue
        
        start = int(shift['Shift_Start'])
        current_break = int(shift['Break_Hour']) if pd.notna(shift['Break_Hour']) else calculate_valid_break_hour(start, shift_hours, max_continuous)
        
        # Get ALL valid break positions for this shift
        valid_positions = get_valid_break_positions(start, shift_hours, max_continuous)
        
        if len(valid_positions) <= 1:
            continue  # Only one option, nothing to optimize
        
        # Test each valid position and keep the best
        best_break = current_break
        best_gap = baseline_gap
        
        for brk_pos in valid_positions:
            if brk_pos == current_break:
                continue  # Already tested (it's the baseline)
            
            optimized_shifts.at[idx, 'Break_Hour'] = brk_pos
            test_gap = calc_total_gap(optimized_shifts)
            
            if test_gap < best_gap:
                best_gap = test_gap
                best_break = brk_pos
        
        # Apply the best break position
        if best_break != current_break:
            optimized_shifts.at[idx, 'Break_Hour'] = best_break
            baseline_gap = best_gap
            changes_made += 1
        else:
            # Revert to current
            optimized_shifts.at[idx, 'Break_Hour'] = current_break
    
    return optimized_shifts, changes_made

def optimize_shift_starts(shifts_df, demand_df, store, params):
    """
    Smooth shift starts to reduce coverage spikes/dips.
    Moves DAs to adjacent start times (+/- 1 hour) if it reduces gaps.
    Respects: 12h min rest between shifts, night_shift_enabled setting.
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0
    
    optimized_shifts = shifts_df.copy()
    store_demand = demand_df[demand_df['Store'] == store].copy()
    min_rest = params.get('min_rest', 12)
    shift_hours = params.get('shift_hours', 10)
    night_shift_enabled = params.get('night_shift_enabled', True)
    
    # Build demand lookup
    demand_lookup = {}
    for _, row in store_demand.iterrows():
        day = str(row['Day'])[:3]
        slot = int(row['Slot'])
        demand_lookup[(day, slot)] = row['DA Required']
    
    max_continuous = params.get('max_continuous', 5)
    
    changes_made = 0
    
    # Try to move each DA's shift by +/- 1 hour
    for da_id in optimized_shifts['DA_ID'].unique():
        da_mask = optimized_shifts['DA_ID'] == da_id
        da_shifts = optimized_shifts[da_mask]
        
        for idx, shift in da_shifts.iterrows():
            if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
                continue
            
            current_start = int(shift['Shift_Start'])
            day = shift['Day']
            day_idx = shift['Day_Index']
            
            best_start = current_start
            best_gap_reduction = 0
            
            for delta in [-1, 1]:
                new_start = (current_start + delta) % 24
                
                # NIGHT SHIFT CHECK
                if not night_shift_enabled and new_start + shift_hours > 24:
                    continue
                
                # Check 12h rest rule with previous day
                if day_idx > 0:
                    prev_shift = da_shifts[da_shifts['Day_Index'] == day_idx - 1]
                    if not prev_shift.empty and not prev_shift.iloc[0]['Is_Day_Off']:
                        prev_start = int(prev_shift.iloc[0]['Shift_Start'])
                        prev_end = (prev_start + shift_hours) % 24
                        prev_overnight = prev_start + shift_hours > 24
                        rest = new_start - prev_end if prev_overnight else new_start + 24 - prev_end
                        if rest < min_rest:
                            continue
                
                # Check 12h rest rule with next day
                if day_idx < 6:
                    next_shift = da_shifts[da_shifts['Day_Index'] == day_idx + 1]
                    if not next_shift.empty and not next_shift.iloc[0]['Is_Day_Off']:
                        next_start = int(next_shift.iloc[0]['Shift_Start'])
                        new_end = (new_start + shift_hours) % 24
                        new_overnight = new_start + shift_hours > 24
                        rest = next_start - new_end if new_overnight else next_start + 24 - new_end
                        if rest < min_rest:
                            continue
                

                # Wrap-around: check rest with Saturday when on Sunday
                if day_idx == 0:
                    sat_shift = da_shifts[da_shifts['Day_Index'] == 6]
                    if not sat_shift.empty and not sat_shift.iloc[0]['Is_Day_Off']:
                        prev_start = int(sat_shift.iloc[0]['Shift_Start'])
                        prev_end = (prev_start + shift_hours) % 24
                        sat_overnight = (prev_end != 0 and prev_end < prev_start)
                        if sat_overnight:
                            rest = new_start - prev_end
                        elif prev_end == 0:
                            rest = new_start
                        else:
                            rest = new_start + 24 - prev_end
                        if rest < min_rest:
                            continue

                # Wrap-around: check rest with Sunday when on Saturday
                if day_idx == 6:
                    sun_shift = da_shifts[da_shifts['Day_Index'] == 0]
                    if not sun_shift.empty and not sun_shift.iloc[0]['Is_Day_Off']:
                        new_end = (new_start + shift_hours) % 24
                        new_overnight = (new_end != 0 and new_end < new_start)
                        sun_start = int(sun_shift.iloc[0]['Shift_Start'])
                        if new_overnight:
                            rest = sun_start - new_end
                        elif new_end == 0:
                            rest = sun_start
                        else:
                            rest = sun_start + 24 - new_end
                        if rest < min_rest:
                            continue

                # Calculate gap improvement
                current_demand = demand_lookup.get((day, current_start), 0)
                new_demand = demand_lookup.get((day, new_start), 0)
                gap_reduction = new_demand - current_demand
                
                if gap_reduction > best_gap_reduction:
                    best_gap_reduction = gap_reduction
                    best_start = new_start
            
            if best_start != current_start:
                optimized_shifts.at[idx, 'Shift_Start'] = best_start
                optimized_shifts.at[idx, 'Shift_End'] = (best_start + shift_hours) % 24
                optimized_shifts.at[idx, 'Break_Hour'] = calculate_valid_break_hour(best_start, shift_hours, max_continuous)
                changes_made += 1
    
    return optimized_shifts, changes_made

def optimize_off_days(shifts_df, demand_df, store, params):
    """
    Redistribute off-days to minimize gaps on high-demand days.
    Respects: flexible_day_off setting, 6 working days rule.
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0
    
    optimized_shifts = shifts_df.copy()
    store_demand = demand_df[demand_df['Store'] == store].copy()
    flexible = params.get('flexible_day_off', False)
    min_rest = params.get('min_rest', 12)
    shift_hours = params.get('shift_hours', 10)
    
    # Calculate daily demand totals
    daily_demand = store_demand.groupby('Day')['DA Required'].sum().to_dict()
    
    # Sort days by demand (highest first)
    day_order = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    sorted_days = sorted(day_order, key=lambda d: daily_demand.get(d, 0), reverse=True)
    
    # Allowed off days based on flexible setting
    if flexible:
        allowed_off_days = day_order  # All days allowed
    else:
        allowed_off_days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu']  # Fri/Sat must work
    
    changes_made = 0
    
    for da_id in optimized_shifts['DA_ID'].unique():
        da_mask = optimized_shifts['DA_ID'] == da_id
        da_shifts = optimized_shifts[da_mask].copy()
        
        # Find current off day
        current_off = da_shifts[da_shifts['Is_Day_Off']]['Day'].values
        if len(current_off) == 0:
            continue
        current_off_day = current_off[0]
        
        # Find best off day (lowest demand among allowed days)
        best_off_day = None
        lowest_demand = float('inf')
        
        for day in allowed_off_days:
            day_demand = daily_demand.get(day, 0)
            if day_demand < lowest_demand:
                # Check if moving off day here maintains 12h rest
                day_idx = day_order.index(day)
                
                # Get shifts for adjacent days
                prev_day_idx = (day_idx - 1) % 7
                next_day_idx = (day_idx + 1) % 7
                
                can_move = True
                
                # If we make this day OFF, check rest between prev and next working days
                prev_shift = da_shifts[da_shifts['Day_Index'] == prev_day_idx]
                next_shift = da_shifts[da_shifts['Day_Index'] == next_day_idx]
                
                if not prev_shift.empty and not next_shift.empty:
                    if not prev_shift.iloc[0]['Is_Day_Off'] and not next_shift.iloc[0]['Is_Day_Off']:
                        # Both adjacent days are working - rest is guaranteed by the off day
                        pass
                
                if can_move:
                    lowest_demand = day_demand
                    best_off_day = day
        
        if best_off_day and best_off_day != current_off_day:
            # Move off day
            current_off_idx = day_order.index(current_off_day)
            new_off_idx = day_order.index(best_off_day)
            
            # Get the shift that was on the new off day (to copy to old off day)
            new_off_shift = da_shifts[da_shifts['Day'] == best_off_day].iloc[0]
            
            # Update: make new day OFF
            for idx in optimized_shifts[da_mask & (optimized_shifts['Day'] == best_off_day)].index:
                optimized_shifts.at[idx, 'Is_Day_Off'] = True
                optimized_shifts.at[idx, 'Shift_Start'] = None
                optimized_shifts.at[idx, 'Shift_End'] = None
                optimized_shifts.at[idx, 'Break_Hour'] = None
            
            # Update: make old off day WORKING (copy shift from a working day)
            working_shift = da_shifts[~da_shifts['Is_Day_Off']].iloc[0]
            new_start = working_shift['Shift_Start']
            
            # Night shift check: don't assign overnight shift when disabled
            night_enabled = params.get('night_shift_enabled', True)
            if not night_enabled and new_start is not None and int(new_start) + shift_hours >= 24:
                continue  # Skip this DA, would create overnight
            
            for idx in optimized_shifts[da_mask & (optimized_shifts['Day'] == current_off_day)].index:
                optimized_shifts.at[idx, 'Is_Day_Off'] = False
                optimized_shifts.at[idx, 'Shift_Start'] = working_shift['Shift_Start']
                optimized_shifts.at[idx, 'Shift_End'] = working_shift['Shift_End']
                optimized_shifts.at[idx, 'Break_Hour'] = working_shift['Break_Hour']
            
            changes_made += 1
    
    # Validate no sacred rule violations
    is_valid, violations = validate_sacred_rules(optimized_shifts, params)
    if not is_valid:
        return shifts_df, 0  # Revert if violations
    
    return optimized_shifts, changes_made

def optimize_overnight_balance(shifts_df, demand_df, store, params):
    """
    Balance overnight shift distribution to match night demand (00:00-06:00).
    Moves some day shifts to overnight if night is under-covered.
    Respects: 12h min rest, night_shift_enabled setting.
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0
    
    if not params.get('night_shift_enabled', True):
        return shifts_df, 0  # Night shifts disabled
    
    optimized_shifts = shifts_df.copy()
    store_demand = demand_df[demand_df['Store'] == store].copy()
    min_rest = params.get('min_rest', 12)
    shift_hours = params.get('shift_hours', 10)
    
    # Build demand lookup
    demand_lookup = {}
    for _, row in store_demand.iterrows():
        day = str(row['Day'])[:3]
        slot = int(row['Slot'])
        demand_lookup[(day, slot)] = row['DA Required']
    
    # Calculate overnight demand vs coverage
    # Overnight hours = hours from midnight to shift_hours past midnight (dynamic)
    overnight_hours = list(range(0, min(shift_hours, 7)))
    
    def calc_overnight_gap():
        overnight_coverage = {day: 0 for day in DAYS}
        overnight_demand = {day: 0 for day in DAYS}
        
        for day in DAYS:
            for h in overnight_hours:
                overnight_demand[day] += demand_lookup.get((day, h), 0)
        
        for _, shift in optimized_shifts.iterrows():
            if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
                continue
            start = int(shift['Shift_Start'])
            # Check if shift covers overnight
            if start >= 20 or start <= 2:  # Overnight shift
                day = shift['Day']
                overnight_coverage[day] += len([h for h in overnight_hours if h in range(start, start + shift_hours) or h in range(0, (start + shift_hours) % 24)])
        
        total_gap = sum(overnight_demand[d] - overnight_coverage[d] for d in DAYS)
        return total_gap
    
    changes_made = 0
    overnight_starts = [20, 21, 22, 23, 0, 1, 2]  # Valid overnight start times
    
    # Find DAs with day shifts that could move to overnight
    for da_id in optimized_shifts['DA_ID'].unique():
        da_mask = optimized_shifts['DA_ID'] == da_id
        da_shifts = optimized_shifts[da_mask]
        
        for idx, shift in da_shifts.iterrows():
            if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
                continue
            
            current_start = int(shift['Shift_Start'])
            
            # Skip if already overnight
            if current_start in overnight_starts:
                continue
            
            day = shift['Day']
            day_idx = shift['Day_Index']
            
            # Try moving to overnight
            for new_start in overnight_starts:
                # Check 12h rest with adjacent days
                valid = True
                
                if day_idx > 0:
                    prev_shift = da_shifts[da_shifts['Day_Index'] == day_idx - 1]
                    if not prev_shift.empty and not prev_shift.iloc[0]['Is_Day_Off']:
                        prev_start = int(prev_shift.iloc[0]['Shift_Start'])
                        prev_end = (prev_start + shift_hours) % 24
                        prev_overnight = prev_start + shift_hours > 24
                        rest = new_start - prev_end if prev_overnight else new_start + 24 - prev_end
                        if rest < min_rest:
                            valid = False
                
                if day_idx < 6 and valid:
                    next_shift = da_shifts[da_shifts['Day_Index'] == day_idx + 1]
                    if not next_shift.empty and not next_shift.iloc[0]['Is_Day_Off']:
                        next_start = int(next_shift.iloc[0]['Shift_Start'])
                        new_end = (new_start + shift_hours) % 24
                        new_overnight = new_start + shift_hours > 24
                        rest = next_start - new_end if new_overnight else next_start + 24 - new_end
                        if rest < min_rest:
                            valid = False
                
                if valid:
                    # Check if this improves overnight coverage
                    overnight_demand_at_start = sum(demand_lookup.get((day, h), 0) for h in overnight_hours)
                    day_demand_at_current = demand_lookup.get((day, current_start), 0)
                    
                    if overnight_demand_at_start > day_demand_at_current * 1.2:  # 20% threshold
                        optimized_shifts.at[idx, 'Shift_Start'] = new_start
                        optimized_shifts.at[idx, 'Shift_End'] = (new_start + shift_hours) % 24
                        max_continuous = params.get('max_continuous', 5)
                        optimized_shifts.at[idx, 'Break_Hour'] = calculate_valid_break_hour(new_start, shift_hours, max_continuous)
                        changes_made += 1
                        break
            
            if changes_made >= 5:  # Limit changes per run
                break
        
        if changes_made >= 5:
            break
    
    return optimized_shifts, changes_made

def optimize_gap_filling(shifts_df, demand_df, store, params):
    """
    Gap-Priority Shift Movement Optimizer.
    Identifies hours with gaps (sorted by severity) and moves nearby DA shifts to cover them.
    Uses multiple passes for better results.
    
    Respects SACRED RULES:
    - 12h minimum rest between shifts
    - Break at hour 4 or 5 of shift
    - No zero-coverage slots created
    - No overnight shifts when night_shift_enabled=False
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0
    
    optimized_shifts = shifts_df.copy()
    store_demand = demand_df[demand_df['Store'] == store].copy()
    min_rest = params.get('min_rest', 12)
    shift_hours = params.get('shift_hours', 10)
    night_shift_enabled = params.get('night_shift_enabled', True)
    max_continuous = params.get('max_continuous', 5)
    
    # Build demand lookup
    demand_lookup = {}
    for _, row in store_demand.iterrows():
        day = str(row['Day'])[:3]
        slot = int(row['Slot'])
        demand_lookup[(day, slot)] = row['DA Required']
    
    def calc_coverage(shifts):
        """Calculate coverage per day/hour."""
        coverage = {day: {h: 0 for h in range(24)} for day in DAYS}
        for _, shift in shifts.iterrows():
            if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
                continue
            day = shift['Day']
            start = int(shift['Shift_Start'])
            brk = int(shift['Break_Hour']) if pd.notna(shift['Break_Hour']) else calculate_valid_break_hour(start, shift_hours, max_continuous)
            for h in range(shift_hours + 1):
                hour = (start + h) % 24
                if hour != brk:
                    coverage[day][hour] += 1
        return coverage
    
    def calc_gap_and_zeros(coverage):
        """Calculate total gap and zero-coverage slots."""
        total_gap = 0
        zero_slots = 0
        for day in DAYS:
            for h in range(24):
                demand = demand_lookup.get((day, h), 0)
                cov = coverage[day][h]
                if cov < demand:
                    total_gap += (demand - cov)
                if cov == 0 and demand > 0:
                    zero_slots += 1
        return total_gap, zero_slots
    
    def find_gaps(coverage):
        """Find all gap hours sorted by severity (biggest first)."""
        gaps = []
        for day in DAYS:
            for h in range(24):
                demand = demand_lookup.get((day, h), 0)
                cov = coverage[day][h]
                if cov < demand:
                    gaps.append({
                        'day': day, 
                        'hour': h, 
                        'gap': demand - cov, 
                        'day_idx': DAYS.index(day)
                    })
        return sorted(gaps, key=lambda x: -x['gap'])
    
    def check_rest_valid(da_shifts, day_idx, new_start):
        """Check if new start time respects 12h rest rule (handles overnight shifts correctly)."""
        # Check with previous day
        if day_idx > 0:
            prev = da_shifts[da_shifts['Day_Index'] == day_idx - 1]
            if not prev.empty and not prev.iloc[0]['Is_Day_Off']:
                prev_start = int(prev.iloc[0]['Shift_Start'])
                prev_end = (prev_start + shift_hours) % 24
                prev_is_overnight = prev_start + shift_hours > 24
                
                if prev_is_overnight:
                    # Previous shift ended on current day morning
                    rest = new_start - prev_end
                else:
                    # Previous shift ended on previous day
                    rest = new_start + 24 - prev_end
                
                if rest < min_rest:
                    return False
        
        # Check with next day
        if day_idx < 6:
            nxt = da_shifts[da_shifts['Day_Index'] == day_idx + 1]
            if not nxt.empty and not nxt.iloc[0]['Is_Day_Off']:
                new_end = (new_start + shift_hours) % 24
                new_is_overnight = new_start + shift_hours > 24
                nxt_start = int(nxt.iloc[0]['Shift_Start'])
                
                if new_is_overnight:
                    # New shift ends on next day morning
                    rest = nxt_start - new_end
                else:
                    # New shift ends on current day
                    rest = nxt_start + 24 - new_end
                
                if rest < min_rest:
                    return False
        

        # Wrap-around: Saturday → Sunday
        if day_idx == 0:
            sat = da_shifts[da_shifts['Day_Index'] == 6]
            if not sat.empty and not sat.iloc[0]['Is_Day_Off']:
                prev_start = int(sat.iloc[0]['Shift_Start'])
                prev_end = (prev_start + shift_hours) % 24
                sat_overnight = (prev_end != 0 and prev_end < prev_start)
                if sat_overnight:
                    rest = new_start - prev_end
                elif prev_end == 0:
                    rest = new_start
                else:
                    rest = new_start + 24 - prev_end
                if rest < min_rest:
                    return False
        if day_idx == 6:
            sun = da_shifts[da_shifts['Day_Index'] == 0]
            if not sun.empty and not sun.iloc[0]['Is_Day_Off']:
                new_end = (new_start + shift_hours) % 24
                new_overnight = (new_end != 0 and new_end < new_start)
                sun_start = int(sun.iloc[0]['Shift_Start'])
                if new_overnight:
                    rest = sun_start - new_end
                elif new_end == 0:
                    rest = sun_start
                else:
                    rest = sun_start + 24 - new_end
                if rest < min_rest:
                    return False

        return True
    
    changes_made = 0
    
    # Multiple passes for better optimization
    for iteration in range(5):
        cov = calc_coverage(optimized_shifts)
        gaps = find_gaps(cov)
        
        if not gaps:
            break  # No more gaps
        
        iteration_changes = 0
        
        # Process top gaps (biggest first)
        for gap_info in gaps[:5]:
            gap_day = gap_info['day']
            gap_hour = gap_info['hour']
            day_idx = gap_info['day_idx']
            
            best_da = None
            best_idx = None
            best_new_start = None
            
            # Find DAs who could cover this gap
            for da_id in optimized_shifts['DA_ID'].unique():
                da_mask = optimized_shifts['DA_ID'] == da_id
                da_shifts = optimized_shifts[da_mask].sort_values('Day_Index')
                
                day_shift = da_shifts[da_shifts['Day'] == gap_day]
                if day_shift.empty or day_shift.iloc[0]['Is_Day_Off']:
                    continue
                
                shift = day_shift.iloc[0]
                idx = day_shift.index[0]
                current_start = int(shift['Shift_Start'])
                max_continuous = params.get('max_continuous', 5)
                
                # Check if shift already covers gap hour
                brk = int(shift['Break_Hour']) if pd.notna(shift['Break_Hour']) else calculate_valid_break_hour(current_start, shift_hours, max_continuous)
                covered = [(current_start + h) % 24 for h in range(shift_hours + 1) if (current_start + h) % 24 != brk]
                
                if gap_hour in covered:
                    continue  # Already covers this gap
                
                # Try shifts that would cover the gap (+/- 1-2 hours)
                for delta in [-1, 1, -2, 2]:
                    new_start = (current_start + delta) % 24
                    
                    # NIGHT SHIFT CHECK - don't create overnight shifts when disabled
                    if not night_shift_enabled:
                        if new_start + shift_hours > 24:  # Would be overnight
                            continue
                    
                    new_brk = calculate_valid_break_hour(new_start, shift_hours, max_continuous)
                    new_covered = [(new_start + h) % 24 for h in range(shift_hours + 1) if (new_start + h) % 24 != new_brk]
                    
                    if gap_hour not in new_covered:
                        continue
                    
                    # Check rest rules (with correct overnight handling)
                    if not check_rest_valid(da_shifts, day_idx, new_start):
                        continue
                    
                    # Test the change - ensure no zeros created
                    optimized_shifts.at[idx, 'Shift_Start'] = new_start
                    optimized_shifts.at[idx, 'Shift_End'] = (new_start + shift_hours) % 24
                    optimized_shifts.at[idx, 'Break_Hour'] = new_brk
                    
                    cov_test = calc_coverage(optimized_shifts)
                    _, zeros_test = calc_gap_and_zeros(cov_test)
                    
                    if zeros_test == 0:
                        best_da = da_id
                        best_idx = idx
                        best_new_start = new_start
                    
                    # Revert for now
                    optimized_shifts.at[idx, 'Shift_Start'] = current_start
                    optimized_shifts.at[idx, 'Shift_End'] = (current_start + shift_hours) % 24
                    optimized_shifts.at[idx, 'Break_Hour'] = calculate_valid_break_hour(current_start, shift_hours, max_continuous)
                    
                    if best_da:
                        break
                
                if best_da:
                    break
            
            # Apply the best change found
            if best_da:
                optimized_shifts.at[best_idx, 'Shift_Start'] = best_new_start
                optimized_shifts.at[best_idx, 'Shift_End'] = (best_new_start + shift_hours) % 24
                optimized_shifts.at[best_idx, 'Break_Hour'] = calculate_valid_break_hour(best_new_start, shift_hours, max_continuous)
                changes_made += 1
                iteration_changes += 1
        
        if iteration_changes == 0:
            break  # No more improvements possible
    
    return optimized_shifts, changes_made

def optimize_excess_redistribution(shifts_df, demand_df, store, params):
    """
    Move DAs from excess hours to gap hours (allows bigger moves than Gap-Priority).
    Especially effective when night shifts are disabled.
    
    Respects SACRED RULES:
    - 12h minimum rest between shifts
    - No overnight shifts when night_shift_enabled=False
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0
    
    optimized = shifts_df.copy()
    store_demand = demand_df[demand_df['Store'] == store]
    min_rest = params.get('min_rest', 12)
    shift_hours = params.get('shift_hours', 10)
    night_enabled = params.get('night_shift_enabled', True)
    
    demand_lookup = {(str(r['Day'])[:3], int(r['Slot'])): r['DA Required'] 
                     for _, r in store_demand.iterrows()}
    
    max_cont = params.get('max_continuous', 5)
    
    def calc_cov(shifts):
        cov = {d: {h: 0 for h in range(24)} for d in DAYS}
        for _, s in shifts.iterrows():
            if s['Is_Day_Off'] or pd.isna(s['Shift_Start']): continue
            start = int(s['Shift_Start'])
            brk = int(s['Break_Hour']) if pd.notna(s['Break_Hour']) else calculate_valid_break_hour(start, shift_hours, max_cont)
            for h in range(shift_hours+1):
                hr = (start+h)%24
                if hr != brk: cov[s['Day']][hr] += 1
        return cov
    
    def find_excess_and_gaps(cov):
        excess, gaps = [], []
        for d in DAYS:
            for h in range(24):
                dem = demand_lookup.get((d,h),0)
                diff = cov[d][h] - dem
                if diff > 0:
                    excess.append({'day':d,'hour':h,'excess':diff,'day_idx':DAYS.index(d)})
                elif diff < 0:
                    gaps.append({'day':d,'hour':h,'gap':-diff,'day_idx':DAYS.index(d)})
        return sorted(excess, key=lambda x:-x['excess']), sorted(gaps, key=lambda x:-x['gap'])
    
    def rest_valid(da_shifts, day_idx, new_start):
        if day_idx > 0:
            prev = da_shifts[da_shifts['Day_Index']==day_idx-1]
            if not prev.empty and not prev.iloc[0]['Is_Day_Off']:
                ps = int(prev.iloc[0]['Shift_Start'])
                pe = (ps+shift_hours)%24
                rest = new_start-pe if ps+shift_hours>=24 else new_start+24-pe
                if rest < min_rest: return False
        if day_idx < 6:
            nxt = da_shifts[da_shifts['Day_Index']==day_idx+1]
            if not nxt.empty and not nxt.iloc[0]['Is_Day_Off']:
                ne = (new_start+shift_hours)%24
                ns = int(nxt.iloc[0]['Shift_Start'])
                rest = ns-ne if new_start+shift_hours>=24 else ns+24-ne
                if rest < min_rest: return False

        # Wrap-around: Saturday → Sunday
        if day_idx == 0:
            sat = da_shifts[da_shifts['Day_Index'] == 6]
            if not sat.empty and not sat.iloc[0]['Is_Day_Off']:
                ps = int(sat.iloc[0]['Shift_Start'])
                pe = (ps + shift_hours) % 24
                sat_overnight = (pe != 0 and pe < ps)
                if sat_overnight:
                    rest = new_start - pe
                elif pe == 0:
                    rest = new_start
                else:
                    rest = new_start + 24 - pe
                if rest < min_rest:
                    return False
        if day_idx == 6:
            sun = da_shifts[da_shifts['Day_Index'] == 0]
            if not sun.empty and not sun.iloc[0]['Is_Day_Off']:
                new_end = (new_start + shift_hours) % 24
                new_overnight = (new_end != 0 and new_end < new_start)
                sun_start = int(sun.iloc[0]['Shift_Start'])
                if new_overnight:
                    rest = sun_start - new_end
                elif new_end == 0:
                    rest = sun_start
                else:
                    rest = sun_start + 24 - new_end
                if rest < min_rest:
                    return False

        return True
    
    changes = 0
    for _ in range(10):
        cov = calc_cov(optimized)
        excess_list, gap_list = find_excess_and_gaps(cov)
        if not gap_list or not excess_list: break
        
        made_change = False
        for gap in gap_list[:3]:
            gap_day, gap_hour, day_idx = gap['day'], gap['hour'], gap['day_idx']
            
            for exc in excess_list:
                if exc['day'] != gap_day: continue
                
                for da_id in optimized['DA_ID'].unique():
                    da_mask = optimized['DA_ID']==da_id
                    da_shifts = optimized[da_mask].sort_values('Day_Index')
                    day_shift = da_shifts[da_shifts['Day']==gap_day]
                    if day_shift.empty or day_shift.iloc[0]['Is_Day_Off']: continue
                    
                    shift = day_shift.iloc[0]
                    idx = day_shift.index[0]
                    curr = int(shift['Shift_Start'])
                    if curr != exc['hour']: continue
                    
                    max_cont = params.get('max_continuous', 5)
                    for offset in range(shift_hours+1):
                        new_start = (gap_hour - offset) % 24
                        if not night_enabled and new_start+shift_hours>=24: continue
                        
                        new_brk = calculate_valid_break_hour(new_start, shift_hours, max_cont)
                        new_cov = [(new_start+h)%24 for h in range(shift_hours+1) if (new_start+h)%24!=new_brk]
                        if gap_hour not in new_cov: continue
                        if not rest_valid(da_shifts, day_idx, new_start): continue
                        
                        optimized.at[idx,'Shift_Start'] = new_start
                        optimized.at[idx,'Shift_End'] = (new_start+shift_hours)%24
                        optimized.at[idx,'Break_Hour'] = new_brk
                        changes += 1
                        made_change = True
                        break
                    if made_change: break
                if made_change: break
            if made_change: break
        if not made_change: break
    
    return optimized, changes

def optimize_day_specific(shifts_df, demand_df, store, params):
    """
    Optimize only the worst 3 days by gap. Allows bigger moves (±3 hours).
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0
    
    optimized = shifts_df.copy()
    store_demand = demand_df[demand_df['Store'] == store]
    min_rest = params.get('min_rest', 12)
    shift_hours = params.get('shift_hours', 10)
    night_enabled = params.get('night_shift_enabled', True)
    max_cont = params.get('max_continuous', 5)
    
    demand_lookup = {(str(r['Day'])[:3], int(r['Slot'])): r['DA Required'] 
                     for _, r in store_demand.iterrows()}
    
    def calc_cov(shifts):
        cov = {d: {h: 0 for h in range(24)} for d in DAYS}
        for _, s in shifts.iterrows():
            if s['Is_Day_Off'] or pd.isna(s['Shift_Start']): continue
            start = int(s['Shift_Start'])
            brk = int(s['Break_Hour']) if pd.notna(s['Break_Hour']) else calculate_valid_break_hour(start, shift_hours, max_cont)
            for h in range(shift_hours+1):
                hr = (start+h)%24
                if hr != brk: cov[s['Day']][hr] += 1
        return cov
    
    # Find worst 3 days
    cov = calc_cov(optimized)
    day_gaps = {}
    for d in DAYS:
        day_gap = sum(max(0, demand_lookup.get((d,h),0) - cov[d][h]) for h in range(24))
        day_gaps[d] = day_gap
    target_days = sorted(day_gaps.keys(), key=lambda x:-day_gaps[x])[:3]
    
    def rest_valid(da_shifts, day_idx, new_start):
        if day_idx > 0:
            prev = da_shifts[da_shifts['Day_Index']==day_idx-1]
            if not prev.empty and not prev.iloc[0]['Is_Day_Off']:
                ps = int(prev.iloc[0]['Shift_Start'])
                pe = (ps+shift_hours)%24
                rest = new_start-pe if ps+shift_hours>=24 else new_start+24-pe
                if rest < min_rest: return False
        if day_idx < 6:
            nxt = da_shifts[da_shifts['Day_Index']==day_idx+1]
            if not nxt.empty and not nxt.iloc[0]['Is_Day_Off']:
                ne = (new_start+shift_hours)%24
                ns = int(nxt.iloc[0]['Shift_Start'])
                rest = ns-ne if new_start+shift_hours>=24 else ns+24-ne
                if rest < min_rest: return False

        # Wrap-around: Saturday → Sunday
        if day_idx == 0:
            sat = da_shifts[da_shifts['Day_Index'] == 6]
            if not sat.empty and not sat.iloc[0]['Is_Day_Off']:
                ps = int(sat.iloc[0]['Shift_Start'])
                pe = (ps + shift_hours) % 24
                sat_overnight = (pe != 0 and pe < ps)
                if sat_overnight:
                    rest = new_start - pe
                elif pe == 0:
                    rest = new_start
                else:
                    rest = new_start + 24 - pe
                if rest < min_rest:
                    return False
        if day_idx == 6:
            sun = da_shifts[da_shifts['Day_Index'] == 0]
            if not sun.empty and not sun.iloc[0]['Is_Day_Off']:
                new_end = (new_start + shift_hours) % 24
                new_overnight = (new_end != 0 and new_end < new_start)
                sun_start = int(sun.iloc[0]['Shift_Start'])
                if new_overnight:
                    rest = sun_start - new_end
                elif new_end == 0:
                    rest = sun_start
                else:
                    rest = sun_start + 24 - new_end
                if rest < min_rest:
                    return False

        return True
    
    changes = 0
    for _ in range(5):
        cov = calc_cov(optimized)
        gaps = []
        for d in target_days:
            for h in range(24):
                dem = demand_lookup.get((d,h),0)
                if cov[d][h] < dem:
                    gaps.append({'day':d,'hour':h,'gap':dem-cov[d][h],'day_idx':DAYS.index(d)})
        gaps = sorted(gaps, key=lambda x:-x['gap'])
        if not gaps: break
        
        made_change = False
        max_cont = params.get('max_continuous', 5)
        for g in gaps[:5]:
            for da_id in optimized['DA_ID'].unique():
                da_mask = optimized['DA_ID']==da_id
                da_shifts = optimized[da_mask].sort_values('Day_Index')
                day_shift = da_shifts[da_shifts['Day']==g['day']]
                if day_shift.empty or day_shift.iloc[0]['Is_Day_Off']: continue
                
                shift = day_shift.iloc[0]
                idx = day_shift.index[0]
                curr = int(shift['Shift_Start'])
                brk = int(shift['Break_Hour']) if pd.notna(shift['Break_Hour']) else calculate_valid_break_hour(curr, shift_hours, max_cont)
                covered = [(curr+h)%24 for h in range(shift_hours+1) if (curr+h)%24!=brk]
                if g['hour'] in covered: continue
                
                for delta in [-1,1,-2,2,-3,3]:
                    ns = (curr+delta)%24
                    if not night_enabled and ns+shift_hours>=24: continue
                    nb = calculate_valid_break_hour(ns, shift_hours, max_cont)
                    nc = [(ns+h)%24 for h in range(shift_hours+1) if (ns+h)%24!=nb]
                    if g['hour'] not in nc: continue
                    if not rest_valid(da_shifts, g['day_idx'], ns): continue
                    
                    optimized.at[idx,'Shift_Start'] = ns
                    optimized.at[idx,'Shift_End'] = (ns+shift_hours)%24
                    optimized.at[idx,'Break_Hour'] = nb
                    changes += 1
                    made_change = True
                    break
                if made_change: break
            if made_change: break
        if not made_change: break
    
    return optimized, changes

def calc_total_gap_fast(shifts_df, demand_df, store, params):
    """
    Fast gap calculation for optimizer iterations.
    Must match engine's generate_hourly_roster logic exactly.
    """
    shift_hours = params.get('shift_hours', 10)
    max_cont = params.get('max_continuous', 5)
    store_demand = demand_df[demand_df['Store'] == store]
    demand_lookup = {(str(r['Day'])[:3], int(r['Slot'])): r['DA Required'] 
                     for _, r in store_demand.iterrows()}
    
    coverage = {d: {h: 0 for h in range(24)} for d in DAYS}
    
    for _, s in shifts_df.iterrows():
        if s['Is_Day_Off'] or pd.isna(s['Shift_Start']): continue
        day = s['Day']
        day_idx = DAYS.index(day)
        start = int(s['Shift_Start'])
        brk = int(s['Break_Hour']) if pd.notna(s['Break_Hour']) else calculate_valid_break_hour(start, shift_hours, max_cont)
        end = (start + shift_hours) % 24
        is_overnight = (end != 0 and end < start)  # Match engine's _is_overnight logic
        
        # Engine covers exactly shift_hours slots (start to start+shift_hours-1)
        # Break takes one slot but is still within the span
        for h in range(shift_hours):
            hr = (start + h) % 24
            if hr == brk: continue
            
            if is_overnight and hr < start:
                next_day = DAYS[(day_idx + 1) % 7]
                coverage[next_day][hr] += 1
            else:
                coverage[day][hr] += 1
    
    gap = sum(max(0, demand_lookup.get((d,h),0) - coverage[d][h]) 
              for d in DAYS for h in range(24))
    return gap

def calc_total_gap(shifts_df, demand_df, store, params):
    """
    Calculate total gap using the engine's roster generation for accuracy.
    This ensures the gap matches what's displayed in the UI.
    """
    from roster_engine_v12_2 import generate_hourly_roster as engine_gen_roster
    
    store_demand = demand_df[demand_df['Store'] == store]
    roster = engine_gen_roster(shifts_df, store_demand, params)
    
    # Gap is sum of negative Diff values (where rostered < required)
    gap = abs(roster[roster['Diff'] < 0]['Diff'].sum())
    return int(gap)

def count_violations(shifts_df, params):
    """Count ALL violations in shifts (rest, breaks, max_continuous, overnight)."""
    shift_hours = params.get('shift_hours', 10)
    break_hours_dur = params.get('break_hours', 1)
    min_rest = params.get('min_rest', 12)
    max_continuous = params.get('max_continuous', 5)
    night_shift_enabled = params.get('night_shift_enabled', True)
    violations = 0
    
    for da_id in shifts_df['DA_ID'].unique():
        da_shifts = shifts_df[shifts_df['DA_ID'] == da_id].sort_values('Day_Index')
        prev_start, prev_day_idx = None, None
        prev_overnight = False
        
        for _, shift in da_shifts.iterrows():
            if shift['Is_Day_Off'] or pd.isna(shift.get('Shift_Start')):
                # Don't reset — keep tracking last working shift for rest calc
                continue
            curr_start = int(shift['Shift_Start'])
            curr_day_idx = shift['Day_Index']
            
            # Check overnight violation
            if not night_shift_enabled and curr_start + shift_hours > 24:
                violations += 1
            
            # Check break violations
            brk = shift.get('Break_Hour')
            if pd.isna(brk) and shift_hours > max_continuous:
                violations += 1  # Missing break
            elif not pd.isna(brk):
                brk = int(brk)
                brk2 = shift.get('Break_Hour_2')
                if break_hours_dur >= 2 and pd.notna(brk2):
                    brk2 = int(brk2)
                    breaks_sorted = sorted([(brk - curr_start) % 24, (brk2 - curr_start) % 24])
                    seg1 = breaks_sorted[0]
                    seg2 = breaks_sorted[1] - breaks_sorted[0] - 1
                    seg3 = shift_hours - breaks_sorted[1] - 1
                    for seg_len in [seg1, seg2, seg3]:
                        if seg_len > max_continuous:
                            violations += 1
                else:
                    hours_before = (brk - curr_start) % 24
                    if hours_before > shift_hours:
                        hours_before = shift_hours
                    hours_after = shift_hours - hours_before - 1
                    if hours_before > max_continuous:
                        violations += 1
                    if hours_after > max_continuous:
                        violations += 1
            
            # Check rest using engine-compatible logic
            if prev_start is not None and prev_day_idx is not None:
                prev_end = (prev_start + shift_hours) % 24
                
                if prev_overnight:
                    end_day = (prev_day_idx + 1) % 7
                else:
                    end_day = prev_day_idx
                
                if prev_end == 0:
                    end_day = (end_day + 1) % 7
                    effective_prev_end = 0
                else:
                    effective_prev_end = prev_end
                
                day_gap = (curr_day_idx - end_day) % 7
                
                if day_gap == 0:
                    rest = curr_start - effective_prev_end
                elif day_gap == 1:
                    rest = (24 - effective_prev_end) + curr_start
                else:
                    rest = (24 - effective_prev_end) + (day_gap - 1) * 24 + curr_start
                
                if rest < min_rest:
                    violations += 1
            
            prev_start = curr_start
            prev_day_idx = curr_day_idx
            prev_overnight = (curr_start + shift_hours) >= 24 and ((curr_start + shift_hours) % 24) != 0 and ((curr_start + shift_hours) % 24) < curr_start
        
        # Check wrap-around rest: last working day → first working day
        working_shifts = [(idx, s) for idx, s in da_shifts.iterrows() if not s['Is_Day_Off'] and pd.notna(s.get('Shift_Start'))]
        if len(working_shifts) >= 2:
            last_idx, last_shift = working_shifts[-1]
            first_idx, first_shift = working_shifts[0]
            last_start = int(last_shift['Shift_Start'])
            last_end = (last_start + shift_hours) % 24
            last_day_idx = last_shift['Day_Index']
            first_start = int(first_shift['Shift_Start'])
            first_day_idx = first_shift['Day_Index']
            last_overnight = (last_end != 0 and last_end < last_start)
            if last_overnight:
                end_day = (last_day_idx + 1) % 7
            else:
                end_day = last_day_idx
            if last_end == 0:
                end_day = (end_day + 1) % 7
                effective_end = 0
            else:
                effective_end = last_end
            day_gap = (first_day_idx + 7 - end_day) % 7
            if day_gap == 0:
                rest = first_start - effective_end
            elif day_gap == 1:
                rest = (24 - effective_end) + first_start
            else:
                rest = (24 - effective_end) + (day_gap - 1) * 24 + first_start
            if rest < min_rest:
                violations += 1
    
    return violations

def optimize_smart_chain(shifts_df, demand_df, store, params):
    """
    Smart Chain Optimizer - runs multiple optimizers in sequence until no improvement.
    Runs: Gap-Priority → Excess-Redistribution → Day-Specific
    Keeps only improvements with 0 violations.
    Repeats until no improvement for 5 consecutive runs.
    
    Returns: (optimized_shifts, total_changes, iterations_run)
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0, 0
    
    best_shifts = shifts_df.copy()
    # Use fast gap for internal optimization iterations
    best_gap = calc_total_gap_fast(best_shifts, demand_df, store, params)
    total_changes = 0
    iterations_run = 0
    no_improvement_count = 0
    max_no_improvement = 5
    
    optimizers = [
        ('Gap-Priority', optimize_gap_filling),
        ('Excess-Redistribution', optimize_excess_redistribution),
        ('Day-Specific', optimize_day_specific),
    ]
    
    # Count baseline violations (pre-existing, e.g. overnight when night ON)
    baseline_violations = count_violations(best_shifts, params)
    
    while no_improvement_count < max_no_improvement:
        iterations_run += 1
        iteration_improved = False
        
        for name, opt_func in optimizers:
            test_shifts, changes = opt_func(best_shifts.copy(), demand_df, store, params)
            test_gap = calc_total_gap_fast(test_shifts, demand_df, store, params)
            test_violations = count_violations(test_shifts, params)
            
            # Only keep if gap improved and no NEW violations added
            if test_gap < best_gap and test_violations <= baseline_violations:
                best_shifts = test_shifts
                best_gap = test_gap
                total_changes += changes
                iteration_improved = True
        
        if iteration_improved:
            no_improvement_count = 0
        else:
            no_improvement_count += 1
        
        # Safety limit
        if iterations_run >= 50:
            break
    
    return best_shifts, total_changes, iterations_run

def calculate_optimization_impact(original_shifts, optimized_shifts, demand_df, store, params):
    """Calculate the impact of any optimization on coverage."""
    from roster_engine_v12_2 import generate_hourly_roster as engine_generate_hourly_roster
    
    store_demand = demand_df[demand_df['Store'] == store]
    
    original_roster = engine_generate_hourly_roster(original_shifts, store_demand, params)
    original_gap = abs(original_roster[original_roster['Diff'] < 0]['Diff'].sum())
    original_excess = original_roster[original_roster['Diff'] > 0]['Diff'].sum()
    
    optimized_roster = engine_generate_hourly_roster(optimized_shifts, store_demand, params)
    optimized_gap = abs(optimized_roster[optimized_roster['Diff'] < 0]['Diff'].sum())
    optimized_excess = optimized_roster[optimized_roster['Diff'] > 0]['Diff'].sum()
    
    return {
        'original_gap': original_gap,
        'optimized_gap': optimized_gap,
        'new_gap': optimized_gap,
        'gap_improvement': original_gap - optimized_gap,
        'original_excess': original_excess,
        'optimized_excess': optimized_excess,
        'excess_change': optimized_excess - original_excess,
        'improvement_pct': ((original_gap - optimized_gap) / original_gap * 100) if original_gap > 0 else 0
    }

# =============================================================================
# LM CAP GENERATION
# =============================================================================

def generate_lm_cap(demand_df, das_df, params, uploaded_file, selected_week, stores=None):
    """
    Generate LM Cap report: Store × Slot × Day with Rostered DAs, DPH, and Max Orders.
    
    Returns: DataFrame matching LM Cap format, or None if error.
    """
    DAYS_ORDER = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    
    # Get DPH per store/day/slot from uploaded file
    slot_dph = {}  # {(store, day, slot): dph}
    store_avg_dph = {}  # fallback
    try:
        uploaded_file.seek(0)
        # Try slot-level DPH first
        try:
            dph_df = pd.read_excel(uploaded_file, sheet_name='Daily slot level DPH')
            if 'Softened DPH' in dph_df.columns:
                for _, row in dph_df.iterrows():
                    store = row.get('Store')
                    day = str(row.get('Weekday', ''))[:3]
                    slot = row.get('Slot')
                    dph_val = row.get('Softened DPH')
                    if pd.notna(store) and pd.notna(slot) and pd.notna(dph_val) and day in DAYS_ORDER:
                        slot_dph[(store, day, int(slot))] = round(float(dph_val), 3)
        except:
            pass
        
        # Fallback: store-level AVG DPH
        if not slot_dph:
            uploaded_file.seek(0)
            try:
                avg_df = pd.read_excel(uploaded_file, sheet_name='Store Level AVG DPH')
                week_num = selected_week.replace('WK', '').replace('Week', '').strip() if selected_week else ''
                dph_col = None
                for col in avg_df.columns:
                    col_str = str(col)
                    if week_num and week_num in col_str and ('Week' in col_str or 'WK' in col_str):
                        dph_col = col
                        break
                if dph_col is None:
                    dph_col = 'AVG Input DPH' if 'AVG Input DPH' in avg_df.columns else None
                if dph_col:
                    for _, row in avg_df.iterrows():
                        if pd.notna(row.get('Store')) and pd.notna(row.get(dph_col)):
                            store_avg_dph[row['Store']] = round(float(row[dph_col]), 3)
            except:
                pass
        
        uploaded_file.seek(0)
    except:
        pass
    
    if stores is None:
        stores = sorted(demand_df['Store'].unique())
    
    # Build rostered DAs per store/slot/day from saved rosters
    records = []
    
    for store in stores:
        # Get the latest saved roster for this store
        opt_key = f'optimized_shifts_{store}'
        if opt_key in st.session_state and st.session_state[opt_key] is not None:
            store_shifts = st.session_state[opt_key]
        else:
            # Generate fresh if no saved roster
            transfers = load_transfers()
            adjusted_das_df = apply_transfer_adjustments(das_df, transfers)
            store_das = adjusted_das_df[adjusted_das_df['Store'] == store].copy()
            if store_das.empty or store_das['DA_Count'].sum() == 0:
                continue
            
            custom_shifts = st.session_state.get('custom_fixed_shifts', FIXED_SHIFTS.copy())
            current_engine = st.session_state.get('engine_type', 'flexible')
            
            if current_engine == 'fixed':
                engine_params = fixed_get_params({
                    'shift_hours': params.get('shift_hours', 10),
                    'break_hours': params.get('break_hours', 1),
                    'max_continuous': params.get('max_continuous', 5),
                    'min_rest': params.get('min_rest', 12),
                    'working_days': params.get('working_days', 6),
                    'custom_shifts': custom_shifts
                })
                da_list = fixed_build_da_list(store_das)
                shift_shares = st.session_state.get(f'shift_shares_{store}', None)
                store_shifts = assign_shifts_fixed(da_list, demand_df, store, engine_params, shift_shares)
            elif current_engine == 'proportional':
                ep = v13_get_params({
                    'night_shift_enabled': params.get('night_shift', True),
                    'flexible_day_off': params.get('flexible_day_off', False),
                    'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                    'carryover_mode': params.get('carryover_mode', 'auto'),
                    'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                    'carryover_excel_data': params.get('carryover_excel_data', []),
                    'shift_hours': params.get('shift_hours', 10),
                    'break_hours': params.get('break_hours', 1),
                    'max_continuous': params.get('max_continuous', 5),
                    'min_rest': params.get('min_rest', 12),
                    'working_days': params.get('working_days', 6)
                })
                da_list = v13_build_da_list(store_das)
                store_shifts = v13_assign_shifts(da_list, demand_df[demand_df['Store'] == store], None, ep)
            elif current_engine == 'demand_driven':
                ep = v14_get_params({
                    'night_shift_enabled': params.get('night_shift', True),
                    'flexible_day_off': params.get('flexible_day_off', False),
                    'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                    'carryover_mode': params.get('carryover_mode', 'auto'),
                    'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                    'carryover_excel_data': params.get('carryover_excel_data', []),
                    'shift_hours': params.get('shift_hours', 10),
                    'break_hours': params.get('break_hours', 1),
                    'max_continuous': params.get('max_continuous', 5),
                    'min_rest': params.get('min_rest', 12),
                    'working_days': params.get('working_days', 6),
                    'fixed_start_optimizer': params.get('fixed_start_optimizer', 'post_off'),
                    'max_shifts': params.get('max_shifts', 0),
                })
                da_list = v14_build_da_list(store_das)
                store_shifts = v14_assign_shifts(da_list, demand_df[demand_df['Store'] == store], None, ep)
            elif current_engine == 'demand_driven_ultimate':
                ep = v14u_get_params({
                    'night_shift_enabled': params.get('night_shift', True),
                    'flexible_day_off': params.get('flexible_day_off', False),
                    'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                    'carryover_mode': params.get('carryover_mode', 'auto'),
                    'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                    'carryover_excel_data': params.get('carryover_excel_data', []),
                    'shift_hours': params.get('shift_hours', 10),
                    'break_hours': params.get('break_hours', 1),
                    'max_continuous': params.get('max_continuous', 5),
                    'min_rest': params.get('min_rest', 12),
                    'working_days': params.get('working_days', 6)
                })
                da_list = v14u_build_da_list(store_das)
                store_shifts = v14u_assign_shifts(da_list, demand_df[demand_df['Store'] == store], None, ep)
            elif current_engine == 'tunable':
                ep = v15_get_params({
                    'night_shift_enabled': params.get('night_shift', True),
                    'flexible_day_off': params.get('flexible_day_off', False),
                    'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                    'carryover_mode': params.get('carryover_mode', 'auto'),
                    'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                    'carryover_excel_data': params.get('carryover_excel_data', []),
                    'shift_hours': params.get('shift_hours', 10),
                    'break_hours': params.get('break_hours', 1),
                    'max_continuous': params.get('max_continuous', 5),
                    'min_rest': params.get('min_rest', 12),
                    'working_days': params.get('working_days', 6),
                    'priorities': st.session_state.get('tunable_priorities', {}),
                })
                da_list = v15_build_da_list(store_das)
                store_shifts = v15_assign_shifts(da_list, demand_df[demand_df['Store'] == store], None, ep)
            else:
                store_priorities = st.session_state.get('store_priorities', {}).get(store, {h: 5 for h in range(24)})
                _, store_shifts = generate_roster_with_priorities(
                    demand_df, das_df, store, store_priorities, params,
                    day_multipliers=st.session_state.get('day_multipliers', {}),
                    scale_mode=params.get('scale_mode', 'exponential'),
                    intensity=params.get('intensity', 2.0)
                )
        
        if store_shifts is None or store_shifts.empty:
            continue
        
        # Generate hourly roster to get rostered counts
        store_demand = demand_df[demand_df['Store'] == store]
        current_engine = st.session_state.get('engine_type', 'flexible')
        
        if current_engine == 'fixed':
            engine_params = fixed_get_params({
                'shift_hours': params.get('shift_hours', 10),
                'break_hours': params.get('break_hours', 1),
                'max_continuous': params.get('max_continuous', 5),
                'min_rest': params.get('min_rest', 12),
                'working_days': params.get('working_days', 6),
                'custom_shifts': st.session_state.get('custom_fixed_shifts', FIXED_SHIFTS.copy())
            })
            roster = fixed_generate_hourly_roster(store_shifts, store_demand, engine_params)
        else:
            engine_params = engine_get_params(params)
            if current_engine == 'proportional':
                ep = v13_get_params(params)
                roster = v13_generate_hourly_roster(store_shifts, store_demand, ep)
            elif current_engine == 'demand_driven':
                ep = v14_get_params(params)
                roster = v14_generate_hourly_roster(store_shifts, store_demand, ep)
            elif current_engine == 'demand_driven_ultimate':
                ep = v14u_get_params(params)
                roster = v14u_generate_hourly_roster(store_shifts, store_demand, ep)
            elif current_engine == 'tunable':
                ep = v15_get_params({**params, 'priorities': st.session_state.get('tunable_priorities', {})})
                roster = v15_generate_hourly_roster(store_shifts, store_demand, ep)
            else:
                roster = engine_generate_hourly_roster(store_shifts, store_demand, engine_params)
        
        if roster is None or roster.empty:
            continue
        
        for slot in range(24):
            row = {'Store': store, 'Slot': slot}
            
            # Rostered DAs per day
            for day in DAYS_ORDER:
                day_slot = roster[(roster['Day'] == day) & (roster['Slot'] == slot)]
                rostered = int(day_slot['Rostered'].values[0]) if len(day_slot) > 0 else 0
                row[day] = rostered
            
            row[''] = None  # Separator column
            
            # Get DPH: slot-level > store-avg > default 2.0
            # Use first available day's DPH as the "Input DPH" display value
            slot_dph_val = slot_dph.get((store, 'Sun', slot), 
                           slot_dph.get((store, 'Mon', slot),
                           store_avg_dph.get(store, 2.0)))
            row['Input DPH'] = round(slot_dph_val, 2)
            
            # Max Orders per day = Rostered × DPH (using day-specific DPH if available)
            for day in DAYS_ORDER:
                day_dph = slot_dph.get((store, day, slot), store_avg_dph.get(store, slot_dph_val))
                row[f'{day}_Orders'] = int(round(row[day] * day_dph))
            
            records.append(row)
    
    if not records:
        return None
    
    # Build final DataFrame with proper column order
    result = pd.DataFrame(records)
    
    # Rename order columns to match LM Cap format (day names for both sections)
    col_order = ['Store', 'Slot'] + DAYS_ORDER + ['', 'Input DPH'] + [f'{d}_Orders' for d in DAYS_ORDER]
    result = result[col_order]
    
    return result


# =============================================================================
# DA TRANSFER PERSISTENCE (NEW in v2)
# =============================================================================
TRANSFER_FILE = 'da_transfers.json'

def load_transfers():
    """Load DA transfers from JSON file."""
    if os.path.exists(TRANSFER_FILE):
        try:
            with open(TRANSFER_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {'transfers': [], 'adjustments': {}}

def save_transfers(data):
    """Save DA transfers to JSON file."""
    with open(TRANSFER_FILE, 'w') as f:
        json.dump(data, f, indent=2, default=str)

def apply_transfer_adjustments(das_df, transfers):
    """
    Apply transfer adjustments to DA dataframe.
    Adjusts DA_Count for each store based on recorded transfers.
    Returns: adjusted das_df copy
    """
    if not transfers or 'adjustments' not in transfers:
        return das_df
    
    adjusted_df = das_df.copy()
    adjustments = transfers.get('adjustments', {})
    
    for store, adjustment in adjustments.items():
        if adjustment == 0:
            continue
        
        store_mask = adjusted_df['Store'] == store
        if not store_mask.any():
            continue
        
        # Distribute adjustment across DSPs in the store
        store_rows = adjusted_df[store_mask]
        n_dsps = len(store_rows)
        
        if n_dsps == 0:
            continue
        
        if adjustment > 0:
            # Adding DAs - add to first DSP or distribute evenly
            per_dsp = adjustment // n_dsps
            remainder = adjustment % n_dsps
            
            for i, idx in enumerate(store_rows.index):
                add_count = per_dsp + (1 if i < remainder else 0)
                adjusted_df.at[idx, 'DA_Count'] = adjusted_df.at[idx, 'DA_Count'] + add_count
        else:
            # Removing DAs - remove proportionally from DSPs
            total_das = store_rows['DA_Count'].sum()
            to_remove = abs(adjustment)
            
            if to_remove >= total_das:
                # Can't remove more than available
                to_remove = total_das - 1  # Keep at least 1
            
            for idx in store_rows.index:
                current = adjusted_df.at[idx, 'DA_Count']
                proportion = current / total_das if total_das > 0 else 0
                remove_count = int(to_remove * proportion)
                adjusted_df.at[idx, 'DA_Count'] = max(0, current - remove_count)
    
    # Ensure no negative DA counts
    adjusted_df['DA_Count'] = adjusted_df['DA_Count'].clip(lower=0)
    
    return adjusted_df

def get_store_da_adjustment(store, transfers):
    """Get the net DA adjustment for a specific store."""
    if not transfers or 'adjustments' not in transfers:
        return 0
    return transfers.get('adjustments', {}).get(store, 0)

def calculate_excess_das(roster_df, params):
    """Calculate how many DAs can be removed while still satisfying demand."""
    if roster_df is None or roster_df.empty:
        return 0
    effective_hours = params.get('shift_hours', 10) - params.get('break_hours', 1)
    working_days = params.get('working_days', 6)
    da_weekly_hours = effective_hours * working_days
    
    # Find minimum excess per hour across all days (conservative)
    hourly_min_excess = {}
    for slot in range(24):
        slot_data = roster_df[roster_df['Slot'] == slot]
        if not slot_data.empty:
            min_excess = slot_data['Diff'].min()
            if min_excess > 0:
                hourly_min_excess[slot] = min_excess
    
    consistent_excess_hours = sum(hourly_min_excess.values()) * 7
    removable_das = int(consistent_excess_hours * 0.7 / da_weekly_hours)
    return max(0, removable_das)

# Custom CSS for better slider appearance
st.markdown("""
<style>
    .stSlider > div > div > div > div {
        background-color: #1f77b4;
    }
    .priority-high { color: #d62728; font-weight: bold; }
    .priority-medium { color: #ff7f0e; }
    .priority-low { color: #2ca02c; }
    .undo-redo-btn {
        padding: 5px 15px;
        margin: 2px;
        border-radius: 5px;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# Keyboard shortcut handler using streamlit components
import streamlit.components.v1 as components

def add_keyboard_shortcuts():
    """Add keyboard shortcuts for undo/redo using JavaScript injection."""
    components.html("""
    <script>
    // Only add listener once
    if (!window.undoRedoListenerAdded) {
        window.undoRedoListenerAdded = true;
        
        document.addEventListener('keydown', function(e) {
            // Ctrl+Z for Undo
            if (e.ctrlKey && e.key === 'z' && !e.shiftKey) {
                e.preventDefault();
                // Find undo button by looking for button with Undo text
                const buttons = parent.document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (btn.innerText && btn.innerText.includes('Undo') && !btn.disabled) {
                        btn.click();
                        break;
                    }
                }
            }
            // Ctrl+Y or Ctrl+Shift+Z for Redo
            if ((e.ctrlKey && e.key === 'y') || (e.ctrlKey && e.shiftKey && e.key === 'z')) {
                e.preventDefault();
                const buttons = parent.document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (btn.innerText && btn.innerText.includes('Redo') && !btn.disabled) {
                        btn.click();
                        break;
                    }
                }
            }
        });
    }
    </script>
    """, height=0)

def detect_available_weeks(uploaded_file):
    """Detect available weeks from the uploaded file.
    
    Returns:
        tuple: (list of week names like ['WK7', 'WK8', ...], default week)
    """
    try:
        # Read Slot Level DA Requirement to get weeks from Week column
        demand_df = pd.read_excel(uploaded_file, sheet_name='Slot Level DA Requirement')
        
        weeks_from_demand = []
        if 'Week' in demand_df.columns:
            # Get unique weeks and sort them
            unique_weeks = demand_df['Week'].dropna().unique().tolist()
            # Normalize week names (ensure WK format)
            for w in unique_weeks:
                w_str = str(w).strip()
                # Extract the number from various formats
                if w_str.startswith('WK'):
                    weeks_from_demand.append(w_str)
                elif w_str.startswith('Week '):
                    # Convert "Week 7" to "WK7"
                    num = w_str.replace('Week ', '').strip()
                    weeks_from_demand.append(f'WK{num}')
                elif w_str.startswith('Week'):
                    # Convert "Week7" to "WK7"
                    num = w_str.replace('Week', '').strip()
                    weeks_from_demand.append(f'WK{num}')
                elif w_str.isdigit():
                    weeks_from_demand.append(f'WK{w_str}')
                else:
                    # Try to extract number from string
                    import re
                    match = re.search(r'\d+', w_str)
                    if match:
                        weeks_from_demand.append(f'WK{match.group()}')
                    else:
                        weeks_from_demand.append(w_str)
        
        # Sort weeks numerically
        def week_sort_key(w):
            try:
                import re
                match = re.search(r'\d+', str(w))
                return int(match.group()) if match else 999
            except:
                return 999
        
        weeks_from_demand = sorted(list(set(weeks_from_demand)), key=week_sort_key)
        
        # Also check Available DAs sheet for week columns
        das_df = pd.read_excel(uploaded_file, sheet_name='Available DAs')
        week_cols = []
        for col in das_df.columns:
            col_str = str(col)
            if 'Week' in col_str or col_str.startswith('WK'):
                # Check if column has numeric data
                if das_df[col].dtype in ['int64', 'float64'] or das_df[col].notna().any():
                    week_cols.append(col_str)
        
        # If no weeks found in demand, try to infer from Available DAs columns
        if not weeks_from_demand and week_cols:
            import re
            for col in week_cols:
                match = re.search(r'\d+', col)
                if match:
                    weeks_from_demand.append(f'WK{match.group()}')
            weeks_from_demand = sorted(list(set(weeks_from_demand)), key=week_sort_key)
        
        # Default to first week if available
        default_week = weeks_from_demand[0] if weeks_from_demand else None
        
        # Reset file position for subsequent reads
        uploaded_file.seek(0)
        
        if not weeks_from_demand:
            # No weeks found at all — return empty so UI shows "single week" mode
            return [], None
        
        return weeks_from_demand, default_week
        
    except Exception as e:
        # Reset file position
        try:
            uploaded_file.seek(0)
        except:
            pass
        return [], None

def load_data(uploaded_file, selected_week=None):
    """Load demand and DA data from uploaded file, filtered by week if specified."""
    demand_df = pd.read_excel(uploaded_file, sheet_name='Slot Level DA Requirement')
    
    # Remove duplicate columns (keep first occurrence)
    demand_df = demand_df.loc[:, ~demand_df.columns.duplicated()]
    
    # Filter by week if Week column exists and week is specified
    if 'Week' in demand_df.columns and selected_week is not None:
        # Normalize week values for comparison
        week_num = selected_week.replace('WK', '').replace('Week', '').strip()
        
        # Create a normalized version of the Week column for matching
        def normalize_week(val):
            val_str = str(val).strip()
            if val_str.startswith('WK'):
                return val_str.replace('WK', '')
            elif val_str.startswith('Week'):
                return val_str.replace('Week', '').strip()
            return val_str
        
        demand_df['_week_normalized'] = demand_df['Week'].apply(normalize_week)
        demand_df = demand_df[demand_df['_week_normalized'] == week_num].copy()
        demand_df = demand_df.drop(columns=['_week_normalized'])
        
        if demand_df.empty:
            available_weeks = pd.read_excel(uploaded_file, sheet_name='Slot Level DA Requirement')['Week'].unique().tolist()
            raise ValueError(f"No data found for {selected_week}. Available weeks: {available_weeks}")
    
    # Rename 'Hourly Orders' to 'Final Orders' for engine compatibility
    if 'Hourly Orders' in demand_df.columns:
        demand_df = demand_df.rename(columns={'Hourly Orders': 'Final Orders'})
    
    das_df = pd.read_excel(uploaded_file, sheet_name='Available DAs')
    
    # Debug: show available columns
    das_columns = list(das_df.columns)
    
    # Check if week columns exist (Week 7, Week 8, etc. or WK7, WK8, etc.)
    week_col = None
    if selected_week:
        # Extract week number from selected_week (e.g., "WK7" -> "7")
        import re
        week_match = re.search(r'\d+', selected_week)
        week_num = week_match.group() if week_match else selected_week.replace('WK', '').replace('Week', '').strip()
        
        # Try different column name formats - "Week 7" format is most common
        possible_cols = [
            f'Week {week_num}',      # "Week 7" - most common format
            f'Week{week_num}',       # "Week7"
            f'WK{week_num}',         # "WK7"
            selected_week,           # exact match
        ]
        
        for col in possible_cols:
            if col in das_df.columns:
                week_col = col
                break
        
        # If still not found, search for any column containing the week number
        if week_col is None:
            for col in das_df.columns:
                col_str = str(col)
                # Check if column contains "Week" and the week number
                if ('Week' in col_str or 'WK' in col_str) and week_num in col_str:
                    week_col = col
                    break
    
    das_df = das_df.rename(columns={
        'Station': 'Store', 'DSP Name': 'DSP',
        'DSP Code': 'DSP_Code'
    })
    
    # Use week-specific DA count if available
    if week_col and week_col in das_df.columns:
        # Use the week column directly - convert to numeric and fill NaN with 0
        das_df['DA_Count'] = pd.to_numeric(das_df[week_col], errors='coerce').fillna(0)
        total_das = int(das_df['DA_Count'].sum())
        if total_das > 0:
            st.success(f"✅ Using DA counts from column: '{week_col}' ({total_das} total DAs)")
        else:
            st.warning(f"⚠️ Column '{week_col}' has 0 DAs - check your data")
    elif 'Actual' in das_df.columns:
        das_df['DA_Count'] = pd.to_numeric(das_df['Actual'], errors='coerce').fillna(0)
        st.info("ℹ️ Using 'Actual' column for DA counts")
    else:
        # Try to find any week column with data
        found_col = None
        for col in das_df.columns:
            col_str = str(col)
            if 'Week' in col_str:
                try:
                    if das_df[col].notna().any():
                        das_df['DA_Count'] = pd.to_numeric(das_df[col], errors='coerce').fillna(0)
                        found_col = col
                        break
                except:
                    continue
        if found_col:
            st.warning(f"⚠️ Week column not found for {selected_week}, using '{found_col}'")
        else:
            das_df['DA_Count'] = 0
            st.error(f"❌ No DA count column found! Available columns: {das_columns}")
    
    das_df = das_df.dropna(subset=['Store'])  # Drop rows with NaN Store
    das_df['DA_Count'] = pd.to_numeric(das_df['DA_Count'], errors='coerce').fillna(0)
    das_df = das_df[das_df['DA_Count'] > 0]

    # Optional Flex (part-time) DA counts per DSP/store
    if 'Flex' in das_df.columns:
        das_df['Flex_Count'] = pd.to_numeric(das_df['Flex'], errors='coerce').fillna(0).astype(int)
    else:
        das_df['Flex_Count'] = 0

    # Load Store Parameters sheet if it exists
    store_configs = {}
    store_params_warnings = []
    try:
        sp_df = None
        for sheet_name in ['Store Parameters', 'Rostering Parameters']:
            try:
                sp_df = pd.read_excel(uploaded_file, sheet_name=sheet_name)
                break
            except Exception:
                continue
        if sp_df is not None and 'Store' in sp_df.columns:
            # Per-store parameters format (Store, shift_hours, break_hours, ...)
            store_configs, store_params_warnings = parse_store_parameters_sheet(sp_df)
            if store_configs:
                st.info(f"📋 Store Parameters loaded for {len(store_configs)} stores")
            for w in store_params_warnings:
                st.warning(w)
        elif sp_df is not None and 'Parameter' in sp_df.columns:
            # Global key-value format (Parameter, Value) — not per-store
            st.info("📋 Global Rostering Parameters found (not per-store)")
    except Exception:
        pass  # No parameters sheet — use global params only
    
    return demand_df, das_df, store_configs, store_params_warnings

def apply_priority_weights(demand_df, slot_priorities, day_multipliers=None, scale_mode='exponential', intensity=2.0):
    """Apply priority weights to demand - boost high priority, reduce low priority.
    
    Scale modes:
    - 'linear': Simple linear scaling (0.1x to 3.0x)
    - 'exponential': Exponential scaling for more dramatic effect (0.1x to 5.0x)
    - 'logarithmic': Log scaling - gentle at extremes, steep in middle
    - 'aggressive': Very aggressive scaling (0x to 10x)
    
    Intensity: Multiplier for the effect strength (1.0 = normal, 2.0 = double effect)
    
    Day Multipliers: Dict {day_name: multiplier} where:
    - 1.0 = use demand as-is (no priority adjustment for this day)
    - >1.0 = boost priority effect (e.g., 1.5 = 50% more priority impact)
    - <1.0 = reduce priority effect (e.g., 0.5 = 50% less priority impact)
    """
    import math
    adjusted_df = demand_df.copy()
    
    # Map full day names to short names for matching
    day_name_map = {
        'Sunday': 'Sun', 'Monday': 'Mon', 'Tuesday': 'Tue', 'Wednesday': 'Wed',
        'Thursday': 'Thu', 'Friday': 'Fri', 'Saturday': 'Sat',
        'Sun': 'Sun', 'Mon': 'Mon', 'Tue': 'Tue', 'Wed': 'Wed',
        'Thu': 'Thu', 'Fri': 'Fri', 'Sat': 'Sat'
    }
    
    # Default day multipliers (all 1.0 = full priority effect)
    if day_multipliers is None:
        day_multipliers = {day: 1.0 for day in ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']}
    
    # Convert day multipliers to short names for matching
    day_mult_short = {}
    for day_full, mult in day_multipliers.items():
        short = day_name_map.get(day_full, day_full[:3])
        day_mult_short[short] = mult
    
    for slot, priority in slot_priorities.items():
        # Normalize priority to 0-1 range
        p = priority / 10.0
        
        if scale_mode == 'linear':
            # Linear: 0 -> 0.1x, 5 -> 1.0x, 10 -> 3.0x
            base_multiplier = 0.1 + p * 2.9 * intensity
            
        elif scale_mode == 'exponential':
            # Exponential: dramatic boost for high priority
            # 0 -> 0.1x, 5 -> 1.0x, 10 -> 5.0x (with intensity=1)
            if p < 0.5:
                # Below neutral: reduce demand
                base_multiplier = 0.1 + (p * 2) * 0.9  # 0.1 to 1.0
            else:
                # Above neutral: boost demand exponentially
                base_multiplier = 1.0 + ((p - 0.5) * 2) ** 2 * 4.0 * intensity  # 1.0 to 5.0+
                
        elif scale_mode == 'logarithmic':
            # Logarithmic: gentle at extremes, steep in middle
            # Good for fine-tuning around the neutral point
            if priority == 0:
                base_multiplier = 0.1
            elif priority == 10:
                base_multiplier = 3.0 * intensity
            else:
                # Log curve centered at 5
                base_multiplier = math.exp((priority - 5) * 0.3 * intensity)
                
        elif scale_mode == 'aggressive':
            # Very aggressive: can completely zero out low priority slots
            # 0 -> 0x, 5 -> 1.0x, 10 -> 10x
            if priority <= 2:
                base_multiplier = priority * 0.1  # 0, 0.1, 0.2
            elif priority <= 5:
                base_multiplier = 0.2 + (priority - 2) * 0.27  # 0.2 to 1.0
            else:
                base_multiplier = 1.0 + (priority - 5) * 1.8 * intensity  # 1.0 to 10.0
        else:
            base_multiplier = 1.0
        
        # Apply to each day with day-specific multiplier
        # Get unique days from the dataframe and match with multipliers
        for _, row in adjusted_df[adjusted_df['Slot'] == slot].iterrows():
            day_in_df = str(row['Day'])[:3]  # Get short day name (Sun, Mon, etc.)
            day_mult = day_mult_short.get(day_in_df, 1.0)
            
            if day_mult == 0.0:
                # No adjustment - keep original demand
                continue
            
            # Apply priority effect scaled by day multiplier
            effective_mult = 1.0 + (base_multiplier - 1.0) * day_mult
            mask = (adjusted_df['Slot'] == slot) & (adjusted_df['Day'] == row['Day'])
            adjusted_df.loc[mask, 'DA Required'] = (adjusted_df.loc[mask, 'DA Required'] * effective_mult).round()
    
    return adjusted_df

def generate_roster_with_priorities(demand_df, das_df, store, slot_priorities, params, day_multipliers=None, scale_mode='exponential', intensity=2.0, apply_transfers=True):
    """Generate roster with priority-adjusted demand and transfer adjustments."""
    store_demand = demand_df[demand_df['Store'] == store].copy()
    
    # Apply transfer adjustments if enabled
    if apply_transfers:
        transfers = load_transfers()
        adjusted_das_df = apply_transfer_adjustments(das_df, transfers)
        store_das = adjusted_das_df[adjusted_das_df['Store'] == store].copy()
    else:
        store_das = das_df[das_df['Store'] == store].copy()
    
    if store_das.empty or store_das['DA_Count'].sum() == 0:
        return None, None
    
    # Apply priority weights to demand with selected scale mode and day multipliers
    adjusted_demand = apply_priority_weights(store_demand, slot_priorities, day_multipliers, scale_mode, intensity)
    
    # Build DA list and assign shifts
    da_list = engine_build_da_list(store_das)
    
    # Pass all working rules to engine
    engine_params = engine_get_params({
        'night_shift_enabled': params.get('night_shift', True),
        'flexible_day_off': params.get('flexible_day_off', False),
        'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
        'carryover_mode': params.get('carryover_mode', 'auto'),
        'sunday_carryover_das': params.get('sunday_carryover_das', 0),
        'carryover_excel_data': params.get('carryover_excel_data', []),
        'shift_hours': params.get('shift_hours', 10),
        'break_hours': params.get('break_hours', 1),
        'max_continuous': params.get('max_continuous', 5),
        'min_rest': params.get('min_rest', 12),
        'working_days': params.get('working_days', 6)
    })
    
    shifts_df = engine_assign_shifts(da_list, adjusted_demand, None, engine_params)
    
    # Generate hourly roster against ORIGINAL demand (not adjusted)
    roster_df = engine_generate_hourly_roster(shifts_df, store_demand, engine_params)
    
    return roster_df, shifts_df

def create_coverage_heatmap(roster_df, slot_priorities):
    """Create interactive heatmap showing coverage by day and hour."""
    if roster_df is None or roster_df.empty:
        return None
    
    # Pivot to get coverage matrix
    pivot_rostered = roster_df.pivot_table(
        index='Day', columns='Slot', values='Rostered', aggfunc='sum'
    ).reindex(DAYS)
    
    pivot_required = roster_df.pivot_table(
        index='Day', columns='Slot', values='Required', aggfunc='sum'
    ).reindex(DAYS)
    
    pivot_diff = roster_df.pivot_table(
        index='Day', columns='Slot', values='Diff', aggfunc='sum'
    ).reindex(DAYS)
    
    # Create figure with subplots
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=('Coverage (Rostered)', 'Demand (Required)', 
                       'Gap/Excess (Diff)', 'Slot Priorities'),
        vertical_spacing=0.12,
        horizontal_spacing=0.08
    )
    
    # Rostered heatmap
    fig.add_trace(
        go.Heatmap(
            z=pivot_rostered.values,
            x=list(range(24)),
            y=DAYS,
            colorscale='Blues',
            name='Rostered',
            showscale=True,
            colorbar=dict(x=0.45, len=0.4, y=0.8)
        ),
        row=1, col=1
    )
    
    # Required heatmap
    fig.add_trace(
        go.Heatmap(
            z=pivot_required.values,
            x=list(range(24)),
            y=DAYS,
            colorscale='Oranges',
            name='Required',
            showscale=True,
            colorbar=dict(x=1.0, len=0.4, y=0.8)
        ),
        row=1, col=2
    )
    
    # Diff heatmap (gap = red, excess = green)
    fig.add_trace(
        go.Heatmap(
            z=pivot_diff.values,
            x=list(range(24)),
            y=DAYS,
            colorscale='RdYlGn',
            zmid=0,
            name='Diff',
            showscale=True,
            colorbar=dict(x=0.45, len=0.4, y=0.2)
        ),
        row=2, col=1
    )
    
    # Priority bar chart
    priorities = [slot_priorities.get(h, 5) for h in range(24)]
    colors = ['#d62728' if p >= 8 else '#ff7f0e' if p >= 5 else '#2ca02c' for p in priorities]
    
    fig.add_trace(
        go.Bar(
            x=list(range(24)),
            y=priorities,
            marker_color=colors,
            name='Priority'
        ),
        row=2, col=2
    )
    
    fig.update_layout(
        height=700,
        title_text="Coverage Analysis Dashboard",
        showlegend=False,
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#64748B'),
    )
    
    # Make subplot axes transparent too
    for i in range(1, 3):
        for j in range(1, 3):
            fig.update_xaxes(gridcolor='rgba(148,163,184,0.15)', row=i, col=j)
            fig.update_yaxes(gridcolor='rgba(148,163,184,0.15)', row=i, col=j)
    
    return fig

def assign_flex_shifts(das_df, demand_df, store, params, full_time_roster_df):
    """Assign short part-time (Flex) shifts to cover remaining gaps left by the
    full-time roster.

    Flex DAs work shorter shifts (default 4h, configurable via params['flex_shift_hours']).
    Each Flex DA greedily picks the start hour on each day that covers the
    largest remaining gap. Returns a shifts-style DataFrame or None if there
    are no Flex DAs, no gap, or the Flex_Count column isn't available.
    """
    if 'Flex_Count' not in das_df.columns:
        return None

    store_flex = das_df[
        (das_df['Store'] == store)
        & (das_df.get('Flex_Count', pd.Series(0, index=das_df.index)) > 0)
    ]
    if store_flex.empty:
        return None

    flex_shift_hours = int(params.get('flex_shift_hours', 4))
    DAYS_LOCAL = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

    # Build gap matrix (day, slot) -> remaining need
    gap_matrix = {}
    if full_time_roster_df is not None and not full_time_roster_df.empty:
        for _, row in full_time_roster_df.iterrows():
            gap = max(0, int(row['Required']) - int(row['Rostered']))
            if gap > 0:
                gap_matrix[(str(row['Day'])[:3], int(row['Slot']))] = gap

    if not gap_matrix:
        return None

    # Build flex DA list
    flex_das = []
    for _, row in store_flex.iterrows():
        for i in range(int(row['Flex_Count'])):
            flex_das.append({
                'DA_ID': f"{store}-{row['DSP_Code']}-FLEX-{str(i + 1).zfill(3)}",
                'Store': store,
                'DSP': row['DSP'],
                'DSP_Code': row['DSP_Code'],
                'Pool': 'Flex',
            })

    all_schedules = []
    coverage = {day: {h: 0 for h in range(24)} for day in DAYS_LOCAL}

    for da in flex_das:
        for di, day in enumerate(DAYS_LOCAL):
            best_start = None
            best_score = 0
            for start in range(24):
                # Skip overnight starts when night shifts are disabled
                if not params.get('night_shift', True) and start + flex_shift_hours > 24:
                    continue
                hours = [(start + h) % 24 for h in range(flex_shift_hours)]
                score = sum(
                    max(0, gap_matrix.get((day, h), 0) - coverage[day][h])
                    for h in hours
                )
                if score > best_score:
                    best_score = score
                    best_start = start

            if best_start is not None and best_score > 0:
                end = (best_start + flex_shift_hours) % 24
                for h in range(flex_shift_hours):
                    coverage[day][(best_start + h) % 24] += 1
                all_schedules.append({
                    'DA_ID': da['DA_ID'], 'Store': store, 'DSP': da['DSP'],
                    'DSP_Code': da['DSP_Code'], 'Pool': 'Flex',
                    'Day': day, 'Day_Index': di,
                    'Shift_Start': best_start, 'Shift_End': end,
                    'Break_Hour': None, 'Break_Hour_2': None,
                    'Is_Day_Off': False,
                })
            else:
                all_schedules.append({
                    'DA_ID': da['DA_ID'], 'Store': store, 'DSP': da['DSP'],
                    'DSP_Code': da['DSP_Code'], 'Pool': 'Flex',
                    'Day': day, 'Day_Index': di,
                    'Shift_Start': None, 'Shift_End': None,
                    'Break_Hour': None, 'Break_Hour_2': None,
                    'Is_Day_Off': True,
                })

    return pd.DataFrame(all_schedules) if all_schedules else None


def generate_flex_hourly_roster(flex_shifts_df, demand_df, store, params):
    """Turn a flex shifts DataFrame into an hour-by-hour roster DataFrame
    with Required and Flex_Rostered columns.
    """
    if flex_shifts_df is None or flex_shifts_df.empty:
        return None

    DAYS_LOCAL = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    records = []
    store_flex = flex_shifts_df[flex_shifts_df['Store'] == store]
    store_demand = demand_df[demand_df['Store'] == store]

    for day in DAYS_LOCAL:
        day_shifts = store_flex[store_flex['Day'] == day]
        for slot in range(24):
            demand_row = store_demand[
                (store_demand['Day'].str[:3] == day)
                & (store_demand['Slot'] == slot)
            ]
            required = int(demand_row['DA Required'].values[0]) if len(demand_row) > 0 else 0

            flex_rostered = 0
            for _, shift in day_shifts.iterrows():
                if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
                    continue
                start = int(shift['Shift_Start'])
                end = int(shift['Shift_End'])
                is_overnight = end != 0 and end < start
                working = (slot >= start) if is_overnight else (start <= slot < end)
                if working:
                    flex_rostered += 1

            records.append({
                'Store': store, 'Day': day, 'Slot': slot,
                'Required': required, 'Flex_Rostered': flex_rostered,
            })

    return pd.DataFrame(records)


def generate_dsp_slot_matrix(shifts_df, demand_df, store, params):
    """Build a DSP × slot matrix for a store showing how many DAs each DSP
    contributes to each active hour slot across the week. Flags slots
    covered by only a single DSP as a resilience risk.
    """
    DAYS_LOCAL = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    if shifts_df is None or shifts_df.empty:
        return pd.DataFrame()

    store_shifts = shifts_df[shifts_df['Store'] == store]
    store_demand = demand_df[demand_df['Store'] == store]
    if store_shifts.empty:
        return pd.DataFrame()

    dsps = sorted(store_shifts['DSP'].unique().tolist())
    records = []

    for day in DAYS_LOCAL:
        day_shifts = store_shifts[store_shifts['Day'] == day]
        for slot in range(24):
            demand_row = store_demand[
                (store_demand['Day'].str[:3] == day)
                & (store_demand['Slot'] == slot)
            ]
            required = int(demand_row['DA Required'].values[0]) if len(demand_row) > 0 else 0
            if required == 0:
                continue

            dsp_counts = {dsp: 0 for dsp in dsps}
            total = 0
            for _, shift in day_shifts.iterrows():
                if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
                    continue
                start = int(shift['Shift_Start'])
                end = int(shift['Shift_End'])
                brk = int(shift['Break_Hour']) if pd.notna(shift.get('Break_Hour')) else -1
                is_overnight = end != 0 and end < start
                working = (slot >= start) if is_overnight else (start <= slot < end)
                if working and slot != brk:
                    dsp = shift['DSP']
                    if dsp in dsp_counts:
                        dsp_counts[dsp] += 1
                        total += 1

            active_dsps = [d for d, c in dsp_counts.items() if c > 0]
            row = {
                'Store': store, 'Day': day,
                'Slot': f"{slot:02d}:00-{(slot + 1) % 24:02d}:00",
                'Required': required, 'Total_Rostered': total,
            }
            for dsp in dsps:
                row[dsp] = dsp_counts.get(dsp, 0)
            row['DSPs_Active'] = len(active_dsps)
            if total > 0:
                row['Mix_Status'] = '✅ Mixed' if len(active_dsps) >= 2 else '⚠️ Single DSP'
            else:
                row['Mix_Status'] = '—'
            records.append(row)

    return pd.DataFrame(records)


def create_hourly_comparison_chart(roster_df, slot_priorities, selected_day='All Week', flex_roster_df=None):
    """Create bar chart comparing required vs rostered by hour, with orders line.
    
    Required is shown as a standalone grouped bar on the left of each hour.
    Rostered and Flex DAs (Part-Time) are stacked together on the right.
    
    Args:
        roster_df: DataFrame with roster data
        slot_priorities: Dict of slot priorities
        selected_day: 'All Week' or specific day name (Sun, Mon, etc.)
        flex_roster_df: Optional DataFrame with flex DA roster data
    """
    if roster_df is None or roster_df.empty:
        return None
    
    # Filter by day if not 'All Week'
    if selected_day != 'All Week':
        # Handle both full and short day names
        day_map = {'Sunday': 'Sun', 'Monday': 'Mon', 'Tuesday': 'Tue', 
                   'Wednesday': 'Wed', 'Thursday': 'Thu', 'Friday': 'Fri', 'Saturday': 'Sat'}
        short_day = day_map.get(selected_day, selected_day[:3] if len(selected_day) > 3 else selected_day)
        
        # Filter roster for selected day
        filtered_df = roster_df[roster_df['Day'].str[:3] == short_day].copy()
        if filtered_df.empty:
            return None
        title_suffix = f" - {selected_day}"
    else:
        filtered_df = roster_df.copy()
        title_suffix = " - All Week"
    
    # Aggregate by slot - include Orders if available
    agg_dict = {
        'Required': 'sum',
        'Rostered': 'sum',
        'Diff': 'sum'
    }
    if 'Orders' in filtered_df.columns:
        agg_dict['Orders'] = 'sum'
    
    hourly = filtered_df.groupby('Slot').agg(agg_dict).reset_index()
    
    fig = go.Figure()

    _has_flex = flex_roster_df is not None and not flex_roster_df.empty

    # --- Required bars: standalone group on the LEFT of each hour ---
    fig.add_trace(go.Bar(
        x=hourly['Slot'],
        y=hourly['Required'],
        name='Required',
        marker_color='rgba(255, 127, 14, 0.7)',
        offsetgroup='required',
    ))

    # --- Rostered bars: base of the stacked group on the RIGHT ---
    fig.add_trace(go.Bar(
        x=hourly['Slot'],
        y=hourly['Rostered'],
        name='Rostered',
        marker_color='rgba(31, 119, 180, 0.7)',
        offsetgroup='rostered',
    ))

    # --- Flex DA coverage stacked on top of Rostered ---
    if _has_flex:
        if selected_day != 'All Week':
            day_map_flex = {
                'Sunday': 'Sun', 'Monday': 'Mon', 'Tuesday': 'Tue',
                'Wednesday': 'Wed', 'Thursday': 'Thu', 'Friday': 'Fri',
                'Saturday': 'Sat',
            }
            short_day_flex = day_map_flex.get(selected_day, selected_day[:3])
            filtered_flex = flex_roster_df[flex_roster_df['Day'].str[:3] == short_day_flex]
        else:
            filtered_flex = flex_roster_df

        flex_hourly = filtered_flex.groupby('Slot')['Flex_Rostered'].sum().reset_index()
        # Merge so we have matching slots
        merged = hourly[['Slot', 'Rostered']].merge(flex_hourly, on='Slot', how='left').fillna(0)

        fig.add_trace(go.Bar(
            x=merged['Slot'],
            y=merged['Flex_Rostered'],
            name='Flex DAs (Part-Time)',
            marker_color='rgba(148, 103, 189, 0.7)',
            offsetgroup='rostered',
            base=merged['Rostered'],
        ))
    
    # Orders line (on secondary y-axis)
    if 'Orders' in hourly.columns:
        fig.add_trace(go.Scatter(
            x=hourly['Slot'],
            y=hourly['Orders'],
            name='Orders',
            mode='lines+markers',
            line=dict(color='rgba(44, 160, 44, 1)', width=2),
            marker=dict(size=6),
            yaxis='y2'
        ))
    
    # Add priority indicators (only for All Week view to avoid clutter)
    if selected_day == 'All Week':
        for slot in range(24):
            priority = slot_priorities.get(slot, 5)
            if priority >= 8:
                fig.add_annotation(
                    x=slot, y=max(hourly['Required'].max(), hourly['Rostered'].max()) * 1.05,
                    text="⭐", showarrow=False, font=dict(size=12)
                )

    title_label = (
        "Hourly Coverage: Required vs Rostered (+ Flex)"
        if _has_flex else "Hourly Coverage: Required vs Rostered"
    )
    fig.update_layout(
        title=f"{title_label}{title_suffix}",
        xaxis_title="Hour",
        yaxis_title="DA Count",
        yaxis2=dict(
            title="Orders",
            overlaying='y',
            side='right',
            showgrid=False
        ),
        barmode='group',
        height=400,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#64748B'),
        xaxis=dict(gridcolor='rgba(148,163,184,0.15)'),
        yaxis=dict(gridcolor='rgba(148,163,184,0.15)'),
    )

    return fig


# =============================================================================
# AUTO-OPTIMIZER: Genetic Algorithm to find optimal priority distribution
# =============================================================================

def evaluate_shift_shares_fitness(shares, demand_df, das_df, store, params):
    """Evaluate fitness of a shift share configuration for fixed shifts."""
    from roster_engine_fixed_shifts import get_active_shifts
    
    active_shifts = get_active_shifts(params)
    num_shifts = len(active_shifts)
    
    # Normalize shares to 100%
    total = sum(shares[:num_shifts]) if len(shares) >= num_shifts else sum(shares)
    if total == 0:
        return float('inf')
    normalized = {i+1: shares[i] / total * 100 for i in range(min(len(shares), num_shifts))}
    
    try:
        store_das = das_df[das_df['Store'] == store].copy()
        if store_das.empty or store_das['DA_Count'].sum() == 0:
            return float('inf')
        
        da_list = fixed_build_da_list(store_das)
        store_demand = demand_df[demand_df['Store'] == store]
        
        shifts_df = assign_shifts_fixed(da_list, demand_df, store, params, normalized)
        roster_df = fixed_generate_hourly_roster(shifts_df, store_demand, params)
        
        if roster_df is None or roster_df.empty:
            return float('inf')
        
        total_gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum())
        return total_gap
        
    except Exception as e:
        return float('inf')

def evaluate_shift_timing_fitness(shift_starts, shares, demand_df, das_df, store, params):
    """Evaluate fitness of shift timing configuration."""
    from roster_engine_fixed_shifts import get_active_shifts
    
    try:
        active_shifts = get_active_shifts(params)
        num_shifts = len(active_shifts)
        
        # Temporarily update shifts with new timings
        original_starts = {k: v['start'] for k, v in active_shifts.items()}
        
        for shift_id, start in shift_starts.items():
            if shift_id in active_shifts:
                active_shifts[shift_id]['start'] = start
        
        # Evaluate with current shares
        if shares:
            shares_list = [shares.get(i+1, 100/num_shifts) for i in range(num_shifts)]
        else:
            shares_list = [100/num_shifts] * num_shifts
        
        fitness = evaluate_shift_shares_fitness(shares_list, demand_df, das_df, store, params)
        
        # Restore original timings
        for shift_id, start in original_starts.items():
            active_shifts[shift_id]['start'] = start
        
        return fitness
    except Exception as e:
        return float('inf')

def run_shift_share_optimizer(demand_df, das_df, store, params, algorithm='quick', 
                              iterations=50, generations=20, baseline_gap=None,
                              current_shares=None, progress_callback=None):
    """
    Optimize shift shares for fixed shifts engine.
    
    Args:
        algorithm: 'demand' (deterministic, fast), 'hillclimb' (random, fast), or 'genetic' (random, slower)
        baseline_gap: Current gap - only return result if better than this
        current_shares: Starting shares (if None, uses auto-calculated)
    
    Returns: best_shares (dict), best_fitness, history, improved (bool)
    """
    from roster_engine_fixed_shifts import get_active_shifts, get_shift_coverage_hours
    
    active_shifts = get_active_shifts(params)
    num_shifts = len(active_shifts)
    default_share = 100 / num_shifts if num_shifts > 0 else 16.67
    sorted_shift_ids = sorted(active_shifts.keys())
    
    # Create index mapping for shift IDs
    id_to_idx = {shift_id: i for i, shift_id in enumerate(sorted_shift_ids)}
    idx_to_id = {i: shift_id for i, shift_id in enumerate(sorted_shift_ids)}
    
    # Get current shares as list (ordered by sorted shift IDs)
    if current_shares:
        current = [current_shares.get(shift_id, default_share) for shift_id in sorted_shift_ids]
    else:
        current = [default_share] * num_shifts
    
    # Normalize current to 100%
    total_current = sum(current)
    if total_current > 0:
        current = [x / total_current * 100 for x in current]
    
    current_fitness = evaluate_shift_shares_fitness(current, demand_df, das_df, store, params)
    
    if baseline_gap is not None:
        baseline = baseline_gap
    else:
        baseline = current_fitness
    
    best = current.copy()
    best_fitness = current_fitness
    history = []
    
    # DEMAND-DRIVEN OPTIMIZATION (deterministic, fast)
    if algorithm in ['quick', 'demand']:
        shift_hours = params.get('shift_hours', 10)
        
        # Step 1: Analyze gap contribution per shift
        store_das = das_df[das_df['Store'] == store].copy()
        store_demand = demand_df[demand_df['Store'] == store]
        da_list = fixed_build_da_list(store_das)
        total_das = len(da_list)
        
        if total_das == 0:
            return {shift_id: default_share for shift_id in sorted_shift_ids}, float('inf'), [], False
        
        # Generate baseline roster to analyze gaps
        test_shares = {sorted_shift_ids[i]: current[i] for i in range(num_shifts)}
        test_shifts = assign_shifts_fixed(da_list, store_demand, store, params, test_shares)
        test_roster = fixed_generate_hourly_roster(test_shifts, store_demand, params)
        
        # Calculate gap per hour
        hourly_gaps = {}
        hourly_excess = {}
        for slot in range(24):
            slot_data = test_roster[test_roster['Slot'] == slot]
            gap = abs(slot_data[slot_data['Diff'] < 0]['Diff'].sum())
            excess = slot_data[slot_data['Diff'] > 0]['Diff'].sum()
            hourly_gaps[slot] = gap
            hourly_excess[slot] = excess
        
        # Calculate gap contribution per shift (how much gap is in hours this shift covers)
        shift_gap_score = {}
        shift_excess_score = {}
        for shift_id, info in active_shifts.items():
            covered = get_shift_coverage_hours(info['start'], params)
            shift_gap_score[shift_id] = sum(hourly_gaps.get(h, 0) for h in covered)
            shift_excess_score[shift_id] = sum(hourly_excess.get(h, 0) for h in covered)
        
        # Track tried combinations to avoid repeating
        tried_combinations = set()
        no_improvement_count = 0
        max_no_improvement = 10
        
        # Step 2: Iteratively move DAs from excess shifts to gap shifts
        for iteration in range(iterations):
            # Update progress at start of each iteration
            if progress_callback:
                progress_callback(iteration + 1, iterations, best_fitness)
            
            # Sort shifts by gap score (high = needs more DAs)
            sorted_by_gap = sorted(shift_gap_score.items(), key=lambda x: x[1], reverse=True)
            # Sort by excess score (high = can spare DAs)
            sorted_by_excess = sorted(shift_excess_score.items(), key=lambda x: x[1], reverse=True)
            
            # Try multiple combinations, not just the top one
            found_improvement = False
            for high_gap_shift, gap_score in sorted_by_gap:
                high_idx = id_to_idx[high_gap_shift]
                if gap_score <= 0 or best[high_idx] >= 50:
                    continue
                    
                for low_gap_shift, excess_score in sorted_by_excess:
                    low_idx = id_to_idx[low_gap_shift]
                    if excess_score <= 0 or best[low_idx] <= 5:
                        continue
                    if low_gap_shift == high_gap_shift:
                        continue
                    
                    # Skip if we've tried this combination
                    combo_key = (low_gap_shift, high_gap_shift)
                    if combo_key in tried_combinations:
                        continue
                    tried_combinations.add(combo_key)
                    
                    # Transfer 1 DA worth of share (100/total_das %)
                    transfer_pct = min(100 / total_das, best[low_idx] - 5)
                    if transfer_pct <= 0:
                        continue
                    
                    # Test the transfer
                    test = best.copy()
                    test[low_idx] -= transfer_pct
                    test[high_idx] += transfer_pct
                    
                    test_fitness = evaluate_shift_shares_fitness(test, demand_df, das_df, store, params)
                    
                    if test_fitness < best_fitness:
                        best = test
                        best_fitness = test_fitness
                        history.append({'iteration': iteration + 1, 'best_fitness': best_fitness})
                        found_improvement = True
                        no_improvement_count = 0
                        
                        # Recalculate gap scores with new distribution
                        test_shares = {sorted_shift_ids[i]: best[i] for i in range(num_shifts)}
                        test_shifts = assign_shifts_fixed(da_list, store_demand, store, params, test_shares)
                        test_roster = fixed_generate_hourly_roster(test_shifts, store_demand, params)
                        
                        for slot in range(24):
                            slot_data = test_roster[test_roster['Slot'] == slot]
                            hourly_gaps[slot] = abs(slot_data[slot_data['Diff'] < 0]['Diff'].sum())
                            hourly_excess[slot] = slot_data[slot_data['Diff'] > 0]['Diff'].sum()
                        
                        for shift_id, info in active_shifts.items():
                            covered = get_shift_coverage_hours(info['start'], params)
                            shift_gap_score[shift_id] = sum(hourly_gaps.get(h, 0) for h in covered)
                            shift_excess_score[shift_id] = sum(hourly_excess.get(h, 0) for h in covered)
                        
                        # Reset tried combinations since scores changed
                        tried_combinations.clear()
                        break
                
                if found_improvement:
                    break
            
            if not found_improvement:
                no_improvement_count += 1
                if no_improvement_count >= max_no_improvement:
                    break
    
    elif algorithm == 'hillclimb':
        # HILL CLIMB OPTIMIZATION (random, fast)
        # Randomly transfers shares between shifts and keeps improvements
        active_shift_ids = list(active_shifts.keys())
        
        store_das = das_df[das_df['Store'] == store].copy()
        da_list = fixed_build_da_list(store_das)
        total_das = len(da_list)
        
        if total_das == 0:
            return {shift_id: default_share for shift_id in sorted_shift_ids}, float('inf'), [], False
        
        # Transfer amount per DA
        da_pct = 100 / total_das
        
        no_improvement_count = 0
        max_no_improvement = 20  # Stop after 20 iterations without improvement
        
        for iteration in range(iterations):
            # Update progress
            if progress_callback:
                progress_callback(iteration + 1, iterations, best_fitness)
            
            # Randomly select two different active shifts
            if len(active_shift_ids) < 2:
                break
            
            from_shift, to_shift = np.random.choice(active_shift_ids, 2, replace=False)
            from_idx = id_to_idx[from_shift]
            to_idx = id_to_idx[to_shift]
            
            # Random transfer amount (1-5 DAs worth)
            transfer_das = np.random.randint(1, 6)
            transfer_pct = da_pct * transfer_das
            
            # Check constraints (min 5%, max 50%)
            if best[from_idx] - transfer_pct < 5:
                transfer_pct = best[from_idx] - 5
            if best[to_idx] + transfer_pct > 50:
                transfer_pct = 50 - best[to_idx]
            
            if transfer_pct <= 0:
                no_improvement_count += 1
                if no_improvement_count >= max_no_improvement:
                    break
                continue
            
            # Test the transfer
            test = best.copy()
            test[from_idx] -= transfer_pct
            test[to_idx] += transfer_pct
            
            test_fitness = evaluate_shift_shares_fitness(test, demand_df, das_df, store, params)
            
            if test_fitness < best_fitness:
                best = test
                best_fitness = test_fitness
                history.append({'iteration': iteration + 1, 'best_fitness': best_fitness})
                no_improvement_count = 0
            else:
                no_improvement_count += 1
                if no_improvement_count >= max_no_improvement:
                    break
    
    else:
        # Genetic Algorithm (kept for backward compatibility, but slower)
        population_size = 15
        population = [current.copy()]
        
        for _ in range(population_size - 1):
            individual = current.copy()
            for j in range(num_shifts):
                individual[j] = max(0, individual[j] + np.random.uniform(-10, 10))
            total = sum(individual)
            individual = [x / total * 100 for x in individual]
            population.append(individual)
        
        for gen in range(generations):
            fitness_scores = [evaluate_shift_shares_fitness(ind, demand_df, das_df, store, params) 
                            for ind in population]
            
            gen_best_idx = np.argmin(fitness_scores)
            if fitness_scores[gen_best_idx] < best_fitness:
                best_fitness = fitness_scores[gen_best_idx]
                best = population[gen_best_idx].copy()
            
            history.append({'generation': gen + 1, 'best_fitness': best_fitness})
            
            if progress_callback:
                progress_callback(gen + 1, generations, best_fitness)
            
            sorted_indices = np.argsort(fitness_scores)
            new_population = [population[sorted_indices[0]].copy(), 
                            population[sorted_indices[1]].copy()]
            
            while len(new_population) < population_size:
                idx1, idx2 = np.random.choice(len(population), 2, replace=False)
                parent1 = population[idx1] if fitness_scores[idx1] < fitness_scores[idx2] else population[idx2]
                idx3, idx4 = np.random.choice(len(population), 2, replace=False)
                parent2 = population[idx3] if fitness_scores[idx3] < fitness_scores[idx4] else population[idx4]
                
                point = np.random.randint(1, num_shifts - 1) if num_shifts > 2 else 1
                child = parent1[:point] + parent2[point:]
                
                if np.random.random() < 0.3:
                    mut_idx = np.random.randint(0, num_shifts)
                    child[mut_idx] = max(0, child[mut_idx] + np.random.uniform(-15, 15))
                
                total = sum(child)
                if total > 0:
                    child = [x / total * 100 for x in child]
                    new_population.append(child)
            
            population = new_population
    
    improved = best_fitness < baseline
    
    # Map best values to actual shift IDs and normalize to 100%
    sorted_shift_ids = sorted(active_shifts.keys())
    total_share = sum(best)
    if total_share > 0:
        best_shares = {sorted_shift_ids[i]: best[i] / total_share * 100 for i in range(num_shifts)}
    else:
        best_shares = {shift_id: default_share for shift_id in sorted_shift_ids}
    
    return best_shares, best_fitness, history, improved


# =============================================================================
# FIXED SHIFTS SMART CHAIN OPTIMIZER
# =============================================================================

def fixed_calc_gap_fast(shifts_df, demand_df, store, params):
    """
    Fast gap calculation for fixed shifts optimizer iterations.
    Includes carryover support and overnight shift handling for zero quality loss.
    """
    from roster_engine_fixed_shifts import DAYS
    
    shift_hours = params.get('shift_hours', 10)
    store_demand = demand_df[demand_df['Store'] == store]
    
    # Build demand lookup
    demand_lookup = {}
    for _, r in store_demand.iterrows():
        day = str(r['Day'])[:3]
        slot = int(r['Slot'])
        demand_lookup[(day, slot)] = r['DA Required'] if pd.notna(r['DA Required']) else 0
    
    # Initialize coverage
    coverage = {d: {h: 0 for h in range(24)} for d in DAYS}
    
    # Handle carryover for Sunday (from Saturday night shifts)
    carryover_mode = params.get('carryover_mode', 'auto')
    sunday_carryover_das = params.get('sunday_carryover_das', 0)
    carryover_excel_data = params.get('carryover_excel_data', [])
    store_carryover_das = [c for c in carryover_excel_data if c.get('Store') == store]
    skip_sunday_overnight = params.get('skip_sunday_overnight', False)
    
    # Add carryover coverage for Sunday early morning (manual/excel mode)
    if carryover_mode == 'manual' and sunday_carryover_das > 0:
        for h in range(5):  # 00:00-04:00
            coverage['Sun'][h] += sunday_carryover_das
    elif carryover_mode == 'excel' and store_carryover_das:
        for carryover_da in store_carryover_das:
            sat_end = carryover_da.get('Sat_Shift_End', 5)
            for h in range(sat_end):
                coverage['Sun'][h] += 1
    
    max_continuous = params.get('max_continuous', 5)
    
    # Process each day and calculate coverage
    for day_idx, day in enumerate(DAYS):
        prev_day = DAYS[(day_idx - 1) % 7]
        next_day = DAYS[(day_idx + 1) % 7]
        
        # Get shifts for this day
        day_shifts = shifts_df[shifts_df['Day'] == day]
        
        for _, s in day_shifts.iterrows():
            if s['Is_Day_Off'] or pd.isna(s['Shift_Start']):
                continue
            
            start = int(s['Shift_Start'])
            brk = int(s['Break_Hour']) if pd.notna(s['Break_Hour']) else calculate_valid_break_hour(start, shift_hours, max_continuous)
            end = int(s['Shift_End']) if pd.notna(s['Shift_End']) else (start + shift_hours) % 24
            
            # Calculate actual shift duration from start/end
            if end > start:
                actual_hours = end - start
            elif end == start:
                actual_hours = shift_hours
            else:
                # Overnight: e.g. start=19, end=5 → 10 hours
                actual_hours = (24 - start) + end
            
            # Check if overnight shift (ends next day)
            is_overnight = (end != 0 and end < start) or (end == 0 and start > 0 and actual_hours > 1)
            
            # Skip Sunday overnight carryover if using manual/excel mode or skip_sunday_overnight
            skip_next_day_carryover = (next_day == 'Sun' and 
                                       (carryover_mode in ['manual', 'excel'] or skip_sunday_overnight))
            
            for h in range(actual_hours):
                hr = (start + h) % 24
                if hr == brk:
                    continue
                
                if is_overnight and hr < start:
                    # This hour is on the next day (overnight carryover)
                    # The DA is working TODAY, so coverage goes to NEXT day
                    if not skip_next_day_carryover:
                        coverage[next_day][hr] += 1
                else:
                    # Normal same-day coverage
                    coverage[day][hr] += 1
    
    # Calculate gap
    gap = 0
    for d in DAYS:
        for h in range(24):
            required = demand_lookup.get((d, h), 0)
            rostered = coverage[d][h]
            if rostered < required:
                gap += required - rostered
    
    return gap

def fixed_calc_gap(shifts_df, demand_df, store, params):
    """Calculate total gap for fixed shifts using full roster (for final display)."""
    store_demand = demand_df[demand_df['Store'] == store]
    roster = fixed_generate_hourly_roster(shifts_df, store_demand, params)
    if roster is None or roster.empty:
        return float('inf')
    return abs(roster[roster['Diff'] < 0]['Diff'].sum())

def fixed_optimize_shift_rebalancing(shifts_df, demand_df, store, params):
    """
    Rebalance DAs between shifts based on gap analysis.
    Move DAs from shifts covering excess hours to shifts covering gap hours.
    DETERMINISTIC - not random.
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0
    
    from roster_engine_fixed_shifts import get_active_shifts, get_shift_coverage_hours, DAYS
    
    active_shifts = get_active_shifts(params)
    shift_hours = params.get('shift_hours', 10)
    store_demand = demand_df[demand_df['Store'] == store]
    
    # Use fast gap calculation for baseline
    old_gap = fixed_calc_gap_fast(shifts_df, demand_df, store, params)
    
    # Generate roster once for analysis (not for gap calc)
    roster = fixed_generate_hourly_roster(shifts_df, store_demand, params)
    if roster is None or roster.empty:
        return shifts_df, 0
    
    # Calculate gap contribution per shift
    shift_gap_score = {}
    shift_excess_score = {}
    for shift_id, info in active_shifts.items():
        covered = get_shift_coverage_hours(info['start'], params)
        gap_sum = 0
        excess_sum = 0
        for hour in covered:
            hour_data = roster[roster['Slot'] == hour]
            gap_sum += abs(hour_data[hour_data['Diff'] < 0]['Diff'].sum())
            excess_sum += hour_data[hour_data['Diff'] > 0]['Diff'].sum()
        shift_gap_score[shift_id] = gap_sum
        shift_excess_score[shift_id] = excess_sum
    
    # Get current DA count per shift
    current_dist = shifts_df.groupby('Shift_ID')['DA_ID'].nunique().to_dict()
    
    # Sort by gap (high = needs more DAs) and excess (high = can spare DAs)
    sorted_by_gap = sorted(shift_gap_score.items(), key=lambda x: x[1], reverse=True)
    sorted_by_excess = sorted(shift_excess_score.items(), key=lambda x: x[1], reverse=True)
    
    # Try moving 1 DA from high-excess shift to high-gap shift
    for to_shift, gap in sorted_by_gap:
        if gap <= 0:
            continue
        for from_shift, excess in sorted_by_excess:
            if excess <= 0 or from_shift == to_shift:
                continue
            if current_dist.get(from_shift, 0) <= 1:
                continue  # Keep at least 1 DA per shift
            
            # Find a DA to move
            from_das = shifts_df[shifts_df['Shift_ID'] == from_shift]['DA_ID'].unique()
            if len(from_das) <= 1:
                continue
            
            da_to_move = from_das[0]
            to_shift_info = active_shifts[to_shift]
            new_start = to_shift_info['start']
            new_end = (new_start + shift_hours) % 24
            max_continuous = params.get('max_continuous', 5)
            new_break = calculate_valid_break_hour(new_start, shift_hours, max_continuous)
            
            # Create test shifts
            test_shifts = shifts_df.copy()
            mask = test_shifts['DA_ID'] == da_to_move
            for idx in test_shifts[mask].index:
                if not test_shifts.loc[idx, 'Is_Day_Off']:
                    test_shifts.loc[idx, 'Shift_ID'] = to_shift
                    test_shifts.loc[idx, 'Shift_Name'] = to_shift_info['name']
                    test_shifts.loc[idx, 'Shift_Start'] = new_start
                    test_shifts.loc[idx, 'Shift_End'] = new_end
                    test_shifts.loc[idx, 'Break_Hour'] = new_break
            
            new_gap = fixed_calc_gap_fast(test_shifts, demand_df, store, params)
            
            if new_gap < old_gap:
                return test_shifts, 1
    
    return shifts_df, 0

def fixed_optimize_off_day_redistribution(shifts_df, demand_df, store, params):
    """
    Redistribute off days to reduce gaps on high-demand days.
    Move off days from high-gap days to low-gap days.
    DETERMINISTIC - not random.
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0
    
    from roster_engine_fixed_shifts import DAYS
    
    store_demand = demand_df[demand_df['Store'] == store]
    
    # Use fast gap calculation
    old_gap = fixed_calc_gap_fast(shifts_df, demand_df, store, params)
    
    # Generate roster once for day analysis
    roster = fixed_generate_hourly_roster(shifts_df, store_demand, params)
    if roster is None or roster.empty:
        return shifts_df, 0
    
    # Calculate gap per day
    day_gaps = {}
    for day in DAYS:
        day_data = roster[roster['Day'] == day]
        day_gaps[day] = abs(day_data[day_data['Diff'] < 0]['Diff'].sum())
    
    # Sort days by gap (excluding Fri/Sat which must be working)
    available_days = [d for d in DAYS if d not in ['Fri', 'Sat']]
    sorted_days = sorted([(d, day_gaps[d]) for d in available_days], key=lambda x: x[1], reverse=True)
    
    high_gap_days = [d for d, g in sorted_days[:2] if g > 0]
    low_gap_days = [d for d, g in sorted_days[-2:]]
    
    if not high_gap_days or not low_gap_days:
        return shifts_df, 0
    
    # Find DAs with off day on high-gap day
    for high_day in high_gap_days:
        das_off_on_high = shifts_df[(shifts_df['Day'] == high_day) & (shifts_df['Is_Day_Off'])]['DA_ID'].unique()
        
        for da_id in das_off_on_high:
            for low_day in low_gap_days:
                if low_day == high_day:
                    continue
                
                # Get DA's shift info
                da_working = shifts_df[(shifts_df['DA_ID'] == da_id) & (~shifts_df['Is_Day_Off'])]
                if da_working.empty:
                    continue
                
                shift_info = da_working.iloc[0]
                
                # Create test shifts - swap off day
                test_shifts = shifts_df.copy()
                
                # Set high_day to working
                high_idx = test_shifts[(test_shifts['DA_ID'] == da_id) & (test_shifts['Day'] == high_day)].index
                if len(high_idx) > 0:
                    test_shifts.loc[high_idx[0], 'Is_Day_Off'] = False
                    test_shifts.loc[high_idx[0], 'Shift_ID'] = shift_info['Shift_ID']
                    test_shifts.loc[high_idx[0], 'Shift_Name'] = shift_info['Shift_Name']
                    test_shifts.loc[high_idx[0], 'Shift_Start'] = shift_info['Shift_Start']
                    test_shifts.loc[high_idx[0], 'Shift_End'] = shift_info['Shift_End']
                    test_shifts.loc[high_idx[0], 'Break_Hour'] = shift_info['Break_Hour']
                
                # Set low_day to off
                low_idx = test_shifts[(test_shifts['DA_ID'] == da_id) & (test_shifts['Day'] == low_day)].index
                if len(low_idx) > 0:
                    test_shifts.loc[low_idx[0], 'Is_Day_Off'] = True
                    test_shifts.loc[low_idx[0], 'Shift_Start'] = None
                    test_shifts.loc[low_idx[0], 'Shift_End'] = None
                    test_shifts.loc[low_idx[0], 'Break_Hour'] = None
                
                new_gap = fixed_calc_gap_fast(test_shifts, demand_df, store, params)
                
                if new_gap < old_gap:
                    return test_shifts, 1
    
    return shifts_df, 0

def fixed_optimize_break_timing(shifts_df, demand_df, store, params):
    """
    Adjust break timing (hour 4 or 5) to reduce gaps.
    DETERMINISTIC - tests DAs systematically but limits to first improvement per shift.
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0
    
    optimized = shifts_df.copy()
    changes = 0
    best_gap = fixed_calc_gap_fast(shifts_df, demand_df, store, params)
    
    # Group DAs by shift to test more efficiently
    shift_ids = optimized['Shift_ID'].dropna().unique()
    
    for shift_id in shift_ids:
        # Get DAs in this shift
        shift_das = optimized[optimized['Shift_ID'] == shift_id]['DA_ID'].unique()
        if len(shift_das) == 0:
            continue
        
        # Get shift start for this shift
        sample_da = shift_das[0]
        da_working = optimized[(optimized['DA_ID'] == sample_da) & (~optimized['Is_Day_Off'])]
        if da_working.empty:
            continue
        
        shift_start = da_working.iloc[0]['Shift_Start']
        if pd.isna(shift_start):
            continue
        shift_start = int(shift_start)
        
        current_break = da_working.iloc[0]['Break_Hour']
        if pd.isna(current_break):
            continue
        current_break = int(current_break)
        
        # Try break at hour 4 or 5 for ALL DAs in this shift at once
        for break_offset in [4, 5]:
            new_break = (shift_start + break_offset) % 24
            if new_break == current_break:
                continue
            
            test_shifts = optimized.copy()
            # Update all DAs in this shift
            for da_id in shift_das:
                mask = (test_shifts['DA_ID'] == da_id) & (~test_shifts['Is_Day_Off'])
                test_shifts.loc[mask, 'Break_Hour'] = new_break
            
            test_gap = fixed_calc_gap_fast(test_shifts, demand_df, store, params)
            
            if test_gap < best_gap:
                optimized = test_shifts
                best_gap = test_gap
                changes += 1
                break  # Move to next shift
    
    return optimized, changes

def fixed_smart_chain_optimizer(shifts_df, demand_df, store, params, progress_callback=None):
    """
    Fixed Shifts Smart Chain Optimizer.
    Runs: Shift Rebalancing → Off-Day Redistribution → Break Timing
    Repeats until no improvement for 5 consecutive runs.
    
    Args:
        progress_callback: Optional callback(current, total, best_gap) for progress updates
    
    Returns: (optimized_shifts, total_changes, iterations_run)
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0, 0
    
    best_shifts = shifts_df.copy()
    best_gap = fixed_calc_gap_fast(best_shifts, demand_df, store, params)
    total_changes = 0
    iterations = 0
    no_improvement = 0
    max_iterations = 30
    
    optimizers = [
        ('Shift Rebalancing', fixed_optimize_shift_rebalancing),
        ('Off-Day Redistribution', fixed_optimize_off_day_redistribution),
        ('Break Timing', fixed_optimize_break_timing),
    ]
    
    while no_improvement < 5 and iterations < max_iterations:
        iterations += 1
        improved = False
        
        # Update progress
        if progress_callback:
            progress_callback(iterations, max_iterations, best_gap)
        
        for name, opt_func in optimizers:
            test_shifts, changes = opt_func(best_shifts.copy(), demand_df, store, params)
            test_gap = fixed_calc_gap_fast(test_shifts, demand_df, store, params)
            
            if test_gap < best_gap:
                best_shifts = test_shifts
                best_gap = test_gap
                total_changes += changes
                improved = True
        
        if improved:
            no_improvement = 0
        else:
            no_improvement += 1
    
    return best_shifts, total_changes, iterations

def run_shift_timing_optimizer(demand_df, das_df, store, params, current_shares=None,
                               shift_to_optimize=None, baseline_gap=None,
                               iterations=30, progress_callback=None):
    """
    Optimize shift start times (±1-2 hours from default).
    
    Args:
        shift_to_optimize: Specific shift ID (1-6) or None for all shifts
        baseline_gap: Current gap - only return if better
        current_shares: Current shift share distribution
    
    Returns: best_timings (dict), best_fitness, history, improved (bool)
    """
    from roster_engine_fixed_shifts import FIXED_SHIFTS
    
    # Get current timings from FIXED_SHIFTS (may have been updated by previous optimization)
    current_timings = {k: v['start'] for k, v in FIXED_SHIFTS.items()}
    
    # Calculate baseline
    if baseline_gap is not None:
        baseline = baseline_gap
    else:
        baseline = evaluate_shift_timing_fitness(current_timings, current_shares, 
                                                  demand_df, das_df, store, params)
    
    best_timings = current_timings.copy()
    best_fitness = baseline
    history = []
    
    # Determine which shifts to optimize
    if shift_to_optimize:
        shifts_to_try = [shift_to_optimize]
    else:
        # Use all shifts from current_timings (dynamic, not hardcoded)
        shifts_to_try = list(current_timings.keys())
    
    for i in range(iterations):
        # Pick a random shift to adjust
        shift_id = np.random.choice(shifts_to_try)
        
        # Try ±1 or ±2 hours
        delta = np.random.choice([-2, -1, 1, 2])
        new_start = (current_timings[shift_id] + delta) % 24
        
        # Don't allow negative or overlapping too much
        if new_start < 0:
            new_start = 0
        
        test_timings = current_timings.copy()
        test_timings[shift_id] = new_start
        
        test_fitness = evaluate_shift_timing_fitness(test_timings, current_shares,
                                                      demand_df, das_df, store, params)
        
        # Only accept if strictly better
        if test_fitness < best_fitness:
            current_timings = test_timings.copy()
            best_timings = test_timings.copy()
            best_fitness = test_fitness
        
        history.append({'iteration': i + 1, 'best_fitness': best_fitness})
        
        if progress_callback:
            progress_callback(i + 1, iterations, best_fitness)
    
    improved = best_fitness < baseline
    return best_timings, best_fitness, history, improved

def run_shift_timing_optimizer_genetic(demand_df, das_df, store, params, current_shares=None,
                                       shift_to_optimize=None, baseline_gap=None,
                                       generations=20, progress_callback=None):
    """
    Optimize shift start times using genetic algorithm.
    """
    from roster_engine_fixed_shifts import FIXED_SHIFTS
    
    # Get current timings from FIXED_SHIFTS (may have been updated by previous optimization)
    current_timings = {k: v['start'] for k, v in FIXED_SHIFTS.items()}
    
    # Calculate baseline
    if baseline_gap is not None:
        baseline = baseline_gap
    else:
        baseline = evaluate_shift_timing_fitness(current_timings, current_shares, 
                                                  demand_df, das_df, store, params)
    
    # Determine which shifts to optimize
    if shift_to_optimize:
        shifts_to_try = [shift_to_optimize]
    else:
        # Use all shifts from current_timings (dynamic, not hardcoded)
        shifts_to_try = list(current_timings.keys())
    
    # Valid hour ranges for each shift (±3 hours from current start time)
    # Dynamically generate based on current timings
    valid_ranges = {}
    for shift_id, start in current_timings.items():
        # Allow ±3 hours from current start, wrapping around midnight
        valid_ranges[shift_id] = [(start + delta) % 24 for delta in range(-3, 4)]
    
    # Initialize population
    population_size = 12
    population = []
    
    # Seed with current timings and variations
    population.append(current_timings.copy())
    for _ in range(population_size - 1):
        individual = current_timings.copy()
        for shift_id in shifts_to_try:
            individual[shift_id] = np.random.choice(valid_ranges[shift_id])
        population.append(individual)
    
    best_timings = current_timings.copy()
    best_fitness = baseline
    history = []
    
    for gen in range(generations):
        # Evaluate fitness
        fitness_scores = [evaluate_shift_timing_fitness(ind, current_shares, demand_df, das_df, store, params) 
                         for ind in population]
        
        # Track best
        gen_best_idx = np.argmin(fitness_scores)
        if fitness_scores[gen_best_idx] < best_fitness:
            best_fitness = fitness_scores[gen_best_idx]
            best_timings = population[gen_best_idx].copy()
        
        history.append({'generation': gen + 1, 'best_fitness': best_fitness})
        
        if progress_callback:
            progress_callback(gen + 1, generations, best_fitness)
        
        # Selection and reproduction
        sorted_indices = np.argsort(fitness_scores)
        new_population = [population[sorted_indices[0]].copy(), 
                         population[sorted_indices[1]].copy()]
        
        while len(new_population) < population_size:
            # Tournament selection
            idx1, idx2 = np.random.choice(len(population), 2, replace=False)
            parent1 = population[idx1] if fitness_scores[idx1] < fitness_scores[idx2] else population[idx2]
            idx3, idx4 = np.random.choice(len(population), 2, replace=False)
            parent2 = population[idx3] if fitness_scores[idx3] < fitness_scores[idx4] else population[idx4]
            
            # Crossover
            child = parent1.copy()
            for shift_id in shifts_to_try:
                if np.random.random() < 0.5:
                    child[shift_id] = parent2[shift_id]
            
            # Mutation
            if np.random.random() < 0.3:
                mut_shift = np.random.choice(shifts_to_try)
                child[mut_shift] = np.random.choice(valid_ranges[mut_shift])
            
            new_population.append(child)
        
        population = new_population
    
    improved = best_fitness < baseline
    return best_timings, best_fitness, history, improved

def evaluate_fitness(priorities, demand_df, das_df, store, params, day_multipliers, scale_mode, intensity, objective='gap', return_data=False):
    """Evaluate fitness of a priority configuration.
    
    If return_data=True, returns (fitness, roster_df, shifts_df) tuple.
    """
    slot_priorities = {h: priorities[h] for h in range(24)}
    
    try:
        roster_df, shifts_df = generate_roster_with_priorities(
            demand_df, das_df, store, slot_priorities, params, day_multipliers, scale_mode, intensity
        )
        
        if roster_df is None or roster_df.empty:
            if return_data:
                return float('inf'), None, None
            return float('inf')
        
        total_gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum())
        total_excess = roster_df[roster_df['Diff'] > 0]['Diff'].sum()
        
        # Calculate priority-weighted gap
        priority_weighted_gap = 0
        for _, row in roster_df.iterrows():
            if row['Diff'] < 0:
                priority = slot_priorities.get(row['Slot'], 5)
                priority_weighted_gap += abs(row['Diff']) * (priority / 5)
        
        if objective == 'gap':
            fitness = total_gap
        elif objective == 'weighted_gap':
            fitness = priority_weighted_gap
        elif objective == 'balanced':
            # Balance gap and excess
            fitness = total_gap + total_excess * 0.1
        else:
            fitness = total_gap
        
        if return_data:
            return fitness, roster_df, shifts_df
        return fitness
            
    except Exception as e:
        if return_data:
            return float('inf'), None, None
        return float('inf')

def crossover(parent1, parent2):
    """Single-point crossover between two parents."""
    point = np.random.randint(1, 23)
    child1 = np.concatenate([parent1[:point], parent2[point:]])
    child2 = np.concatenate([parent2[:point], parent1[point:]])
    return child1, child2

def mutate(individual, mutation_rate=0.1):
    """Mutate an individual with given probability."""
    mutated = individual.copy()
    for i in range(24):
        if np.random.random() < mutation_rate:
            # Mutate by +/- 1-3
            change = np.random.randint(-3, 4)
            mutated[i] = np.clip(mutated[i] + change, 0, 10)
    return mutated

def run_genetic_optimizer(demand_df, das_df, store, params, day_multipliers, scale_mode, intensity,
                          population_size=20, generations=30, objective='gap',
                          progress_callback=None, current_priorities=None):
    """
    Run genetic algorithm to find optimal priority distribution.
    Seeds population with demand-aware patterns for faster convergence.
    
    Returns: best_priorities, best_fitness, history, best_roster_df, best_shifts_df
    """
    # Build demand-aware seed pattern
    store_demand = demand_df[demand_df['Store'] == store]
    avg_demand = {}
    for h in range(24):
        vals = store_demand[store_demand['Slot'] == h]['DA Required'].values
        avg_demand[h] = float(np.mean(vals)) if len(vals) > 0 else 0
    max_d = max(avg_demand.values()) if avg_demand else 1
    # Map demand to priority: high demand → high priority
    demand_pattern = np.array([min(10, max(0, int(avg_demand[h] / max(max_d, 1) * 10))) for h in range(24)])
    
    # Initialize population with diverse strategies
    population = []
    
    # Seed with current priorities first (if available)
    if current_priorities is not None:
        current_arr = np.array([current_priorities.get(h, 5) for h in range(24)])
        population.append(current_arr)
        # Variations of current
        for _ in range(2):
            variant = current_arr.copy()
            for _ in range(3):
                slot = np.random.randint(0, 24)
                variant[slot] = np.clip(variant[slot] + np.random.choice([-1, 1]), 0, 10)
            population.append(variant)
    
    # Demand-aware seeds (much better than arbitrary patterns)
    population.append(demand_pattern.copy())  # Direct demand mapping
    # Demand-inverted (boost low-demand hours to spread DAs)
    population.append(np.clip(10 - demand_pattern, 0, 10))
    # Demand-shifted variants (boost hours just before peak)
    shifted = np.roll(demand_pattern, -1)
    population.append(shifted)
    shifted2 = np.roll(demand_pattern, -2)
    population.append(shifted2)
    
    # Balanced baseline
    population.append(np.array([5] * 24))
    
    # Fill rest with random but biased toward demand shape
    while len(population) < population_size:
        noise = np.random.randint(-2, 3, 24)
        individual = np.clip(demand_pattern + noise, 0, 10)
        population.append(individual)
    
    # Trim to population_size if we added too many
    population = population[:population_size]
    
    # Track best
    best_fitness = float('inf')
    best_individual = None
    best_roster_df = None
    best_shifts_df = None
    history = []
    
    for gen in range(generations):
        # Evaluate fitness - get full data for each individual
        fitness_scores = []
        roster_data = []
        for individual in population:
            fitness, roster, shifts = evaluate_fitness(
                individual, demand_df, das_df, store, params, day_multipliers, scale_mode, intensity, objective, return_data=True
            )
            fitness_scores.append(fitness)
            roster_data.append((roster, shifts))
        
        # Track best
        gen_best_idx = np.argmin(fitness_scores)
        gen_best_fitness = fitness_scores[gen_best_idx]
        
        if gen_best_fitness < best_fitness:
            best_fitness = gen_best_fitness
            best_individual = population[gen_best_idx].copy()
            # Store the EXACT roster and shifts from this evaluation (no re-evaluation)
            best_roster_df, best_shifts_df = roster_data[gen_best_idx]
        
        history.append({
            'generation': gen + 1,
            'best_fitness': gen_best_fitness,
            'avg_fitness': np.mean([f for f in fitness_scores if f != float('inf')]),
            'overall_best': best_fitness
        })
        
        if progress_callback:
            should_stop = progress_callback(gen + 1, generations, best_fitness)
            if should_stop:
                # Early stop requested - return best found so far
                return best_individual, best_fitness, history, best_roster_df, best_shifts_df
        
        # Selection (tournament)
        new_population = []
        
        # Elitism: keep top 2
        sorted_indices = np.argsort(fitness_scores)
        new_population.append(population[sorted_indices[0]].copy())
        new_population.append(population[sorted_indices[1]].copy())
        
        # Generate rest through crossover and mutation
        while len(new_population) < population_size:
            # Tournament selection
            idx1, idx2 = np.random.choice(len(population), 2, replace=False)
            parent1 = population[idx1] if fitness_scores[idx1] < fitness_scores[idx2] else population[idx2]
            
            idx3, idx4 = np.random.choice(len(population), 2, replace=False)
            parent2 = population[idx3] if fitness_scores[idx3] < fitness_scores[idx4] else population[idx4]
            
            # Crossover
            if np.random.random() < 0.8:
                child1, child2 = crossover(parent1, parent2)
            else:
                child1, child2 = parent1.copy(), parent2.copy()
            
            # Mutation
            child1 = mutate(child1, mutation_rate=0.15)
            child2 = mutate(child2, mutation_rate=0.15)
            
            new_population.append(child1)
            if len(new_population) < population_size:
                new_population.append(child2)
        
        population = new_population
    
    return best_individual, best_fitness, history, best_roster_df, best_shifts_df

def run_quick_optimizer(demand_df, das_df, store, params, day_multipliers, scale_mode, intensity,
                        iterations=50, objective='gap', progress_callback=None, current_priorities=None):
    """
    Smart demand-aware tuner optimizer.
    
    Phase 1 (Demand-Guided): Analyze roster gaps/excess and directly adjust priorities
    to push DAs from excess hours toward gap hours. Much faster convergence than random.
    
    Phase 2 (Fine-Tuning): Hill-climbing with targeted moves on remaining gap/excess hours.
    
    Returns: best_priorities, best_fitness, history, best_roster_df, best_shifts_df
    """
    # Start from current priorities if provided, otherwise balanced
    if current_priorities is not None:
        current = np.array([current_priorities.get(h, 5) for h in range(24)])
    else:
        current = np.array([5] * 24)
    current_fitness, current_roster, current_shifts = evaluate_fitness(
        current, demand_df, das_df, store, params, day_multipliers, scale_mode, intensity, objective, return_data=True
    )
    
    best = current.copy()
    best_fitness = current_fitness
    best_roster_df = current_roster
    best_shifts_df = current_shifts
    history = []
    
    def _analyze_roster(roster_df):
        """Analyze roster to find gap hours and excess hours."""
        if roster_df is None or roster_df.empty:
            return {}, {}
        gap_by_hour = {}
        excess_by_hour = {}
        for _, row in roster_df.iterrows():
            h = int(row['Slot'])
            diff = row['Diff']
            if diff < 0:
                gap_by_hour[h] = gap_by_hour.get(h, 0) + abs(diff)
            elif diff > 0:
                excess_by_hour[h] = excess_by_hour.get(h, 0) + diff
        return gap_by_hour, excess_by_hour
    
    # ---- PHASE 1: Demand-guided priority adjustment ----
    phase1_iters = min(iterations // 2, 25)
    
    for i in range(phase1_iters):
        gap_by_hour, excess_by_hour = _analyze_roster(current_roster)
        
        if not gap_by_hour:
            # No gaps — we're done with phase 1
            history.append({'iteration': i + 1, 'current_fitness': current_fitness, 'best_fitness': best_fitness})
            if progress_callback:
                should_stop = progress_callback(i + 1, iterations, best_fitness)
                if should_stop:
                    return best, best_fitness, history, best_roster_df, best_shifts_df
            break
        
        neighbor = current.copy()
        
        # Sort gap hours by severity (worst first)
        sorted_gaps = sorted(gap_by_hour.items(), key=lambda x: -x[1])
        sorted_excess = sorted(excess_by_hour.items(), key=lambda x: -x[1])
        
        # Boost top gap hours, reduce top excess hours
        moves_this_iter = min(3, max(1, len(sorted_gaps)))
        for j in range(moves_this_iter):
            if j < len(sorted_gaps):
                gap_hour = sorted_gaps[j][0]
                # Boost gap hour priority (bigger boost for bigger gaps)
                boost = min(2, max(1, int(sorted_gaps[j][1] / 5)))
                neighbor[gap_hour] = min(10, neighbor[gap_hour] + boost)
            if j < len(sorted_excess):
                exc_hour = sorted_excess[j][0]
                # Reduce excess hour priority
                reduce = min(2, max(1, int(sorted_excess[j][1] / 5)))
                neighbor[exc_hour] = max(0, neighbor[exc_hour] - reduce)
        
        neighbor_fitness, neighbor_roster, neighbor_shifts = evaluate_fitness(
            neighbor, demand_df, das_df, store, params, day_multipliers, scale_mode, intensity, objective, return_data=True
        )
        
        if neighbor_fitness < current_fitness:
            current = neighbor
            current_fitness = neighbor_fitness
            current_roster = neighbor_roster
            if current_fitness < best_fitness:
                best = current.copy()
                best_fitness = current_fitness
                best_roster_df = neighbor_roster
                best_shifts_df = neighbor_shifts
        elif neighbor_fitness == current_fitness:
            # Accept lateral moves in phase 1 to explore
            current = neighbor
            current_roster = neighbor_roster
        
        history.append({'iteration': i + 1, 'current_fitness': current_fitness, 'best_fitness': best_fitness})
        
        if progress_callback:
            should_stop = progress_callback(i + 1, iterations, best_fitness)
            if should_stop:
                return best, best_fitness, history, best_roster_df, best_shifts_df
    
    # ---- PHASE 2: Targeted hill-climbing on remaining gaps ----
    phase2_start = len(history)
    remaining_iters = iterations - phase2_start
    
    for i in range(remaining_iters):
        iter_num = phase2_start + i + 1
        gap_by_hour, excess_by_hour = _analyze_roster(current_roster)
        
        neighbor = current.copy()
        
        if gap_by_hour and np.random.random() < 0.7:
            # 70% of the time: targeted move on a gap/excess hour
            if np.random.random() < 0.5 and gap_by_hour:
                # Boost a gap hour
                gap_hours = list(gap_by_hour.keys())
                gap_weights = [gap_by_hour[h] for h in gap_hours]
                total_w = sum(gap_weights)
                probs = [w / total_w for w in gap_weights] if total_w > 0 else None
                slot = np.random.choice(gap_hours, p=probs)
                change = np.random.choice([1, 2])
                neighbor[slot] = min(10, neighbor[slot] + change)
            elif excess_by_hour:
                # Reduce an excess hour
                exc_hours = list(excess_by_hour.keys())
                exc_weights = [excess_by_hour[h] for h in exc_hours]
                total_w = sum(exc_weights)
                probs = [w / total_w for w in exc_weights] if total_w > 0 else None
                slot = np.random.choice(exc_hours, p=probs)
                change = np.random.choice([-1, -2])
                neighbor[slot] = max(0, neighbor[slot] + change)
        else:
            # 30%: random exploration (escape local minima)
            slot = np.random.randint(0, 24)
            change = np.random.choice([-2, -1, 1, 2])
            neighbor[slot] = np.clip(neighbor[slot] + change, 0, 10)
        
        neighbor_fitness, neighbor_roster, neighbor_shifts = evaluate_fitness(
            neighbor, demand_df, das_df, store, params, day_multipliers, scale_mode, intensity, objective, return_data=True
        )
        
        # Simulated annealing acceptance
        temperature = 1.0 - (i / max(1, remaining_iters))
        if neighbor_fitness < current_fitness or np.random.random() < temperature * 0.05:
            current = neighbor
            current_fitness = neighbor_fitness
            current_roster = neighbor_roster
            
            if current_fitness < best_fitness:
                best = current.copy()
                best_fitness = current_fitness
                best_roster_df = neighbor_roster
                best_shifts_df = neighbor_shifts
        
        history.append({'iteration': iter_num, 'current_fitness': current_fitness, 'best_fitness': best_fitness})
        
        if progress_callback:
            should_stop = progress_callback(iter_num, iterations, best_fitness)
            if should_stop:
                return best, best_fitness, history, best_roster_df, best_shifts_df
    
    return best, best_fitness, history, best_roster_df, best_shifts_df


def evaluate_fitness_tunable(priorities_dict, demand_df, das_df, store, params, objective='gap', return_data=False):
    """Evaluate fitness using the v12.5 tunable engine with a priorities dict."""
    try:
        store_das = das_df[das_df['Store'] == store].copy()
        if store_das.empty or store_das['DA_Count'].sum() == 0:
            return (float('inf'), None, None) if return_data else float('inf')

        engine_params = v15_get_params({
            'night_shift_enabled': params.get('night_shift', True),
            'flexible_day_off': params.get('flexible_day_off', False),
            'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
            'carryover_mode': params.get('carryover_mode', 'auto'),
            'sunday_carryover_das': params.get('sunday_carryover_das', 0),
            'carryover_excel_data': params.get('carryover_excel_data', []),
            'shift_hours': params.get('shift_hours', 10),
            'break_hours': params.get('break_hours', 1),
            'max_continuous': params.get('max_continuous', 5),
            'min_rest': params.get('min_rest', 12),
            'working_days': params.get('working_days', 6),
            'fixed_start_optimizer': params.get('fixed_start_optimizer', 'post_off'),
            'max_shifts': params.get('max_shifts', 0),
            'priorities': priorities_dict,
        })

        da_list = v15_build_da_list(store_das)
        store_demand = demand_df[demand_df['Store'] == store]
        shifts_df = v15_assign_shifts(da_list, store_demand, None, engine_params)
        roster_df = v15_generate_hourly_roster(shifts_df, store_demand, engine_params)

        if roster_df is None or roster_df.empty:
            return (float('inf'), None, None) if return_data else float('inf')

        total_gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum())
        total_excess = roster_df[roster_df['Diff'] > 0]['Diff'].sum()

        if objective == 'balanced':
            fitness = total_gap + total_excess * 0.1
        else:
            fitness = total_gap

        return (fitness, roster_df, shifts_df) if return_data else fitness
    except Exception:
        return (float('inf'), None, None) if return_data else float('inf')


def run_quick_optimizer_tunable(demand_df, das_df, store, params,
                                iterations=50, objective='gap',
                                progress_callback=None, current_priorities=None):
    """Quick optimizer for the tunable engine. Searches priorities in [-10, +10]."""
    if current_priorities is not None:
        current = np.array([current_priorities.get(h, 0) for h in range(24)])
    else:
        current = np.array([0] * 24)

    cur_dict = {h: int(current[h]) for h in range(24)}
    current_fitness, current_roster, current_shifts = evaluate_fitness_tunable(
        cur_dict, demand_df, das_df, store, params, objective, return_data=True
    )

    best = current.copy()
    best_fitness = current_fitness
    best_roster_df = current_roster
    best_shifts_df = current_shifts
    history = []

    def _analyze_roster(roster_df):
        if roster_df is None or roster_df.empty:
            return {}, {}
        gap_by_hour, excess_by_hour = {}, {}
        for _, row in roster_df.iterrows():
            h = int(row['Slot'])
            diff = row['Diff']
            if diff < 0:
                gap_by_hour[h] = gap_by_hour.get(h, 0) + abs(diff)
            elif diff > 0:
                excess_by_hour[h] = excess_by_hour.get(h, 0) + diff
        return gap_by_hour, excess_by_hour

    phase1_iters = min(iterations // 2, 25)
    for i in range(phase1_iters):
        gap_by_hour, excess_by_hour = _analyze_roster(current_roster)
        if not gap_by_hour:
            history.append({'iteration': i + 1, 'current_fitness': current_fitness, 'best_fitness': best_fitness})
            if progress_callback and progress_callback(i + 1, iterations, best_fitness):
                return best, best_fitness, history, best_roster_df, best_shifts_df
            break

        neighbor = current.copy()
        sorted_gaps = sorted(gap_by_hour.items(), key=lambda x: -x[1])
        sorted_excess = sorted(excess_by_hour.items(), key=lambda x: -x[1])
        moves = min(3, max(1, len(sorted_gaps)))
        for j in range(moves):
            if j < len(sorted_gaps):
                h = sorted_gaps[j][0]
                boost = min(2, max(1, int(sorted_gaps[j][1] / 5)))
                neighbor[h] = min(10, neighbor[h] + boost)
            if j < len(sorted_excess):
                h = sorted_excess[j][0]
                reduce = min(2, max(1, int(sorted_excess[j][1] / 5)))
                neighbor[h] = max(-10, neighbor[h] - reduce)

        n_dict = {h: int(neighbor[h]) for h in range(24)}
        nf, nr, ns = evaluate_fitness_tunable(n_dict, demand_df, das_df, store, params, objective, return_data=True)
        if nf <= current_fitness:
            current, current_fitness, current_roster = neighbor, nf, nr
            if nf < best_fitness:
                best, best_fitness, best_roster_df, best_shifts_df = current.copy(), nf, nr, ns

        history.append({'iteration': i + 1, 'current_fitness': current_fitness, 'best_fitness': best_fitness})
        if progress_callback and progress_callback(i + 1, iterations, best_fitness):
            return best, best_fitness, history, best_roster_df, best_shifts_df

    phase2_start = len(history)
    for i in range(iterations - phase2_start):
        iter_num = phase2_start + i + 1
        gap_by_hour, excess_by_hour = _analyze_roster(current_roster)
        neighbor = current.copy()

        if gap_by_hour and np.random.random() < 0.7:
            if np.random.random() < 0.5 and gap_by_hour:
                gh = list(gap_by_hour.keys())
                gw = [gap_by_hour[h] for h in gh]
                tw = sum(gw)
                slot = np.random.choice(gh, p=[w / tw for w in gw] if tw > 0 else None)
                neighbor[slot] = min(10, neighbor[slot] + np.random.choice([1, 2]))
            elif excess_by_hour:
                eh = list(excess_by_hour.keys())
                ew = [excess_by_hour[h] for h in eh]
                tw = sum(ew)
                slot = np.random.choice(eh, p=[w / tw for w in ew] if tw > 0 else None)
                neighbor[slot] = max(-10, neighbor[slot] + np.random.choice([-1, -2]))
        else:
            slot = np.random.randint(0, 24)
            neighbor[slot] = np.clip(neighbor[slot] + np.random.choice([-2, -1, 1, 2]), -10, 10)

        n_dict = {h: int(neighbor[h]) for h in range(24)}
        nf, nr, ns = evaluate_fitness_tunable(n_dict, demand_df, das_df, store, params, objective, return_data=True)
        temperature = 1.0 - (i / max(1, iterations - phase2_start))
        if nf < current_fitness or np.random.random() < temperature * 0.05:
            current, current_fitness, current_roster = neighbor, nf, nr
            if nf < best_fitness:
                best, best_fitness, best_roster_df, best_shifts_df = current.copy(), nf, nr, ns

        history.append({'iteration': iter_num, 'current_fitness': current_fitness, 'best_fitness': best_fitness})
        if progress_callback and progress_callback(iter_num, iterations, best_fitness):
            return best, best_fitness, history, best_roster_df, best_shifts_df

    return best, best_fitness, history, best_roster_df, best_shifts_df


# =============================================================================
# NETWORK OPTIMIZER FUNCTIONS (NEW in v2)
# =============================================================================

def run_all_stores_optimizer(demand_df, das_df, params, day_multipliers, scale_mode, intensity, 
                             run_optimizer=False, generations=10, progress_callback=None):
    """Run optimization for all stores. Returns results DataFrame and optimized priorities."""
    stores = sorted(demand_df['Store'].unique())
    results = []
    optimized_priorities = {}
    
    for idx, store in enumerate(stores):
        if progress_callback:
            progress_callback(idx + 1, len(stores), store)
        
        try:
            # Get or optimize priorities for this store
            if run_optimizer:
                best_priorities, _, _, opt_roster_df, opt_shifts_df = run_genetic_optimizer(
                    demand_df, das_df, store, params, day_multipliers, scale_mode, intensity,
                    population_size=10, generations=generations, objective='gap', progress_callback=None,
                    current_priorities=st.session_state.get('store_priorities', {}).get(store, {h: 5 for h in range(24)})
                )
                store_priorities = {h: int(best_priorities[h]) for h in range(24)} if best_priorities is not None else {h: 5 for h in range(24)}
                # Use the optimized roster directly if available
                if opt_roster_df is not None:
                    roster_df = opt_roster_df
                else:
                    roster_df, _ = generate_roster_with_priorities(
                        demand_df, das_df, store, store_priorities, params, day_multipliers, scale_mode, intensity
                    )
            else:
                store_priorities = st.session_state.get('store_priorities', {}).get(store, {h: 5 for h in range(24)})
                # Generate roster
                roster_df, _ = generate_roster_with_priorities(
                    demand_df, das_df, store, store_priorities, params, day_multipliers, scale_mode, intensity
                )
            
            optimized_priorities[store] = store_priorities
            
            if roster_df is None or roster_df.empty:
                results.append({
                    'Store': store, 'Current_DAs': 0, 'Gap': 0, 'Excess': 0,
                    'DAs_Needed': 0, 'Excess_DAs': 0, 'Utilization_%': 0,
                    'Coverage_%': 0, 'Status': '⚠️ No DAs'
                })
                continue
            
            # Calculate metrics
            store_das = das_df[das_df['Store'] == store]
            current_das = store_das['DA_Count'].sum() if not store_das.empty else 0
            total_gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum())
            total_excess = roster_df[roster_df['Diff'] > 0]['Diff'].sum()
            
            # DAs needed - direct gap hours calculation
            effective_hours = params.get('shift_hours', 10) - params.get('break_hours', 1)
            working_days = params.get('working_days', 6)
            da_weekly_hours = effective_hours * working_days
            # Use gap hours directly with 85% efficiency factor
            das_needed = max(0, int(np.ceil(total_gap / (da_weekly_hours * 0.85)))) if total_gap > 0 else 0
            
            # Excess DAs
            excess_das = calculate_excess_das(roster_df, params)
            
            # Utilization
            total_da_hours = current_das * da_weekly_hours
            utilized_hours = sum(min(row['Rostered'], row['Required']) for _, row in roster_df.iterrows())
            utilization = (utilized_hours / total_da_hours * 100) if total_da_hours > 0 else 0
            
            # Coverage
            total_required = roster_df['Required'].sum()
            total_rostered = roster_df['Rostered'].sum()
            coverage = (total_rostered / total_required * 100) if total_required > 0 else 0
            
            # Status
            if total_gap == 0:
                status = '✅ Perfect'
            elif das_needed == 0:
                status = '✅ Covered'
            elif das_needed <= 2:
                status = '🟡 Minor Gap'
            else:
                status = '🔴 Needs DAs'
            
            results.append({
                'Store': store, 'Current_DAs': int(current_das), 'Gap': int(total_gap),
                'Excess': int(total_excess), 'DAs_Needed': das_needed, 'Excess_DAs': excess_das,
                'Utilization_%': round(utilization, 1), 'Coverage_%': round(coverage, 1), 'Status': status
            })
            
        except Exception as e:
            results.append({
                'Store': store, 'Current_DAs': 0, 'Gap': 0, 'Excess': 0,
                'DAs_Needed': 0, 'Excess_DAs': 0, 'Utilization_%': 0,
                'Coverage_%': 0, 'Status': '❌ Error'
            })
            optimized_priorities[store] = {h: 5 for h in range(24)}
    
    return pd.DataFrame(results), optimized_priorities

def generate_transfer_recommendations(results_df):
    """Generate smart DA transfer recommendations."""
    recommendations = []
    excess_stores = results_df[results_df['Excess_DAs'] > 0].copy().sort_values('Excess_DAs', ascending=False)
    needy_stores = results_df[results_df['DAs_Needed'] > 0].sort_values('DAs_Needed', ascending=False)
    
    for _, needy in needy_stores.iterrows():
        needed = needy['DAs_Needed']
        for idx, excess in excess_stores.iterrows():
            if needed <= 0 or excess['Excess_DAs'] <= 0:
                continue
            transfer_count = min(needed, excess['Excess_DAs'])
            recommendations.append({
                'From_Store': excess['Store'], 'To_Store': needy['Store'],
                'DAs_to_Transfer': transfer_count, 'From_Excess': int(excess['Excess_DAs']),
                'To_Gap': int(needy['Gap']), 'Priority': 'High' if needy['Gap'] > 100 else 'Medium'
            })
            needed -= transfer_count
            excess_stores.loc[idx, 'Excess_DAs'] -= transfer_count
    
    return pd.DataFrame(recommendations) if recommendations else None

def create_network_summary_chart(results_df):
    """Create summary chart for all stores."""
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=('Gap by Store', 'Utilization by Store', 
                       'DAs Needed vs Excess', 'Coverage Rate'),
        specs=[[{"type": "bar"}, {"type": "bar"}],
               [{"type": "bar"}, {"type": "bar"}]]
    )
    
    fig.add_trace(go.Bar(x=results_df['Store'], y=results_df['Gap'], name='Gap', marker_color='red'), row=1, col=1)
    fig.add_trace(go.Bar(x=results_df['Store'], y=results_df['Utilization_%'], name='Utilization %', marker_color='blue'), row=1, col=2)
    fig.add_trace(go.Bar(x=results_df['Store'], y=results_df['DAs_Needed'], name='DAs Needed', marker_color='orange'), row=2, col=1)
    fig.add_trace(go.Bar(x=results_df['Store'], y=-results_df['Excess_DAs'], name='Excess DAs', marker_color='green'), row=2, col=1)
    fig.add_trace(go.Bar(x=results_df['Store'], y=results_df['Coverage_%'], name='Coverage %', marker_color='purple'), row=2, col=2)
    
    fig.update_layout(height=600, showlegend=False,
                      paper_bgcolor='rgba(0,0,0,0)',
                      plot_bgcolor='rgba(0,0,0,0)',
                      font=dict(color='#64748B'))
    return fig

# =============================================================================
# WEEK CONTINUITY — SIDEBAR UI HELPER
# =============================================================================

def _render_week_continuity_sidebar(selected_week):
    """Render the "🔗 Week Continuity" accordion in the sidebar.

    This section lets the planner upload the *previous* week's roster
    (the exact Excel file this app produces) and apply one of three
    continuity strategies:

      * Full Continuity — connect same DA IDs across weeks
      * Coverage Only   — anonymous Sunday coverage boost
      * Flexible Handoff — matching algorithm for changed DSP rosters

    The parsed data and constraints are persisted to session state under:

      * ``prev_week_data``      — parser output dict
      * ``continuity_strategy`` — strategy string
      * ``week_continuity``     — constraints dict (includes report data)
      * ``carryover_excel_data``— bridged for existing engine params flow
    """
    import re
    from week_continuity import (
        parse_previous_week_roster,
        build_continuity_constraints,
        build_continuity_report_df,
        build_store_detail_df,
        STRATEGY_FULL,
        STRATEGY_COVERAGE,
        STRATEGY_FLEX,
    )

    # Suggest the previous week number (e.g. WK17 -> WK16)
    prev_week_suggest = None
    if selected_week:
        m = re.search(r"WK(\d+)", str(selected_week), re.IGNORECASE)
        if m:
            prev_week_suggest = f"WK{max(1, int(m.group(1)) - 1)}"

    with st.expander("🔗 Week Continuity", expanded=False):
        st.markdown(
            "**Carry forward Saturday overnight DAs** from last week's roster "
            "so Sunday rest rules are respected automatically."
        )

        if prev_week_suggest:
            st.caption(f"💡 Suggested file: last week's export for **{prev_week_suggest}**")

        prev_file = st.file_uploader(
            "Upload previous week's roster (.xlsx)",
            type=["xlsx"],
            key="continuity_prev_roster",
            help="The Excel file this app exported for the previous week.",
        )

        # Strategy selector (radio rendered as toggle cards by Streamlit theming)
        strategy_label = {
            STRATEGY_FULL: "⭐ Strategy A — Full Continuity (recommended)",
            STRATEGY_COVERAGE: "Strategy B — Coverage Only",
            STRATEGY_FLEX: "Strategy C — Flexible Handoff",
        }
        strategy = st.radio(
            "Continuity strategy",
            options=[STRATEGY_FULL, STRATEGY_COVERAGE, STRATEGY_FLEX],
            format_func=lambda s: strategy_label[s],
            index=[STRATEGY_FULL, STRATEGY_COVERAGE, STRATEGY_FLEX].index(
                st.session_state.get("continuity_strategy", STRATEGY_FULL)
            ),
            key="continuity_strategy_radio",
            help=(
                "A: reuses same DA IDs across weeks (audit-friendly). "
                "B: anonymous Sunday coverage boost (fastest). "
                "C: match last week's overnight DAs to this week's latest Sunday starts."
            ),
        )
        st.session_state["continuity_strategy"] = strategy

        st.info(
            "💡 **Best practice** — Strategy A is recommended for operations "
            "with ≥10% overnight shifts. Strategy B is fastest when DA-level "
            "tracking is not required. Strategy C suits weeks where DSP "
            "rosters change significantly."
        )

        # Parse & persist on upload
        if prev_file is not None:
            # Cache key based on filename/size to avoid reparsing every rerun
            key_sig = f"{getattr(prev_file, 'name', '')}:{getattr(prev_file, 'size', '')}"
            if st.session_state.get("prev_week_file_sig") != key_sig:
                try:
                    parsed = parse_previous_week_roster(
                        prev_file,
                        min_rest=int(st.session_state.get("min_rest", 12)),
                        shift_hours=int(st.session_state.get("shift_hours", 10)),
                    )
                    st.session_state["prev_week_data"] = parsed
                    st.session_state["prev_week_file_sig"] = key_sig
                except Exception as e:
                    st.error(f"❌ Invalid roster format: {e}")
                    st.session_state.pop("prev_week_data", None)
                    st.session_state.pop("prev_week_file_sig", None)

        prev_data = st.session_state.get("prev_week_data")
        if prev_data and prev_data.get("total_overnight_das", 0) > 0:
            wk_det = prev_data.get("week_detected") or "previous week"
            st.success(
                f"✅ Detected **{prev_data['total_overnight_das']}** Saturday "
                f"overnight DAs across **{prev_data['total_stores']}** stores "
                f"({wk_det})."
            )

            # Build constraints now so they flow into the engine via params
            constraints = build_continuity_constraints(
                prev_data,
                {
                    "min_rest": int(st.session_state.get("min_rest", 12)),
                    "shift_hours": int(st.session_state.get("shift_hours", 10)),
                },
                strategy,
            )
            st.session_state["week_continuity"] = constraints
            # Bridge into existing engine param key — engines already read this.
            st.session_state["carryover_excel_data"] = pd.DataFrame(
                constraints["carryover_excel_data"]
            ) if constraints["carryover_excel_data"] else pd.DataFrame()

            # Preview (per-store summary)
            report_df = build_continuity_report_df(prev_data, constraints)
            if not report_df.empty:
                st.dataframe(
                    report_df, use_container_width=True, hide_index=True
                )
                # Per-store expandable details
                stores_list = list(prev_data.get("stores", {}).keys())
                if stores_list:
                    pick = st.selectbox(
                        "🔎 Inspect store",
                        options=["—"] + stores_list,
                        key="continuity_detail_pick",
                    )
                    if pick and pick != "—":
                        detail_df = build_store_detail_df(prev_data, pick)
                        if not detail_df.empty:
                            st.dataframe(
                                detail_df, use_container_width=True, hide_index=True
                            )
        elif prev_data is not None:
            st.info("No Saturday overnight shifts were found in the uploaded file.")
        else:
            st.caption("📤 Drop the previous week's Excel export above to enable continuity.")


def main():
    # Apply global design system (CSS) once per rerun.
    apply_global_styles()

    # Determine header pill values (best-effort — don't block rendering).
    _current_week_hdr = st.session_state.get('current_week')
    _total_das_hdr = st.session_state.get('header_total_das')
    _data_loaded_hdr = bool(st.session_state.get('header_data_loaded', False))
    _crumb_store = st.session_state.get('selected_store')
    _breadcrumb = ['Home', 'Store Roster']
    if _crumb_store:
        _breadcrumb.append(str(_crumb_store))

    render_app_header(
        title="AART — AI Assisted Rostering Tool",
        subtitle="Multi-week roster planning with priority tuning and network-wide optimization",
        week=_current_week_hdr,
        total_das=_total_das_hdr,
        data_loaded=_data_loaded_hdr,
        breadcrumb=_breadcrumb,
    )

    # Sidebar for file upload and settings
    with st.sidebar:
        st.header("📁 Data Input")
        uploaded_file = st.file_uploader(
            "Upload Capacity Planning File",
            type=['xlsx'],
            help="Excel file with 'Slot Level DA Requirement' (with Week column) and 'Available DAs' sheets",
            key="file_uploader"
        )
        
        # Week Selection (Dynamic based on uploaded file)
        st.header("📆 Week Selection")
        
        # Detect available weeks from uploaded file
        if uploaded_file is not None:
            available_weeks, default_week = detect_available_weeks(uploaded_file)
            
            if not available_weeks:
                # No weeks found — try to extract week from filename
                import re
                fname = uploaded_file.name if hasattr(uploaded_file, 'name') else ''
                week_match = re.search(r'WK(\d+)|Week\s*(\d+)|_W(\d+)', fname, re.IGNORECASE)
                if week_match:
                    wk_num = next(g for g in week_match.groups() if g is not None)
                    selected_week = f'WK{wk_num}'
                    st.info(f"📋 Single week file — detected **{selected_week}** from filename.")
                else:
                    selected_week = 'WK1'
                    st.info("📋 Single week file detected (no Week column). Using WK1.")
            else:
                # Get default index
                default_idx = 0
                if 'current_week' in st.session_state and st.session_state.current_week in available_weeks:
                    default_idx = available_weeks.index(st.session_state.current_week)
                elif default_week and default_week in available_weeks:
                    default_idx = available_weeks.index(default_week)
                
                selected_week = st.selectbox(
                    "Select Week",
                    options=available_weeks,
                    index=default_idx,
                    help=f"{len(available_weeks)} week(s) detected: {', '.join(available_weeks)}",
                    key="selected_week"
                )
            
            # Show DA Pool Summary for selected week
            try:
                import re
                uploaded_file.seek(0)
                das_preview = pd.read_excel(uploaded_file, sheet_name='Available DAs')
                das_preview = das_preview.rename(columns={'Station': 'Store', 'DSP Name': 'DSP', 'DSP Code': 'DSP_Code'})
                
                if available_weeks:
                    # Find the week column - extract week number
                    week_match = re.search(r'\d+', selected_week)
                    week_num = week_match.group() if week_match else selected_week.replace('WK', '').replace('Week', '').strip()
                    
                    # Try different column formats
                    week_col = None
                    possible_cols = [f'Week {week_num}', f'Week{week_num}', f'WK{week_num}', selected_week]
                    for col in possible_cols:
                        if col in das_preview.columns:
                            week_col = col
                            break
                    
                    # Fallback: search for any column containing the week number
                    if week_col is None:
                        for col in das_preview.columns:
                            col_str = str(col)
                            if ('Week' in col_str or 'WK' in col_str) and week_num in col_str:
                                week_col = col
                                break
                    
                    if week_col:
                        das_preview['DA_Count'] = pd.to_numeric(das_preview[week_col], errors='coerce').fillna(0)
                    else:
                        # Try 'Actual' column as fallback
                        if 'Actual' in das_preview.columns:
                            das_preview['DA_Count'] = pd.to_numeric(das_preview['Actual'], errors='coerce').fillna(0)
                        else:
                            das_preview['DA_Count'] = 0
                            st.warning(f"⚠️ No DA column found for {selected_week}. Columns: {list(das_preview.columns)}")
                else:
                    # Single week mode — use 'Actual' column
                    if 'Actual' in das_preview.columns:
                        das_preview['DA_Count'] = pd.to_numeric(das_preview['Actual'], errors='coerce').fillna(0)
                    else:
                        das_preview['DA_Count'] = 0
                
                total_das = int(das_preview['DA_Count'].sum())
                # Expose live values to the header hero banner
                st.session_state['header_total_das'] = total_das
                st.session_state['header_data_loaded'] = True
                stores_with_das = das_preview[das_preview['DA_Count'] > 0]['Store'].nunique()
                dsps_with_das = len(das_preview[das_preview['DA_Count'] > 0])
                st.success(f"📊 {selected_week}: {total_das} DAs across {stores_with_das} stores ({dsps_with_das} DSPs)")
                
                uploaded_file.seek(0)
            except Exception as e:
                st.error(f"Error reading DA pool: {e}")
        else:
            st.info("📤 Upload a file to see available weeks")
            selected_week = None
        
        # Store selected week in session state
        if selected_week is not None:
            if 'current_week' not in st.session_state:
                st.session_state.current_week = selected_week
            
            # If week changed, clear optimized shifts
            if st.session_state.current_week != selected_week:
                for key in list(st.session_state.keys()):
                    if key.startswith('optimized_shifts_') or key.startswith('shift_shares_'):
                        del st.session_state[key]
                st.session_state.current_week = selected_week
                st.info(f"📆 Switched to {selected_week} - regenerating roster...")
        
        # Force reload button
        if uploaded_file is not None:
            if st.button("🔄 Reload Data", help="Force reload data from the uploaded file"):
                # Clear all cached data
                keys_to_clear = ['global_roster', 'global_shifts', 'network_results', 'store_priorities']
                for key in keys_to_clear:
                    if key in st.session_state:
                        del st.session_state[key]
                # Clear optimized shifts
                for key in list(st.session_state.keys()):
                    if key.startswith('optimized_shifts_') or key.startswith('shift_shares_'):
                        del st.session_state[key]
                st.success("✅ Data cache cleared! Reloading...")
                st.rerun()
        
        # =====================================================================
        # WEEK CONTINUITY (Previous Week Roster Upload)
        # =====================================================================
        _render_week_continuity_sidebar(selected_week)

        st.header("⚙️ Engine Settings")
        
        # Engine Type Toggle (NEW)
        engine_type = st.radio(
            "🔧 Engine Type",
            options=['demand_driven'],
            format_func=lambda x: {"demand_driven": "📊 Demand-Driven (v12.4)"}.get(x, x),
            help="Select the rostering engine to use.",
            horizontal=True
        )
        
        # Store engine type in session state and clear optimized shifts if changed
        if 'engine_type' not in st.session_state:
            st.session_state.engine_type = 'demand_driven'
        
        # Fallback guard: reset stale engine types to demand_driven
        if st.session_state.get('engine_type') in ('overnight', 'flexible', 'fixed', 'proportional', 'demand_driven_ultimate', 'tunable', 'split', 'dual_pool', 'constrained'):
            st.session_state['engine_type'] = 'demand_driven'
        
        # If engine type changed, clear all optimized shifts to force regeneration
        if st.session_state.engine_type != engine_type:
            for key in list(st.session_state.keys()):
                if key.startswith('optimized_shifts_'):
                    del st.session_state[key]
        
        st.session_state.engine_type = engine_type
        
        # Show engine-specific settings
        if engine_type == 'flexible':
            night_shift = st.checkbox("🌙 Enable Night Shift", value=True, 
                help="Allow overnight shifts (19:00-05:00)")
            flexible_day_off = st.checkbox("📅 Flexible Day Off (allow Fri/Sat off)", value=True,
                help="If unchecked, Fri/Sat are mandatory working days")
            
            # Sunday overnight carryover settings (only relevant when night shift is enabled)
            if night_shift:
                st.markdown("**Sunday Carryover Settings**")
                carryover_mode = st.radio(
                    "Sunday Early Morning (00:00-05:00)",
                    options=['auto', 'upload', 'skip'],
                    format_func=lambda x: {
                        'auto': '🔄 Auto',
                        'upload': '📥 Upload Previous Week Roster',
                        'skip': '⏭️ Skip'
                    }[x],
                    horizontal=True,
                    help="Auto: engine estimates carryover. Upload: load last week's roster Excel to extract Saturday night DAs. Skip: no carryover."
                )
                
                if carryover_mode == 'upload':
                    st.markdown("Upload the **previous week's roster Excel** (the same file this app generates). "
                                "Saturday overnight shifts will be extracted automatically.")
                    prev_roster_file = st.file_uploader(
                        "Previous week roster (.xlsx)",
                        type=['xlsx'],
                        key="prev_week_roster_upload",
                    )
                    if prev_roster_file:
                        try:
                            prev_xls = pd.ExcelFile(prev_roster_file)
                            # Try Shift_Details sheet first, then look for {Store}_Shifts sheets
                            sat_overnight = []
                            if 'Shift_Details' in prev_xls.sheet_names:
                                sdf = pd.read_excel(prev_xls, sheet_name='Shift_Details')
                                sat_rows = sdf[(sdf['Day'] == 'Sat') & (~sdf['Is_Day_Off'])]
                                for _, r in sat_rows.iterrows():
                                    s_start = r.get('Shift_Start')
                                    s_end = r.get('Shift_End')
                                    if pd.notna(s_start) and pd.notna(s_end):
                                        sat_overnight.append({
                                            'DA_ID': r['DA_ID'], 'Store': r['Store'],
                                            'DSP_Code': r.get('DSP', ''),
                                            'Sat_Shift_Start': int(s_start), 'Sat_Shift_End': int(s_end),
                                        })
                            else:
                                # Try per-store sheets
                                for sheet in prev_xls.sheet_names:
                                    if sheet.endswith('_Shifts'):
                                        sdf = pd.read_excel(prev_xls, sheet_name=sheet)
                                        sat_rows = sdf[(sdf['Day'] == 'Sat') & (~sdf['Is_Day_Off'])]
                                        for _, r in sat_rows.iterrows():
                                            s_start = r.get('Shift_Start')
                                            s_end = r.get('Shift_End')
                                            if pd.notna(s_start) and pd.notna(s_end):
                                                sat_overnight.append({
                                                    'DA_ID': r['DA_ID'], 'Store': r['Store'],
                                                    'DSP_Code': r.get('DSP', ''),
                                                    'Sat_Shift_Start': int(s_start), 'Sat_Shift_End': int(s_end),
                                                })
                            if sat_overnight:
                                st.session_state['carryover_excel_data'] = pd.DataFrame(sat_overnight)
                                st.success(f"✅ Found {len(sat_overnight)} Saturday overnight DAs across "
                                           f"{len(set(d['Store'] for d in sat_overnight))} stores")
                                with st.expander("Preview carryover DAs"):
                                    st.dataframe(pd.DataFrame(sat_overnight), use_container_width=True, hide_index=True)
                            else:
                                st.info("No Saturday overnight shifts found in the uploaded roster.")
                            carryover_mode = 'excel'  # Use excel mode internally
                        except Exception as e:
                            st.error(f"Error reading roster file: {e}")
                            carryover_mode = 'auto'
                    elif 'carryover_excel_data' in st.session_state:
                        cdata = st.session_state['carryover_excel_data']
                        n_carry = len(cdata) if isinstance(cdata, pd.DataFrame) else len(cdata)
                        st.info(f"📋 Using previously loaded carryover ({n_carry} DAs)")
                        carryover_mode = 'excel'
                    else:
                        carryover_mode = 'auto'
                    sunday_carryover_das = 0
                else:
                    sunday_carryover_das = 0
                
                skip_sunday_overnight = (carryover_mode == 'skip')
            else:
                skip_sunday_overnight = False
                carryover_mode = 'auto'
                sunday_carryover_das = 0
        elif engine_type == 'fixed':
            # Fixed Shifts Engine
            
            # Initialize custom shifts in session state if not exists
            if 'custom_fixed_shifts' not in st.session_state:
                st.session_state.custom_fixed_shifts = {
                    1: {'start': 5, 'name': 'Dawn (05:00)'},
                    2: {'start': 7, 'name': 'Early Morning (07:00)'},
                    3: {'start': 11, 'name': 'Late Morning (11:00)'},
                    4: {'start': 12, 'name': 'Noon (12:00)'},
                    5: {'start': 15, 'name': 'Afternoon (15:00)'},
                    6: {'start': 19, 'name': 'Evening (19:00)'},
                }
            
            # Display current shifts
            current_shifts = st.session_state.custom_fixed_shifts
            shift_times = [f"{current_shifts[k]['start']:02d}:00" for k in sorted(current_shifts.keys())]
            st.info(f"📌 Fixed Shifts ({len(current_shifts)}): {', '.join(shift_times)}")
            
            # Shift management expander
            with st.expander("⚙️ Manage Shifts", expanded=False):
                st.markdown("**Current Shifts:**")
                
                # Show current shifts with delete buttons
                shifts_to_delete = []
                for shift_id in sorted(current_shifts.keys()):
                    shift = current_shifts[shift_id]
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.write(f"Shift {shift_id}: {shift['name']}")
                    with col2:
                        if len(current_shifts) > 1:  # Don't allow deleting last shift
                            if st.button("🗑️", key=f"del_shift_{shift_id}", help=f"Delete Shift {shift_id}"):
                                shifts_to_delete.append(shift_id)
                
                # Process deletions
                for shift_id in shifts_to_delete:
                    del st.session_state.custom_fixed_shifts[shift_id]
                    # Renumber remaining shifts - make a deep copy first
                    old_shifts = {k: dict(v) for k, v in st.session_state.custom_fixed_shifts.items()}
                    st.session_state.custom_fixed_shifts = {}
                    for i, (_, shift) in enumerate(sorted(old_shifts.items(), key=lambda x: x[1]['start']), 1):
                        st.session_state.custom_fixed_shifts[i] = shift
                    # Clear optimized shifts to force recalculation
                    for key in list(st.session_state.keys()):
                        if key.startswith('optimized_shifts_') or key.startswith('shift_shares_'):
                            del st.session_state[key]
                    st.success(f"✅ Deleted shift. Shares will be recalculated.")
                    st.rerun()
                
                st.markdown("---")
                st.markdown("**Add New Shift:**")
                
                # Add new shift
                available_hours = [h for h in range(24) if h not in [s['start'] for s in current_shifts.values()]]
                
                if available_hours:
                    new_shift_col1, new_shift_col2 = st.columns([2, 1])
                    with new_shift_col1:
                        new_shift_hour = st.selectbox(
                            "Start Hour",
                            options=available_hours,
                            format_func=lambda h: f"{h:02d}:00",
                            key="new_shift_hour"
                        )
                    with new_shift_col2:
                        if st.button("➕ Add Shift", key="add_shift_btn"):
                            # Add new shift
                            new_id = max(current_shifts.keys()) + 1
                            hour_names = {
                                0: 'Midnight', 1: 'Night', 2: 'Night', 3: 'Night', 4: 'Dawn', 5: 'Dawn',
                                6: 'Early Morning', 7: 'Early Morning', 8: 'Morning', 9: 'Morning',
                                10: 'Late Morning', 11: 'Late Morning', 12: 'Noon', 13: 'Afternoon',
                                14: 'Afternoon', 15: 'Afternoon', 16: 'Late Afternoon', 17: 'Evening',
                                18: 'Evening', 19: 'Evening', 20: 'Night', 21: 'Night', 22: 'Night', 23: 'Night'
                            }
                            st.session_state.custom_fixed_shifts[new_id] = {
                                'start': new_shift_hour,
                                'name': f"{hour_names.get(new_shift_hour, 'Custom')} ({new_shift_hour:02d}:00)"
                            }
                            # Renumber shifts by start time - make a deep copy first
                            old_shifts = {k: dict(v) for k, v in st.session_state.custom_fixed_shifts.items()}
                            st.session_state.custom_fixed_shifts = {}
                            for i, (_, shift) in enumerate(sorted(old_shifts.items(), key=lambda x: x[1]['start']), 1):
                                st.session_state.custom_fixed_shifts[i] = shift
                            # Clear optimized shifts to force recalculation
                            for key in list(st.session_state.keys()):
                                if key.startswith('optimized_shifts_') or key.startswith('shift_shares_'):
                                    del st.session_state[key]
                            st.success(f"✅ Added shift at {new_shift_hour:02d}:00. Shares will be recalculated.")
                            st.rerun()
                else:
                    st.info("All 24 hours are already assigned to shifts.")
                
                # Reset to default button
                if st.button("🔄 Reset to Default (6 shifts)", key="reset_shifts_btn"):
                    st.session_state.custom_fixed_shifts = {
                        1: {'start': 5, 'name': 'Dawn (05:00)'},
                        2: {'start': 7, 'name': 'Early Morning (07:00)'},
                        3: {'start': 11, 'name': 'Late Morning (11:00)'},
                        4: {'start': 12, 'name': 'Noon (12:00)'},
                        5: {'start': 15, 'name': 'Afternoon (15:00)'},
                        6: {'start': 19, 'name': 'Evening (19:00)'},
                    }
                    # Clear optimized shifts
                    for key in list(st.session_state.keys()):
                        if key.startswith('optimized_shifts_') or key.startswith('shift_shares_'):
                            del st.session_state[key]
                    st.success("✅ Reset to default 6 shifts.")
                    st.rerun()
            
            night_shift = True  # Shift 6 (19:00) is overnight by default
            flexible_day_off = st.checkbox("📅 Flexible Day Off (allow Fri/Sat off)", value=True,
                key="fixed_flexible_day_off",
                help="If unchecked, Fri/Sat are mandatory working days")
            
            # Sunday carryover for Shift 6 (19:00 overnight)
            st.markdown("**Sunday Carryover Settings**")
            carryover_mode = st.radio(
                "Sunday Early Morning (00:00-05:00)",
                options=['auto', 'manual', 'excel', 'skip'],
                format_func=lambda x: {
                    'auto': '🔄 Auto',
                    'manual': '✏️ Manual',
                    'excel': '📊 Excel Upload',
                    'skip': '⏭️ Skip'
                }[x],
                horizontal=True,
                key="fixed_carryover_mode",
                help="Auto: Uses Saturday Shift 6 DAs. Manual: Specify count. Excel: Upload per-store carryover. Skip: No carryover."
            )
            
            if carryover_mode == 'manual':
                sunday_carryover_das = st.number_input(
                    "Carryover DAs for Sunday 00:00-05:00",
                    min_value=0,
                    max_value=50,
                    value=st.session_state.get('sunday_carryover_das_fixed', 0),
                    key="fixed_carryover_das",
                    help="Number of DAs carrying over from previous week's Saturday night shift"
                )
                st.session_state['sunday_carryover_das_fixed'] = sunday_carryover_das
            elif carryover_mode == 'excel':
                st.markdown("**📥 Upload Previous Week's Saturday Night DAs**")
                
                # Template download - individual DAs with shift end time
                template_df = pd.DataFrame({
                    'Store': ['QRA1', 'QRA1', 'QRA4'],
                    'DSP_Code': ['DSP001', 'DSP001', 'DSP003'],
                    'DA_Number': [1, 2, 1],
                    'Sat_Shift_End': [5, 5, 5]
                })
                template_buffer = io.BytesIO()
                template_df.to_excel(template_buffer, index=False)
                st.download_button(
                    "📄 Download Template",
                    data=template_buffer.getvalue(),
                    file_name="carryover_template.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="carryover_template_fixed"
                )
                st.caption("DSP_Code must match your Available DAs sheet")
                
                carryover_file = st.file_uploader(
                    "Excel with Saturday night DAs",
                    type=['xlsx', 'xls'],
                    key="carryover_upload_fixed",
                    help="Columns: Store, DSP_Code, DA_Number, Sat_Shift_End (hour 0-23)"
                )
                if carryover_file:
                    try:
                        carryover_df = pd.read_excel(carryover_file)
                        # Normalize column names
                        carryover_df.columns = [c.strip().replace(' ', '_') for c in carryover_df.columns]
                        st.session_state['carryover_excel_data_fixed'] = carryover_df
                        st.success(f"✅ Loaded {len(carryover_df)} carryover DAs")
                        with st.expander("Preview Carryover Data"):
                            st.dataframe(carryover_df, use_container_width=True)
                    except Exception as e:
                        st.error(f"Error reading file: {e}")
                elif 'carryover_excel_data_fixed' in st.session_state:
                    st.info(f"📋 Using previously loaded carryover ({len(st.session_state['carryover_excel_data_fixed'])} records)")
                sunday_carryover_das = 0
            else:
                sunday_carryover_das = 0
            
            skip_sunday_overnight = (carryover_mode == 'skip')
        

        elif engine_type == 'proportional':
            # Proportional Engine (v12.3) — same settings as flexible
            night_shift = st.checkbox("🌙 Enable Night Shift", value=True,
                key="prop_night_shift",
                help="Allow overnight shifts (19:00-05:00)")
            flexible_day_off = st.checkbox("📅 Flexible Day Off (allow Fri/Sat off)", value=True,
                key="prop_flexible_day_off",
                help="If unchecked, Fri/Sat are mandatory working days")

            if night_shift:
                st.markdown("**Sunday Carryover Settings**")
                carryover_mode = st.radio(
                    "Sunday Early Morning (00:00-05:00)",
                    options=['auto', 'manual', 'excel', 'skip'],
                    format_func=lambda x: {
                        'auto': '🔄 Auto',
                        'manual': '✏️ Manual',
                        'excel': '📊 Excel Upload',
                        'skip': '⏭️ Skip'
                    }[x],
                    horizontal=True,
                    key="prop_carryover_mode",
                    help="Auto: Uses Saturday night shift DAs. Manual: Specify count. Excel: Upload per-store carryover. Skip: No carryover."
                )

                if carryover_mode == 'manual':
                    sunday_carryover_das = st.number_input(
                        "Carryover DAs for Sunday 00:00-05:00",
                        min_value=0,
                        max_value=50,
                        value=st.session_state.get('sunday_carryover_das_prop', 0),
                        key="prop_carryover_das",
                        help="Number of DAs carrying over from previous week's Saturday night shift"
                    )
                    st.session_state['sunday_carryover_das_prop'] = sunday_carryover_das
                elif carryover_mode == 'excel':
                    st.markdown("**📥 Upload Previous Week's Saturday Night DAs**")
                    template_df = pd.DataFrame({
                        'Store': ['QRA1', 'QRA1', 'QRA4'],
                        'DSP_Code': ['DSP001', 'DSP001', 'DSP003'],
                        'DA_Number': [1, 2, 1],
                        'Sat_Shift_End': [5, 5, 5]
                    })
                    template_buffer = io.BytesIO()
                    template_df.to_excel(template_buffer, index=False)
                    st.download_button(
                        "📄 Download Template",
                        data=template_buffer.getvalue(),
                        file_name="carryover_template.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="carryover_template_prop"
                    )
                    st.caption("DSP_Code must match your Available DAs sheet")
                    carryover_file = st.file_uploader(
                        "Excel with Saturday night DAs",
                        type=['xlsx', 'xls'],
                        key="carryover_upload_prop",
                        help="Columns: Store, DSP_Code, DA_Number, Sat_Shift_End (hour 0-23)"
                    )
                    if carryover_file:
                        try:
                            carryover_df = pd.read_excel(carryover_file)
                            carryover_df.columns = [c.strip().replace(' ', '_') for c in carryover_df.columns]
                            st.session_state['carryover_excel_data'] = carryover_df
                            st.success(f"✅ Loaded {len(carryover_df)} carryover DAs")
                            with st.expander("Preview Carryover Data"):
                                st.dataframe(carryover_df, use_container_width=True)
                        except Exception as e:
                            st.error(f"Error reading file: {e}")
                    elif 'carryover_excel_data' in st.session_state:
                        st.info(f"📋 Using previously loaded carryover ({len(st.session_state['carryover_excel_data'])} DAs)")
                    sunday_carryover_das = 0
                else:
                    sunday_carryover_das = 0

                skip_sunday_overnight = (carryover_mode == 'skip')
            else:
                skip_sunday_overnight = False
                carryover_mode = 'auto'
                sunday_carryover_das = 0

        elif engine_type == 'demand_driven':
            # Demand-Driven Engine (v12.4) — same settings as flexible
            night_shift = st.checkbox("🌙 Enable Night Shift", value=True,
                key="dd_night_shift",
                help="Allow overnight shifts (19:00-05:00)")
            flexible_day_off = st.checkbox("📅 Flexible Day Off (allow Fri/Sat off)", value=True,
                key="dd_flexible_day_off",
                help="If unchecked, Fri/Sat are mandatory working days")
            
            # Fixed start optimizer mode (only shown when shift+rest=24)
            fixed_start_optimizer = st.radio(
                "🔄 Shift Start Optimizer",
                options=['strict', 'post_off', 'flexible'],
                format_func=lambda x: {
                    'strict': '🔒 Strict (same start every day)',
                    'post_off': '📅 Post-Off (different start only after off day)',
                    'flexible': '🔓 Flexible (optimizer can change any day)'
                }[x],
                key="dd_fixed_start_optimizer",
                help="Controls whether the optimizer can change DA start times in fixed start mode (shift+rest=24h)",
                horizontal=True
            )
            
            max_shifts = st.slider(
                "📌 Max Shift Starts (0 = unlimited)",
                min_value=0,
                max_value=24,
                value=st.session_state.get('dd_max_shifts_val', 8),
                key="dd_max_shifts",
                help="Limit the number of unique shift start times. 0 = engine picks as many as needed. Set to 6-10 for operational simplicity."
            )
            if max_shifts > 0:
                st.caption(f"Engine will auto-select the best {max_shifts} shift start times from demand")

            if night_shift:
                st.markdown("**Sunday Carryover Settings**")
                carryover_mode = st.radio(
                    "Sunday Early Morning (00:00-05:00)",
                    options=['auto', 'upload', 'skip'],
                    format_func=lambda x: {
                        'auto': '🔄 Auto',
                        'upload': '📥 Upload Previous Week Roster',
                        'skip': '⏭️ Skip'
                    }[x],
                    horizontal=True,
                    key="dd_carryover_mode",
                    help="Auto: engine estimates carryover. Upload: load last week's roster Excel. Skip: no carryover."
                )

                if carryover_mode == 'upload':
                    prev_roster_file = st.file_uploader(
                        "Previous week roster (.xlsx)",
                        type=['xlsx'],
                        key="prev_week_roster_upload_dd",
                    )
                    if prev_roster_file:
                        try:
                            prev_xls = pd.ExcelFile(prev_roster_file)
                            sat_overnight = []
                            for sheet in prev_xls.sheet_names:
                                if sheet.endswith('_Shifts') or sheet == 'Shift_Details':
                                    _sdf = pd.read_excel(prev_xls, sheet_name=sheet)
                                    _sat = _sdf[(_sdf['Day'] == 'Sat') & (~_sdf['Is_Day_Off'])]
                                    for _, _r in _sat.iterrows():
                                        _ss = _r.get('Shift_Start')
                                        _se = _r.get('Shift_End')
                                        if pd.notna(_ss) and pd.notna(_se):
                                            sat_overnight.append({
                                                'DA_ID': _r['DA_ID'], 'Store': _r['Store'],
                                                'Sat_Shift_Start': int(_ss), 'Sat_Shift_End': int(_se),
                                            })
                            if sat_overnight:
                                st.session_state['carryover_excel_data'] = pd.DataFrame(sat_overnight)
                                st.success(f"✅ Found {len(sat_overnight)} Saturday overnight DAs")
                            else:
                                st.info("No Saturday overnight shifts found.")
                            carryover_mode = 'excel'
                        except Exception as e:
                            st.error(f"Error: {e}")
                            carryover_mode = 'auto'
                    elif 'carryover_excel_data' in st.session_state:
                        cdata = st.session_state['carryover_excel_data']
                        n_carry = len(cdata) if isinstance(cdata, pd.DataFrame) else len(cdata)
                        st.info(f"📋 Using previously loaded carryover ({n_carry} DAs)")
                        carryover_mode = 'excel'
                    else:
                        carryover_mode = 'auto'
                    sunday_carryover_das = 0
                else:
                    sunday_carryover_das = 0

                skip_sunday_overnight = (carryover_mode == 'skip')
            else:
                skip_sunday_overnight = False
                carryover_mode = 'auto'
                sunday_carryover_das = 0


        elif engine_type == 'demand_driven_ultimate':
            # Demand-Driven Ultimate Engine (v12.4u) — same settings as demand_driven
            night_shift = st.checkbox("🌙 Enable Night Shift", value=True,
                key="ddu_night_shift",
                help="Allow overnight shifts (19:00-05:00)")
            flexible_day_off = st.checkbox("📅 Flexible Day Off (allow Fri/Sat off)", value=True,
                key="ddu_flexible_day_off",
                help="If unchecked, Fri/Sat are mandatory working days")

            # Fixed start optimizer mode (only shown when shift+rest=24)
            fixed_start_optimizer = st.radio(
                "🔄 Shift Start Optimizer",
                options=['strict', 'post_off', 'flexible'],
                format_func=lambda x: {
                    'strict': '🔒 Strict (same start every day)',
                    'post_off': '📅 Post-Off (different start only after off day)',
                    'flexible': '🔓 Flexible (optimizer can change any day)'
                }[x],
                key="ddu_fixed_start_optimizer",
                help="Controls whether the optimizer can change DA start times in fixed start mode (shift+rest=24h)",
                horizontal=True
            )

            max_shifts = st.slider(
                "📌 Max Shift Starts (0 = unlimited)",
                min_value=0,
                max_value=24,
                value=st.session_state.get('ddu_max_shifts_val', 0),
                key="ddu_max_shifts",
                help="Limit the number of unique shift start times. 0 = engine picks as many as needed. Set to 6-10 for operational simplicity."
            )
            if max_shifts > 0:
                st.caption(f"Engine will auto-select the best {max_shifts} shift start times from demand")

            if night_shift:
                st.markdown("**Sunday Carryover Settings**")
                carryover_mode = st.radio(
                    "Sunday Early Morning (00:00-05:00)",
                    options=['auto', 'manual', 'excel', 'skip'],
                    format_func=lambda x: {
                        'auto': '🔄 Auto',
                        'manual': '✏️ Manual',
                        'excel': '📊 Excel Upload',
                        'skip': '⏭️ Skip'
                    }[x],
                    horizontal=True,
                    key="ddu_carryover_mode",
                    help="Auto: Uses Saturday night shift DAs. Manual: Specify count. Excel: Upload per-store carryover. Skip: No carryover."
                )

                if carryover_mode == 'manual':
                    sunday_carryover_das = st.number_input(
                        "Carryover DAs for Sunday 00:00-05:00",
                        min_value=0,
                        max_value=50,
                        value=st.session_state.get('sunday_carryover_das_ddu', 0),
                        key="ddu_carryover_das",
                        help="Number of DAs carrying over from previous week's Saturday night shift"
                    )
                    st.session_state['sunday_carryover_das_ddu'] = sunday_carryover_das
                elif carryover_mode == 'excel':
                    st.markdown("**📥 Upload Previous Week's Saturday Night DAs**")
                    template_df = pd.DataFrame({
                        'Store': ['QRA1', 'QRA1', 'QRA4'],
                        'DSP_Code': ['DSP001', 'DSP001', 'DSP003'],
                        'DA_Number': [1, 2, 1],
                        'Sat_Shift_End': [5, 5, 5]
                    })
                    template_buffer = io.BytesIO()
                    template_df.to_excel(template_buffer, index=False)
                    st.download_button(
                        "📄 Download Template",
                        data=template_buffer.getvalue(),
                        file_name="carryover_template.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="carryover_template_ddu"
                    )
                    st.caption("DSP_Code must match your Available DAs sheet")
                    carryover_file = st.file_uploader(
                        "Excel with Saturday night DAs",
                        type=['xlsx', 'xls'],
                        key="carryover_upload_ddu",
                        help="Columns: Store, DSP_Code, DA_Number, Sat_Shift_End (hour 0-23)"
                    )
                    if carryover_file:
                        try:
                            carryover_df = pd.read_excel(carryover_file)
                            carryover_df.columns = [c.strip().replace(' ', '_') for c in carryover_df.columns]
                            st.session_state['carryover_excel_data'] = carryover_df
                            st.success(f"✅ Loaded {len(carryover_df)} carryover DAs")
                            with st.expander("Preview Carryover Data"):
                                st.dataframe(carryover_df, use_container_width=True)
                        except Exception as e:
                            st.error(f"Error reading file: {e}")
                    elif 'carryover_excel_data' in st.session_state:
                        st.info(f"📋 Using previously loaded carryover ({len(st.session_state['carryover_excel_data'])} DAs)")
                    sunday_carryover_das = 0
                else:
                    sunday_carryover_das = 0

                skip_sunday_overnight = (carryover_mode == 'skip')
            else:
                skip_sunday_overnight = False
                carryover_mode = 'auto'
                sunday_carryover_das = 0


        elif engine_type == 'tunable':
            # Tunable Engine (v12.5) — same base settings as demand_driven + priority sliders
            night_shift = st.checkbox("🌙 Enable Night Shift", value=True,
                key="tun_night_shift",
                help="Allow overnight shifts (19:00-05:00)")
            flexible_day_off = st.checkbox("📅 Flexible Day Off (allow Fri/Sat off)", value=True,
                key="tun_flexible_day_off",
                help="If unchecked, Fri/Sat are mandatory working days")

            fixed_start_optimizer = st.radio(
                "🔄 Shift Start Optimizer",
                options=['strict', 'post_off', 'flexible'],
                format_func=lambda x: {
                    'strict': '🔒 Strict (same start every day)',
                    'post_off': '📅 Post-Off (different start only after off day)',
                    'flexible': '🔓 Flexible (optimizer can change any day)'
                }[x],
                key="tun_fixed_start_optimizer",
                help="Controls whether the optimizer can change DA start times in fixed start mode (shift+rest=24h)",
                horizontal=True
            )

            max_shifts = st.slider(
                "📌 Max Shift Starts (0 = unlimited)",
                min_value=0, max_value=24,
                value=st.session_state.get('tun_max_shifts_val', 0),
                key="tun_max_shifts",
                help="Limit the number of unique shift start times. 0 = engine picks as many as needed."
            )

            # Priority sliders
            st.markdown("### 🎚️ Hour Priorities (-10 to +10)")
            st.caption("0 = neutral, positive = boost demand, negative = suppress demand")
            if 'tunable_priorities' not in st.session_state:
                st.session_state.tunable_priorities = {h: 0 for h in range(24)}
            cols = st.columns(6)
            for h in range(24):
                with cols[h % 6]:
                    st.session_state.tunable_priorities[h] = st.slider(
                        f"{h:02d}:00", -10, 10,
                        value=st.session_state.tunable_priorities.get(h, 0),
                        key=f"tun_prio_{h}"
                    )

            if night_shift:
                st.markdown("**Sunday Carryover Settings**")
                carryover_mode = st.radio(
                    "Sunday Early Morning (00:00-05:00)",
                    options=['auto', 'manual', 'skip'],
                    format_func=lambda x: {'auto': '🔄 Auto', 'manual': '✏️ Manual', 'skip': '⏭️ Skip'}[x],
                    horizontal=True, key="tun_carryover_mode"
                )
                if carryover_mode == 'manual':
                    sunday_carryover_das = st.number_input(
                        "Carryover DAs for Sunday 00:00-05:00",
                        min_value=0, max_value=50,
                        value=st.session_state.get('sunday_carryover_das_tun', 0),
                        key="tun_carryover_das"
                    )
                    st.session_state['sunday_carryover_das_tun'] = sunday_carryover_das
                else:
                    sunday_carryover_das = 0
                skip_sunday_overnight = (carryover_mode == 'skip')
            else:
                skip_sunday_overnight = False
                carryover_mode = 'auto'
                sunday_carryover_das = 0


        st.header("📋 Working Rules")
        
        # Initialize working rules in session state if not set
        if 'working_rules' not in st.session_state:
            st.session_state.working_rules = {
                'shift_hours': 10,
                'break_hours': 1,
                'max_continuous': 5,
                'min_rest': 14,
                'working_days': 6
            }
        
        # Shift duration always defaults to 9 hours
        shift_hours = st.slider(
            "Shift Duration (hours)",
            min_value=8,
            max_value=14,
            value=st.session_state.working_rules.get('shift_hours', 10),
            help="Total shift length including break",
            key="slider_shift_hours"
        )
        
        # Check if shift_hours changed and clear optimized shifts
        if shift_hours != st.session_state.working_rules.get('shift_hours', 10):
            st.session_state.working_rules['shift_hours'] = shift_hours
            # Clear ALL optimized shifts to force regeneration with new shift duration
            for key in list(st.session_state.keys()):
                if key.startswith('optimized_shifts_'):
                    del st.session_state[key]
            st.rerun()  # Force rerun to regenerate with new params
        
        break_hours = st.slider(
            "Break Duration (hours)",
            min_value=0,
            max_value=2,
            value=st.session_state.working_rules.get('break_hours', 1),
            help="Break time within shift",
            key="slider_break_hours"
        )
        
        # Check if break_hours changed
        if break_hours != st.session_state.working_rules.get('break_hours', 1):
            st.session_state.working_rules['break_hours'] = break_hours
            for key in list(st.session_state.keys()):
                if key.startswith('optimized_shifts_'):
                    del st.session_state[key]
            st.rerun()
        
        max_continuous = st.slider(
            "Max Continuous Work (hours)",
            min_value=3,
            max_value=6,
            value=st.session_state.working_rules.get('max_continuous', 5),
            help="Maximum hours before break is required",
            key="slider_max_continuous"
        )
        
        # Check if max_continuous changed
        if max_continuous != st.session_state.working_rules.get('max_continuous', 5):
            st.session_state.working_rules['max_continuous'] = max_continuous
            for key in list(st.session_state.keys()):
                if key.startswith('optimized_shifts_'):
                    del st.session_state[key]
            st.rerun()
        
        # Min rest defaults to 15 always
        default_min_rest = 14
        
        min_rest = st.slider(
            "Min Rest Between Shifts (hours)",
            min_value=8,
            max_value=15,
            value=st.session_state.working_rules.get('min_rest', default_min_rest),
            help="Minimum rest period between shifts (15h default)",
            key="slider_min_rest"
        )
        
        # Check if min_rest changed
        if min_rest != st.session_state.working_rules.get('min_rest', default_min_rest):
            st.session_state.working_rules['min_rest'] = min_rest
            for key in list(st.session_state.keys()):
                if key.startswith('optimized_shifts_'):
                    del st.session_state[key]
            st.rerun()
        
        # Show fixed start time mode indicator for v12.4
        if engine_type in ('demand_driven', 'demand_driven_ultimate', 'tunable') and shift_hours + min_rest == 24:
            st.info(f"🔒 Fixed Start Mode: shift ({shift_hours}h) + rest ({min_rest}h) = 24h → each DA works the same start time every day")
        
        working_days = st.slider(
            "Working Days per Week",
            min_value=5,
            max_value=7,
            value=st.session_state.working_rules.get('working_days', 6),
            help="Number of working days (rest = 7 - working)",
            key="slider_working_days"
        )
        
        # Check if working_days changed
        if working_days != st.session_state.working_rules.get('working_days', 6):
            st.session_state.working_rules['working_days'] = working_days
            for key in list(st.session_state.keys()):
                if key.startswith('optimized_shifts_'):
                    del st.session_state[key]
            st.rerun()

        # Flex (part-time) shift duration — used by the Flex gap-fill feature
        flex_shift_hours = st.slider(
            "⚡ Flex Shift Duration (hours)",
            min_value=2, max_value=6,
            value=st.session_state.get('flex_shift_hours_val', 4),
            key="flex_shift_hours_slider",
            help="Duration of part-time flex DA shifts used to fill coverage gaps"
        )
        st.session_state['flex_shift_hours_val'] = flex_shift_hours

        # Show effective hours
        effective_hours = shift_hours - break_hours
        st.info(f"**Effective Work Hours:** {effective_hours}h per shift")
        
        # Warn if shift_hours + min_rest > 24 (impossible combination)
        if shift_hours + min_rest > 24:
            st.warning(f"⚠️ Shift duration ({shift_hours}h) + min rest ({min_rest}h) = {shift_hours + min_rest}h > 24h. "
                       f"Rest violations are unavoidable. Reduce shift hours or min rest to fit within 24h.")
        
        # Default values for removed tuner/multiplier settings
        scale_mode = 'exponential'
        intensity = 2.0
        
        if 'day_multipliers' not in st.session_state:
            day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
            st.session_state.day_multipliers = {day: 1.0 for day in day_names}

        # Build carryover_excel_data from session state
        carryover_excel_data = []  # List of {DA_ID, Store, Sat_Shift_End}
        if carryover_mode == 'excel':
            excel_key = 'carryover_excel_data_fixed' if engine_type == 'fixed' else 'carryover_excel_data'
            if excel_key in st.session_state:
                df = st.session_state[excel_key]
                if isinstance(df, pd.DataFrame):
                    for _, row in df.iterrows():
                        # New format (from roster upload): has DA_ID directly
                        if 'DA_ID' in row.index and pd.notna(row.get('DA_ID')):
                            sat_start = row.get('Sat_Shift_Start', 0)
                            sat_end = row.get('Sat_Shift_End', 5)
                            carryover_excel_data.append({
                                'DA_ID': row['DA_ID'],
                                'Store': row.get('Store', ''),
                                'Sat_Shift_Start': int(sat_start) if pd.notna(sat_start) else 0,
                                'Sat_Shift_End': int(sat_end) if pd.notna(sat_end) else 5
                            })
                        # Old template format: build DA_ID from parts
                        else:
                            store = row.get('Store', row.get('Station', ''))
                            dsp_code = row.get('DSP_Code', row.get('DSP', ''))
                            da_num = row.get('DA_Number', row.get('DA_Num', 1))
                            sat_end = row.get('Sat_Shift_End', 5)
                            if store and dsp_code and pd.notna(da_num):
                                da_id = f"{store}-{dsp_code}-{str(int(da_num)).zfill(3)}"
                                carryover_excel_data.append({
                                    'DA_ID': da_id,
                                    'Store': store,
                                    'Sat_Shift_End': int(sat_end) if pd.notna(sat_end) else 5
                                })
                elif isinstance(df, list):
                    carryover_excel_data = df
        
        params = {
            'night_shift': night_shift,
            'flexible_day_off': flexible_day_off,
            'fixed_start_optimizer': fixed_start_optimizer if engine_type in ('demand_driven', 'demand_driven_ultimate', 'tunable') else 'flexible',
            'max_shifts': max_shifts if engine_type in ('demand_driven', 'demand_driven_ultimate', 'tunable') else 0,
            'overnight_start': None,
            'overnight_enabled': False,
            'skip_sunday_overnight': skip_sunday_overnight,
            'carryover_mode': carryover_mode,
            'sunday_carryover_das': sunday_carryover_das,
            'carryover_excel_data': carryover_excel_data,
            'shift_hours': shift_hours,
            'break_hours': break_hours,
            'max_continuous': max_continuous,
            'min_rest': min_rest,
            'working_days': working_days,
            'scale_mode': scale_mode,
            'intensity': intensity,
            'flex_shift_hours': flex_shift_hours,
        }
    
    if uploaded_file is None:
        st.info("👆 Please upload a capacity planning file to get started")
        
        # Show how to use the app
        st.markdown("""
        ### How to Use AART
        
        1. **Upload your input file** — Excel with 3 sheets: `Slot Level DA Requirement`, `Available DAs`, and optionally `Store Parameters`
        2. **Select a week** — Choose the planning week from the dropdown
        3. **Configure working rules** — Set shift duration (10h), break (1h), min rest (14h), max shifts (8) in the sidebar
        4. **Select a store** — Pick a store from the dropdown to generate its roster
        5. **Review the roster** — Check the heatmap, hourly comparison, daily breakdown, and violation detector
        6. **Upload previous week roster** *(optional)* — For Sunday carryover connection, upload last week's roster Excel under Sunday Carryover Settings
        7. **Generate all stores** — Switch to "All Stores" download scope and click Generate to roster all stores at once
        8. **Download** — Export the roster as Excel with Hourly Roster, Shift Details, and Sunday Carryover sheets
        
        ### Store Parameters (Optional)
        Add a `Store Parameters` sheet to your input file to override global settings per store:
        - `operating_start` / `operating_end` — Store operating hours (e.g., 6-22)
        - `shift_hours` / `break_hours` — Custom shift duration per store
        - `min_rest` — Minimum rest between shifts
        - `operating_days` — Which days the store operates
        
        ### Key Features
        - Demand-driven shift optimization with configurable max shift starts
        - Fixed start mode when shift + rest = 24h (each DA works same start daily)
        - Store-level parameter overrides from Excel
        - Saturday→Sunday carryover connection from previous week
        - Violation detection for rest, max continuous work, and zero coverage
        - Network report across all stores with gap and utilization metrics
        """)
        return
    
    # Load data
    try:
        demand_df, das_df, store_configs, store_params_warnings = load_data(uploaded_file, selected_week)
        st.session_state['store_configs'] = store_configs
        stores = sorted(demand_df['Store'].unique())
        total_das = das_df['DA_Count'].sum()
        st.success(f"✅ Loaded {len(stores)} stores | {selected_week} | Total DAs: {int(total_das)}")
        
        # Show DA summary in expander
        with st.expander("📊 DA Pool Summary (click to verify data)", expanded=False):
            da_summary = das_df.groupby('Store')['DA_Count'].sum().reset_index()
            da_summary.columns = ['Store', 'Total DAs']
            da_summary = da_summary.sort_values('Total DAs', ascending=False)
            st.dataframe(da_summary, use_container_width=True, height=200)
            st.caption(f"File: {uploaded_file.name} | Week: {selected_week}")

            if 'Flex_Count' in das_df.columns:
                total_flex_pool = int(das_df['Flex_Count'].sum())
                stores_with_flex = das_df[das_df['Flex_Count'] > 0]['Store'].nunique()
                if total_flex_pool > 0:
                    st.info(
                        f"⚡ Flex (Part-Time) DAs: {total_flex_pool} "
                        f"across {stores_with_flex} stores"
                    )
            
    except Exception as e:
        st.error(f"Error loading file: {e}")
        return
    
    # Initialize per-store priorities (NEW in v2)
    if 'store_priorities' not in st.session_state:
        st.session_state.store_priorities = {}
    if 'selected_store' not in st.session_state:
        st.session_state.selected_store = stores[0]
    
    # Add keyboard shortcuts for undo/redo
    add_keyboard_shortcuts()
    
    # Main tabs
    tab1, tab2 = st.tabs(["🏪 Store Roster", "🔄 DA Transfers"])
    
    # =============================================================================
    # TAB 1: SINGLE STORE TUNER (Original v12 functionality)
    # =============================================================================
    with tab1:
        # Initialize Flex gap-fill state (always defined to avoid NameError)
        flex_shifts_df = None
        flex_roster_df = None
        store_flex_count = 0

        # Undo/Redo buttons at the top
        undo_col1, undo_col2, undo_col3, undo_col4 = st.columns([1, 1, 1, 5])
        with undo_col1:
            undo_disabled = not can_undo()
            if st.button("↩️ Undo", disabled=undo_disabled, key="undo_btn", help="Ctrl+Z"):
                if undo():
                    st.rerun()
        with undo_col2:
            redo_disabled = not can_redo()
            if st.button("↪️ Redo", disabled=redo_disabled, key="redo_btn", help="Ctrl+Y"):
                if redo():
                    st.rerun()
        with undo_col3:
            undo_count = len(st.session_state.get('undo_stack', []))
            redo_count = len(st.session_state.get('redo_stack', []))
            st.caption(f"History: {undo_count} undo / {redo_count} redo")
        
        # Store selection
        col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
        with col1:
            idx = stores.index(st.session_state.selected_store) if st.session_state.selected_store in stores else 0
            selected_store = st.selectbox("Select Store", stores, index=idx, key="store_select")
            st.session_state.selected_store = selected_store
        with col2:
            # Show transfer adjustment for this store
            transfers = load_transfers()
            store_adjustment = get_store_da_adjustment(selected_store, transfers)
            if store_adjustment != 0:
                if store_adjustment > 0:
                    st.info(f"📥 +{store_adjustment} DAs (transferred in)")
                else:
                    st.warning(f"📤 {store_adjustment} DAs (transferred out)")
        with col3:
            # Show original vs adjusted DA count
            original_das = das_df[das_df['Store'] == selected_store]['DA_Count'].sum()
            adjusted_das = original_das + store_adjustment
            st.metric("DAs", f"{adjusted_das}", delta=f"{store_adjustment:+d}" if store_adjustment != 0 else None)
        with col4:
            if selected_store in st.session_state.store_priorities:
                st.success("✅ Optimized")
        
        # Load priorities for this store
        if selected_store not in st.session_state.store_priorities:
            st.session_state.store_priorities[selected_store] = {h: 5 for h in range(24)}
        
        # Use per-store priorities
        slot_priorities = st.session_state.store_priorities[selected_store]
    
        # Get current engine type for UI decisions
        current_engine = st.session_state.get('engine_type', 'flexible')
        
        # =============================================================================
        # PERSISTENT HISTORY PANEL
        # =============================================================================
        st.markdown("---")
        
        # Auto-save toggle and status
        if 'auto_save_enabled' not in st.session_state:
            st.session_state.auto_save_enabled = True
        
        history_col1, history_col2, history_col3, history_col4 = st.columns([1, 1, 1, 1])
        
        with history_col1:
            auto_save = st.checkbox("💾 Auto-Save", value=st.session_state.auto_save_enabled, 
                                   help="Automatically save changes to disk", key="auto_save_toggle")
            st.session_state.auto_save_enabled = auto_save
        
        with history_col2:
            # Persistent undo button
            can_p_undo = can_persistent_undo(selected_week, selected_store)
            if st.button("⏮️ Undo", disabled=not can_p_undo, key="persistent_undo_btn", 
                        help="Undo to previous saved state"):
                shifts_df, msg = persistent_undo(selected_week, selected_store)
                if shifts_df is not None:
                    st.session_state[f'optimized_shifts_{selected_store}'] = shifts_df
                    st.success(msg)
                    st.rerun()
        
        with history_col3:
            # Persistent redo button
            can_p_redo = can_persistent_redo(selected_week, selected_store)
            if st.button("⏭️ Redo", disabled=not can_p_redo, key="persistent_redo_btn",
                        help="Redo to next saved state"):
                shifts_df, msg = persistent_redo(selected_week, selected_store)
                if shifts_df is not None:
                    st.session_state[f'optimized_shifts_{selected_store}'] = shifts_df
                    st.success(msg)
                    st.rerun()
        
        with history_col4:
            # Show history count
            history_items, current_idx = get_persistent_history_display(selected_week, selected_store)
            if history_items:
                st.caption(f"📜 Step {current_idx + 1} of {len(history_items)}")
            else:
                st.caption("📜 No history yet")
        
        # Expandable history panel
        with st.expander("📜 History & Checkpoints", expanded=False):
            hist_tab1, hist_tab2 = st.tabs(["📜 History", "🔒 Checkpoints"])
            
            with hist_tab1:
                history_items, current_idx = get_persistent_history_display(selected_week, selected_store)
                
                if history_items:
                    # History list
                    for item in reversed(history_items):  # Show newest first
                        col_a, col_b = st.columns([4, 1])
                        with col_a:
                            if item['is_current']:
                                st.markdown(f"**● [{item['index']+1}] {item['action']}** - Gap: {item['gap']} ({item['timestamp']})")
                            else:
                                st.markdown(f"  [{item['index']+1}] {item['action']} - Gap: {item['gap']} ({item['timestamp']})")
                        with col_b:
                            if not item['is_current']:
                                if st.button("↩️", key=f"jump_{item['index']}", help="Jump to this state"):
                                    shifts_df, msg = persistent_jump_to(selected_week, selected_store, item['index'])
                                    if shifts_df is not None:
                                        st.session_state[f'optimized_shifts_{selected_store}'] = shifts_df
                                        st.success(msg)
                                        st.rerun()
                    
                    st.markdown("---")
                    del_col1, del_col2 = st.columns(2)
                    with del_col1:
                        if st.button("🗑️ Clear Store History", key="clear_hist", help="Clear history for this store"):
                            clear_store_history(selected_week, selected_store)
                            st.success("Store history cleared!")
                            st.rerun()
                    with del_col2:
                        if st.button("💣 Delete ALL History", key="delete_all_hist", type="secondary",
                                    help="Delete all history, checkpoints, and optimized shifts for ALL stores"):
                            # Clear all persistent saves
                            import glob
                            for f in glob.glob(os.path.join(ROSTER_SAVES_DIR, '*.json')):
                                os.remove(f)
                            # Clear all session state
                            for key in list(st.session_state.keys()):
                                if key.startswith(('optimized_shifts_', 'shift_shares_', 'shift_timings_',
                                                  'persistent_loaded_', 'store_priorities')):
                                    del st.session_state[key]
                            if 'store_priorities' in st.session_state:
                                st.session_state.store_priorities = {}
                            st.success("All history deleted for all stores!")
                            st.rerun()
                else:
                    st.info("No history yet. Run an optimization to start tracking changes.")
            
            with hist_tab2:
                checkpoints = get_checkpoints_display(selected_week, selected_store)
                
                # Save checkpoint form
                cp_col1, cp_col2 = st.columns([3, 1])
                with cp_col1:
                    checkpoint_name = st.text_input("Checkpoint name", placeholder="e.g., Before experiment", 
                                                   key="checkpoint_name_input")
                with cp_col2:
                    st.write("")  # Spacer
                    st.write("")  # Spacer
                    optimized_key = f'optimized_shifts_{selected_store}'
                    has_shifts = optimized_key in st.session_state and st.session_state[optimized_key] is not None
                    if st.button("🔒 Save", disabled=not has_shifts or not checkpoint_name, key="save_checkpoint_btn"):
                        shifts_df = st.session_state[optimized_key]
                        # Calculate current gap
                        store_demand = demand_df[demand_df['Store'] == selected_store]
                        engine_params = engine_get_params(params)
                        roster_df = engine_generate_hourly_roster(shifts_df, store_demand, engine_params)
                        gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                        
                        if save_checkpoint(selected_week, selected_store, shifts_df, checkpoint_name, gap):
                            st.success(f"✅ Checkpoint '{checkpoint_name}' saved!")
                            st.rerun()
                
                if checkpoints:
                    st.markdown("**Saved Checkpoints:**")
                    for cp in checkpoints:
                        cp_col_a, cp_col_b, cp_col_c = st.columns([3, 1, 1])
                        with cp_col_a:
                            st.markdown(f"🔒 **{cp['name']}** - Gap: {cp['gap']} ({cp['timestamp']})")
                        with cp_col_b:
                            if st.button("↩️ Load", key=f"load_cp_{cp['index']}", help="Load this checkpoint"):
                                shifts_df, msg = load_checkpoint(selected_week, selected_store, cp['index'])
                                if shifts_df is not None:
                                    st.session_state[f'optimized_shifts_{selected_store}'] = shifts_df
                                    # Also save to history
                                    if st.session_state.auto_save_enabled:
                                        store_demand = demand_df[demand_df['Store'] == selected_store]
                                        engine_params = engine_get_params(params)
                                        roster_df = engine_generate_hourly_roster(shifts_df, store_demand, engine_params)
                                        gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                                        save_to_persistent_history(selected_week, selected_store, shifts_df, 
                                                                  f"Loaded checkpoint: {cp['name']}", gap)
                                    st.success(msg)
                                    st.rerun()
                        with cp_col_c:
                            if st.button("🗑️", key=f"del_cp_{cp['index']}", help="Delete this checkpoint"):
                                if delete_checkpoint(selected_week, selected_store, cp['index']):
                                    st.success("Checkpoint deleted!")
                                    st.rerun()
                else:
                    st.info("No checkpoints saved. Use checkpoints to lock in good results.")
        
        # Load from persistent storage on first load for this store
        if f'persistent_loaded_{selected_store}' not in st.session_state:
            saved_shifts = load_current_from_persistent(selected_week, selected_store)
            if saved_shifts is not None:
                st.session_state[f'optimized_shifts_{selected_store}'] = saved_shifts
                st.toast(f"💾 Loaded saved state for {selected_store}")
            st.session_state[f'persistent_loaded_{selected_store}'] = True
        

                # ===== SMART CHAIN OPTIMIZER (Fixed Shifts) =====
        if current_engine == 'fixed':
            st.markdown("---")
            st.markdown("#### 🔗 Smart Chain Optimizer")
            st.markdown("*Runs Shift Rebalancing → Off-Day Redistribution → Break Timing until no improvement*")
            
            smart_col1, smart_col2 = st.columns([3, 1])
            with smart_col1:
                st.caption("Deterministic optimizer that systematically improves the roster by:")
                st.caption("1. Moving DAs from excess shifts to gap shifts")
                st.caption("2. Moving off days from high-demand to low-demand days")
                st.caption("3. Adjusting break timing (hour 4 or 5)")
            
            with smart_col2:
                run_smart_chain = st.button("🚀 Run Smart Chain", type="primary", key="run_fixed_smart_chain")
            
            if run_smart_chain:
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                def update_chain_progress(current, total, best_gap):
                    progress_bar.progress(current / total)
                    status_text.text(f"Smart Chain: Step {current}/{total} | Gap: {best_gap:.0f}")
                
                # Get current shifts
                optimized_key = f'optimized_shifts_{selected_store}'
                if optimized_key in st.session_state and st.session_state[optimized_key] is not None:
                    current_shifts = st.session_state[optimized_key].copy()
                else:
                    # Generate fresh shifts
                    status_text.text("Generating initial shifts...")
                    store_das = das_df[das_df['Store'] == selected_store].copy()
                    da_list = fixed_build_da_list(store_das)
                    current_shifts = assign_shifts_fixed(da_list, demand_df, selected_store, engine_params, current_shares)
                
                # Calculate baseline gap
                store_demand = demand_df[demand_df['Store'] == selected_store]
                baseline_roster = fixed_generate_hourly_roster(current_shifts, store_demand, engine_params)
                baseline_gap = abs(baseline_roster[baseline_roster['Diff'] < 0]['Diff'].sum())
                
                # Run Smart Chain with progress
                optimized_shifts, total_changes, iterations = fixed_smart_chain_optimizer(
                    current_shifts, demand_df, selected_store, engine_params,
                    progress_callback=update_chain_progress
                )
                
                progress_bar.empty()
                status_text.empty()
                
                # Calculate new gap
                new_roster = fixed_generate_hourly_roster(optimized_shifts, store_demand, engine_params)
                new_gap = abs(new_roster[new_roster['Diff'] < 0]['Diff'].sum())
                
                if new_gap < baseline_gap:
                    save_state_for_undo()
                    st.session_state[optimized_key] = optimized_shifts
                    improvement = baseline_gap - new_gap
                    pct = (improvement / baseline_gap * 100) if baseline_gap > 0 else 0
                    st.success(f"✅ Smart Chain Complete! Gap: {baseline_gap:.0f} → {new_gap:.0f} (↓{improvement:.0f}, {pct:.1f}%) | {iterations} iterations, {total_changes} changes")
                    st.rerun()
                else:
                    st.info(f"ℹ️ Smart Chain ran {iterations} iterations but found no improvement. Current roster is already optimal.")
        
        st.markdown("---")
        
        # Slider grid for all 24 hours (only for flexible engine)
        if current_engine in ('flexible', 'proportional'):
            st.markdown("#### Hour-by-Hour Priority (0=Low, 10=High)")
            
            slot_priorities = st.session_state.store_priorities[selected_store]
            
            # Only force-sync widget keys when optimizer has updated priorities
            # (flagged by optimizer setting this key)
            sync_key = f'_priority_sync_{selected_store}'
            if st.session_state.get(sync_key, False):
                for h in range(24):
                    widget_key = f"priority_{selected_store}_{h}"
                    st.session_state[widget_key] = slot_priorities.get(h, 5)
                st.session_state[sync_key] = False
            
            priority_changed = False
            for row in range(4):
                cols = st.columns(6)
                for col_idx, col in enumerate(cols):
                    hour = row * 6 + col_idx
                    with col:
                        current_value = slot_priorities.get(hour, 5)
                        color = "🔴" if current_value >= 8 else "🟠" if current_value >= 5 else "🟢"
                        new_priority = st.slider(
                            f"{color} {hour:02d}:00",
                            min_value=0,
                            max_value=10,
                            value=current_value,
                            key=f"priority_{selected_store}_{hour}"
                        )
                        if new_priority != current_value:
                            st.session_state.store_priorities[selected_store][hour] = new_priority
                            priority_changed = True
            
            if priority_changed:
                save_state_for_undo()
                # Clear optimized shifts when priorities change
                optimized_key = f'optimized_shifts_{selected_store}'
                if optimized_key in st.session_state:
                    del st.session_state[optimized_key]
                st.rerun()
            
            # Show priority summary
            non_default = {h: p for h, p in slot_priorities.items() if p != 5}
            if non_default:
                high = [f"{h:02d}:00 ({p})" for h, p in sorted(non_default.items()) if p > 5]
                low = [f"{h:02d}:00 ({p})" for h, p in sorted(non_default.items()) if p < 5]
                summary_parts = []
                if high:
                    summary_parts.append(f"🔴 Boosted: {', '.join(high)}")
                if low:
                    summary_parts.append(f"🟢 Reduced: {', '.join(low)}")
                st.caption(" | ".join(summary_parts))
            else:
                st.caption("All priorities at default (5). Move sliders above 5 to boost hours, below 5 to reduce.")
        
        # Generate roster with current priorities
        st.markdown("---")
        
        # Check if we have optimized shifts stored for this store
        optimized_key = f'optimized_shifts_{selected_store}'
        
        # Auto-clear cached shifts when engine type or key params change
        cache_sig_key = f'_engine_sig_{selected_store}'
        current_sig = f"{current_engine}|{params.get('night_shift', True)}|{params.get('flexible_day_off', False)}|{params.get('shift_hours', 10)}|{params.get('working_days', 6)}|{params.get('min_rest', 12)}|{params.get('max_shifts', 0)}|{params.get('fixed_start_optimizer', 'post_off')}|{str(sorted(st.session_state.get('tunable_priorities', {}).items())) if current_engine == 'tunable' else ''}|{params.get('overnight_start', '') if current_engine == 'overnight' else ''}|{params.get('overnight_enabled', '') if current_engine == 'overnight' else ''}"
        prev_sig = st.session_state.get(cache_sig_key, None)
        if prev_sig is not None and prev_sig != current_sig:
            # Engine settings changed — clear cached shifts to force regeneration
            if optimized_key in st.session_state:
                del st.session_state[optimized_key]
        st.session_state[cache_sig_key] = current_sig
        
        if optimized_key in st.session_state and st.session_state[optimized_key] is not None:
            # Use optimized shifts instead of regenerating
            shifts_df = st.session_state[optimized_key]
            store_demand = demand_df[demand_df['Store'] == selected_store]
            
            if current_engine == 'fixed':
                custom_shifts = st.session_state.get('custom_fixed_shifts', FIXED_SHIFTS.copy())
                engine_params = fixed_get_params({
                    'shift_hours': params.get('shift_hours', 10),
                    'break_hours': params.get('break_hours', 1),
                    'max_continuous': params.get('max_continuous', 5),
                    'min_rest': params.get('min_rest', 12),
                    'working_days': params.get('working_days', 6),
                    'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                    'custom_shifts': custom_shifts
                })
                roster_df = fixed_generate_hourly_roster(shifts_df, store_demand, engine_params)
                opt_gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                st.info(f"📌 Using optimized shifts (Fixed Engine). Gap: {opt_gap:.0f}. Click 'Reset' to regenerate.")
            elif current_engine == 'flexible':
                engine_params = engine_get_params({
                    'night_shift_enabled': params.get('night_shift', True),
                    'flexible_day_off': params.get('flexible_day_off', False),
                    'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                    'carryover_mode': params.get('carryover_mode', 'auto'),
                    'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                    'carryover_excel_data': params.get('carryover_excel_data', []),
                    'shift_hours': params.get('shift_hours', 10),
                    'break_hours': params.get('break_hours', 1),
                    'max_continuous': params.get('max_continuous', 5),
                    'min_rest': params.get('min_rest', 12),
                    'working_days': params.get('working_days', 6)
                })
                roster_df = engine_generate_hourly_roster(shifts_df, store_demand, engine_params)
                opt_gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                st.info(f"📌 Using optimized shifts (Flexible Engine). Gap: {opt_gap:.0f}. Click 'Reset' to regenerate.")
            elif current_engine == 'proportional':
                engine_params = v13_get_params({
                    'night_shift_enabled': params.get('night_shift', True),
                    'flexible_day_off': params.get('flexible_day_off', False),
                    'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                    'carryover_mode': params.get('carryover_mode', 'auto'),
                    'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                    'carryover_excel_data': params.get('carryover_excel_data', []),
                    'shift_hours': params.get('shift_hours', 10),
                    'break_hours': params.get('break_hours', 1),
                    'max_continuous': params.get('max_continuous', 5),
                    'min_rest': params.get('min_rest', 12),
                    'working_days': params.get('working_days', 6)
                })
                roster_df = v13_generate_hourly_roster(shifts_df, store_demand, engine_params)
                opt_gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                st.info(f"📌 Using optimized shifts (Proportional v12.3). Gap: {opt_gap:.0f}. Click 'Reset' to regenerate.")
            elif current_engine == 'demand_driven':
                engine_params = v14_get_params({
                    'night_shift_enabled': params.get('night_shift', True),
                    'flexible_day_off': params.get('flexible_day_off', False),
                    'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                    'carryover_mode': params.get('carryover_mode', 'auto'),
                    'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                    'carryover_excel_data': params.get('carryover_excel_data', []),
                    'shift_hours': params.get('shift_hours', 10),
                    'break_hours': params.get('break_hours', 1),
                    'max_continuous': params.get('max_continuous', 5),
                    'min_rest': params.get('min_rest', 12),
                    'working_days': params.get('working_days', 6),
                    'fixed_start_optimizer': params.get('fixed_start_optimizer', 'post_off'),
                    'max_shifts': params.get('max_shifts', 0)
                })
                roster_df = v14_generate_hourly_roster(shifts_df, store_demand, engine_params)
                opt_gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                st.info(f"📌 Using optimized shifts (Demand-Driven v12.4). Gap: {opt_gap:.0f}. Click 'Reset' to regenerate.")
            elif current_engine == 'demand_driven_ultimate':
                engine_params = v14u_get_params({
                    'night_shift_enabled': params.get('night_shift', True),
                    'flexible_day_off': params.get('flexible_day_off', False),
                    'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                    'carryover_mode': params.get('carryover_mode', 'auto'),
                    'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                    'carryover_excel_data': params.get('carryover_excel_data', []),
                    'shift_hours': params.get('shift_hours', 10),
                    'break_hours': params.get('break_hours', 1),
                    'max_continuous': params.get('max_continuous', 5),
                    'min_rest': params.get('min_rest', 12),
                    'working_days': params.get('working_days', 6),
                    'fixed_start_optimizer': params.get('fixed_start_optimizer', 'post_off'),
                    'max_shifts': params.get('max_shifts', 0)
                })
                roster_df = v14u_generate_hourly_roster(shifts_df, store_demand, engine_params)
                opt_gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                st.info(f"📌 Using optimized shifts (Ultimate v12.4u). Gap: {opt_gap:.0f}. Click 'Reset' to regenerate.")
            elif current_engine == 'tunable':
                engine_params = v15_get_params({
                    'night_shift_enabled': params.get('night_shift', True),
                    'flexible_day_off': params.get('flexible_day_off', False),
                    'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                    'carryover_mode': params.get('carryover_mode', 'auto'),
                    'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                    'carryover_excel_data': params.get('carryover_excel_data', []),
                    'shift_hours': params.get('shift_hours', 10),
                    'break_hours': params.get('break_hours', 1),
                    'max_continuous': params.get('max_continuous', 5),
                    'min_rest': params.get('min_rest', 12),
                    'working_days': params.get('working_days', 6),
                    'fixed_start_optimizer': params.get('fixed_start_optimizer', 'post_off'),
                    'max_shifts': params.get('max_shifts', 0),
                    'priorities': st.session_state.get('tunable_priorities', {}),
                })
                roster_df = v15_generate_hourly_roster(shifts_df, store_demand, engine_params)
                opt_gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                st.info(f"📌 Using optimized shifts (Tunable v12.5). Gap: {opt_gap:.0f}. Click 'Reset' to regenerate.")
        else:
            if current_engine == 'fixed':
                # Use Fixed Shifts Engine
                with st.spinner("Generating roster with fixed shifts..."):
                    # Apply transfer adjustments to full das_df first, then filter
                    transfers = load_transfers()
                    adjusted_das_df = apply_transfer_adjustments(das_df, transfers)
                    store_das = adjusted_das_df[adjusted_das_df['Store'] == selected_store].copy()
                    
                    if store_das.empty or store_das['DA_Count'].sum() == 0:
                        roster_df, shifts_df = None, None
                    else:
                        # Get custom shifts
                        custom_shifts = st.session_state.get('custom_fixed_shifts', FIXED_SHIFTS.copy())
                        
                        engine_params = fixed_get_params({
                            'shift_hours': params.get('shift_hours', 10),
                            'break_hours': params.get('break_hours', 1),
                            'max_continuous': params.get('max_continuous', 5),
                            'min_rest': params.get('min_rest', 12),
                            'working_days': params.get('working_days', 6),
                            'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                            'custom_shifts': custom_shifts
                        })
                        
                        da_list = fixed_build_da_list(store_das)
                        store_demand = demand_df[demand_df['Store'] == selected_store]
                        
                        # Get shift shares if user has customized them
                        shift_shares_key = f'shift_shares_{selected_store}'
                        shift_shares = st.session_state.get(shift_shares_key, None)
                        
                        shifts_df = assign_shifts_fixed(da_list, demand_df, selected_store, engine_params, shift_shares)
                        roster_df = fixed_generate_hourly_roster(shifts_df, store_demand, engine_params)
                        
                        # Auto-save initial roster to persistent history (only if no history exists)
                        if st.session_state.get('auto_save_enabled', True) and shifts_df is not None:
                            history_items, _ = get_persistent_history_display(selected_week, selected_store)
                            if not history_items:  # Only save if no history exists
                                gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                                save_to_persistent_history(selected_week, selected_store, shifts_df, 
                                                          "Initial roster (Fixed)", int(gap))
            elif current_engine == 'flexible':
                # Use Flexible Shifts Engine (v12.2)
                with st.spinner("Generating roster with priority weights..."):
                    roster_df, shifts_df = generate_roster_with_priorities(
                        demand_df, das_df, selected_store,
                        st.session_state.store_priorities[selected_store], params,
                        day_multipliers=st.session_state.day_multipliers,
                        scale_mode=params.get('scale_mode', 'exponential'),
                        intensity=params.get('intensity', 2.0)
                    )
                    
                    # Auto-save initial roster to persistent history (only if no history exists)
                    if st.session_state.get('auto_save_enabled', True) and shifts_df is not None:
                        history_items, _ = get_persistent_history_display(selected_week, selected_store)
                        if not history_items:
                            gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                            save_to_persistent_history(selected_week, selected_store, shifts_df, 
                                                      "Initial roster", int(gap))
                    
                    # NOTE: Auto break optimizer disabled for speed. Re-enable when needed:
                    # ep = engine_get_params({**{k: params.get(k) for k in ['shift_hours','break_hours','max_continuous','min_rest','working_days']}, 'shift_hours': params.get('shift_hours', 10)})
                    # shifts_df, brk_changes = optimize_break_placement(shifts_df, demand_df, selected_store, ep)
                    # if brk_changes > 0:
                    #     roster_df = engine_generate_hourly_roster(shifts_df, demand_df[demand_df['Store'] == selected_store], ep)
        
            elif current_engine == 'proportional':
                # Use Proportional Engine (v12.3)
                with st.spinner("Generating roster with proportional engine..."):
                    transfers = load_transfers()
                    adjusted_das_df = apply_transfer_adjustments(das_df, transfers)
                    store_das = adjusted_das_df[adjusted_das_df['Store'] == selected_store].copy()

                    if store_das.empty or store_das['DA_Count'].sum() == 0:
                        roster_df, shifts_df = None, None
                    else:
                        engine_params = v13_get_params({
                            'night_shift_enabled': params.get('night_shift', True),
                            'flexible_day_off': params.get('flexible_day_off', False),
                            'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                            'carryover_mode': params.get('carryover_mode', 'auto'),
                            'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                            'carryover_excel_data': params.get('carryover_excel_data', []),
                            'shift_hours': params.get('shift_hours', 10),
                            'break_hours': params.get('break_hours', 1),
                            'max_continuous': params.get('max_continuous', 5),
                            'min_rest': params.get('min_rest', 12),
                            'working_days': params.get('working_days', 6)
                        })

                        da_list = v13_build_da_list(store_das)
                        store_demand = demand_df[demand_df['Store'] == selected_store]
                        shifts_df = v13_assign_shifts(da_list, store_demand, None, engine_params)
                        roster_df = v13_generate_hourly_roster(shifts_df, store_demand, engine_params)

                        # Auto-save initial roster
                        if st.session_state.get('auto_save_enabled', True) and shifts_df is not None:
                            history_items, _ = get_persistent_history_display(selected_week, selected_store)
                            if not history_items:
                                gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                                save_to_persistent_history(selected_week, selected_store, shifts_df,
                                                          "Initial roster (Proportional)", int(gap))

            elif current_engine == 'demand_driven':
                # Use Demand-Driven Engine (v12.4)
                with st.spinner("Generating roster with demand-driven engine..."):
                    transfers = load_transfers()
                    adjusted_das_df = apply_transfer_adjustments(das_df, transfers)
                    store_das = adjusted_das_df[adjusted_das_df['Store'] == selected_store].copy()

                    if store_das.empty or store_das['DA_Count'].sum() == 0:
                        roster_df, shifts_df = None, None
                    else:
                        # Resolve store-level parameter overrides
                        _sc = st.session_state.get('store_configs', {})
                        _resolved = resolve_params(params, selected_store, _sc)
                        
                        # Warn on incompatible param combos
                        for _w in validate_resolved_params(_resolved, selected_store):
                            st.warning(_w)
                        
                        # Apply operating window to demand
                        apply_operating_window(demand_df, selected_store, _sc)
                        
                        engine_params = v14_get_params({
                            'night_shift_enabled': _resolved.get('night_shift_enabled', _resolved.get('night_shift', True)),
                            'flexible_day_off': _resolved.get('flexible_day_off', False),
                            'skip_sunday_overnight': _resolved.get('skip_sunday_overnight', False),
                            'carryover_mode': _resolved.get('carryover_mode', 'auto'),
                            'sunday_carryover_das': _resolved.get('sunday_carryover_das', 0),
                            'carryover_excel_data': _resolved.get('carryover_excel_data', []),
                            'shift_hours': _resolved.get('shift_hours', 10),
                            'break_hours': _resolved.get('break_hours', 1),
                            'max_continuous': _resolved.get('max_continuous', 5),
                            'min_rest': _resolved.get('min_rest', 14),
                            'working_days': _resolved.get('working_days', 6),
                            'fixed_start_optimizer': _resolved.get('fixed_start_optimizer', 'strict'),
                            'max_shifts': _resolved.get('max_shifts', 8)
                        })
                        inject_operating_window_params(engine_params, selected_store, _sc)

                        da_list = v14_build_da_list(store_das)
                        store_demand = demand_df[demand_df['Store'] == selected_store]
                        
                        # Build carryover_df from uploaded previous week data
                        _carry_data = params.get('carryover_excel_data', [])
                        _carry_df = None
                        if _carry_data and params.get('carryover_mode') == 'excel':
                            if isinstance(_carry_data, pd.DataFrame):
                                _carry_df = _carry_data
                            elif isinstance(_carry_data, list) and len(_carry_data) > 0:
                                _carry_df = pd.DataFrame(_carry_data)
                        
                        shifts_df = v14_assign_shifts(da_list, store_demand, _carry_df, engine_params)
                        
                        # Reshuffle DA identities to connect W-1 Saturday with W+1 Sunday
                        _transition_df = None
                        if _carry_df is not None and not _carry_df.empty:
                            shifts_df, _transition_df = v14_reshuffle(shifts_df, _carry_df, engine_params)
                            if _transition_df is not None and not _transition_df.empty:
                                st.session_state['da_transition_map'] = _transition_df
                                _v = len(_transition_df[_transition_df['Status'] == '❌']) if 'Status' in _transition_df.columns else 0
                                _ok = len(_transition_df) - _v
                                if _v > 0:
                                    st.warning(f"🔄 DA Reshuffle: {_ok} connected, {_v} rest violations")
                                else:
                                    st.success(f"🔄 DA Reshuffle: {_ok} DAs connected with valid rest")
                        
                        roster_df = v14_generate_hourly_roster(shifts_df, store_demand, engine_params)

                        # Carryover diagnostic
                        _carry = engine_params.get('carryover_excel_data', [])
                        _carry_mode = engine_params.get('carryover_mode', 'auto')
                        
                        # Show matching debug
                        if _carry:
                            _ci = _carry
                            if hasattr(_carry, 'iterrows'):
                                _ci = [r.to_dict() for _, r in _carry.iterrows()]
                            _carry_ids = set(c.get('DA_ID', '') for c in _ci if c.get('Store') == selected_store)
                            _engine_ids = set(shifts_df['DA_ID'].unique()) if shifts_df is not None else set()
                            _matched_ids = _carry_ids & _engine_ids
                            _unmatched_carry = _carry_ids - _engine_ids
                            st.info(f"🔍 ID Matching: {len(_matched_ids)} matched | {len(_unmatched_carry)} carryover IDs not in engine | {len(_carry_ids)} total carryover | {len(_engine_ids)} engine DAs")
                            if _unmatched_carry:
                                st.warning(f"Unmatched carryover IDs (sample): {list(_unmatched_carry)[:5]}")
                                st.warning(f"Engine DA IDs (sample): {sorted(_engine_ids)[:5]}")
                        
                        if _carry and shifts_df is not None:
                            _carry_items = _carry
                            if hasattr(_carry, 'iterrows'):
                                _carry_items = [r.to_dict() for _, r in _carry.iterrows()]
                            _store_carry = [c for c in _carry_items if c.get('Store') == selected_store]
                            if _store_carry:
                                _sun = shifts_df[shifts_df['Day'] == 'Sun']
                                _violations = 0
                                _connected = 0
                                _disconnected = 0
                                _diag_rows = []
                                _min_rest = engine_params.get('min_rest', 14)
                                for c in _store_carry:
                                    da_id = c.get('DA_ID', '')
                                    sat_end = c.get('Sat_Shift_End', 0)
                                    sat_start = c.get('Sat_Shift_Start', 0)
                                    # Compute earliest Sunday start using actual min_rest
                                    _is_on = sat_end > 0 and sat_end < sat_start
                                    if _is_on:
                                        earliest = sat_end + _min_rest
                                        if earliest >= 24: earliest -= 24
                                    elif sat_end == 0:
                                        earliest = _min_rest if _min_rest < 24 else 0
                                    else:
                                        _rem = _min_rest - (24 - sat_end)
                                        earliest = max(0, _rem)
                                    # Compute actual rest
                                    sun_row = _sun[_sun['DA_ID'] == da_id]
                                    if sun_row.empty:
                                        _disconnected += 1
                                        _diag_rows.append({'DA': da_id, 'Sat End': f"{sat_end:02d}:00", 'Earliest Sun': f"{earliest:02d}:00", 'Sun Start': 'NOT FOUND', 'Status': '❌ DA not in roster'})
                                    else:
                                        sr = sun_row.iloc[0]
                                        if sr['Is_Day_Off']:
                                            _connected += 1
                                            _diag_rows.append({'DA': da_id, 'Sat End': f"{sat_end:02d}:00", 'Earliest Sun': f"{earliest:02d}:00", 'Sun Start': 'OFF', 'Rest': None, 'Status': '✅ Off day'})
                                        elif pd.notna(sr['Shift_Start']):
                                            sun_start = int(sr['Shift_Start'])
                                            if _is_on:
                                                rest = sun_start - sat_end
                                            elif sat_end == 0:
                                                rest = sun_start
                                            else:
                                                rest = (24 - sat_end) + sun_start
                                            if rest >= _min_rest:
                                                _connected += 1
                                                _diag_rows.append({'DA': da_id, 'Sat End': f"{sat_end:02d}:00", 'Earliest Sun': f"{earliest:02d}:00", 'Sun Start': f"{sun_start:02d}:00", 'Rest': f"{rest}h", 'Status': '✅ OK'})
                                            else:
                                                _violations += 1
                                                _diag_rows.append({'DA': da_id, 'Sat End': f"{sat_end:02d}:00", 'Earliest Sun': f"{earliest:02d}:00", 'Sun Start': f"{sun_start:02d}:00", 'Rest': f"{rest}h", 'Status': f'❌ Only {rest}h rest'})
                                
                                # Show diagnostic
                                _total = len(_store_carry)
                                if _violations > 0:
                                    st.error(f"🔍 Carryover: {_total} Sat overnight DAs | ✅ {_connected} connected | ❌ {_violations} rest violations | ⚠️ {_disconnected} not found")
                                else:
                                    st.success(f"🔍 Carryover: {_total} Sat overnight DAs | ✅ {_connected} connected | ❌ {_violations} violations | ⚠️ {_disconnected} not found")
                                with st.expander(f"📋 Carryover Diagnostic ({_total} DAs)", expanded=_violations > 0):
                                    st.dataframe(pd.DataFrame(_diag_rows), use_container_width=True, hide_index=True)
                            else:
                                st.info(f"🔍 Carryover: {len(_carry_items)} total items, 0 for {selected_store}")
                        elif _carry_mode == 'excel':
                            st.warning(f"🔍 Carryover mode=excel but no data received by engine")

                        # Auto-save initial roster
                        if st.session_state.get('auto_save_enabled', True) and shifts_df is not None:
                            history_items, _ = get_persistent_history_display(selected_week, selected_store)
                            if not history_items:
                                gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                                save_to_persistent_history(selected_week, selected_store, shifts_df,
                                                          "Initial roster (Demand-Driven)", int(gap))

            elif current_engine == 'demand_driven_ultimate':
                # Use Demand-Driven Ultimate Engine (v12.4_ultimate)
                with st.spinner("Generating roster with demand-driven ultimate engine..."):
                    transfers = load_transfers()
                    adjusted_das_df = apply_transfer_adjustments(das_df, transfers)
                    store_das = adjusted_das_df[adjusted_das_df['Store'] == selected_store].copy()

                    if store_das.empty or store_das['DA_Count'].sum() == 0:
                        roster_df, shifts_df = None, None
                    else:
                        engine_params = v14u_get_params({
                            'night_shift_enabled': params.get('night_shift', True),
                            'flexible_day_off': params.get('flexible_day_off', False),
                            'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                            'carryover_mode': params.get('carryover_mode', 'auto'),
                            'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                            'carryover_excel_data': params.get('carryover_excel_data', []),
                            'shift_hours': params.get('shift_hours', 10),
                            'break_hours': params.get('break_hours', 1),
                            'max_continuous': params.get('max_continuous', 5),
                            'min_rest': params.get('min_rest', 12),
                            'working_days': params.get('working_days', 6),
                            'fixed_start_optimizer': params.get('fixed_start_optimizer', 'post_off'),
                            'max_shifts': params.get('max_shifts', 0)
                        })

                        da_list = v14u_build_da_list(store_das)
                        store_demand = demand_df[demand_df['Store'] == selected_store]
                        shifts_df = v14u_assign_shifts(da_list, store_demand, None, engine_params)
                        roster_df = v14u_generate_hourly_roster(shifts_df, store_demand, engine_params)

                        # Auto-save initial roster
                        if st.session_state.get('auto_save_enabled', True) and shifts_df is not None:
                            history_items, _ = get_persistent_history_display(selected_week, selected_store)
                            if not history_items:
                                gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                                save_to_persistent_history(selected_week, selected_store, shifts_df,
                                                          "Initial roster (Ultimate)", int(gap))

            elif current_engine == 'tunable':
                # Use Tunable Engine (v12.5)
                with st.spinner("Generating roster with tunable engine..."):
                    transfers = load_transfers()
                    adjusted_das_df = apply_transfer_adjustments(das_df, transfers)
                    store_das = adjusted_das_df[adjusted_das_df['Store'] == selected_store].copy()

                    if store_das.empty or store_das['DA_Count'].sum() == 0:
                        roster_df, shifts_df = None, None
                    else:
                        engine_params = v15_get_params({
                            'night_shift_enabled': params.get('night_shift', True),
                            'flexible_day_off': params.get('flexible_day_off', False),
                            'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                            'carryover_mode': params.get('carryover_mode', 'auto'),
                            'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                            'carryover_excel_data': params.get('carryover_excel_data', []),
                            'shift_hours': params.get('shift_hours', 10),
                            'break_hours': params.get('break_hours', 1),
                            'max_continuous': params.get('max_continuous', 5),
                            'min_rest': params.get('min_rest', 12),
                            'working_days': params.get('working_days', 6),
                            'fixed_start_optimizer': params.get('fixed_start_optimizer', 'post_off'),
                            'max_shifts': params.get('max_shifts', 0),
                            'priorities': st.session_state.get('tunable_priorities', {}),
                        })

                        da_list = v15_build_da_list(store_das)
                        store_demand = demand_df[demand_df['Store'] == selected_store]
                        shifts_df = v15_assign_shifts(da_list, store_demand, None, engine_params)
                        roster_df = v15_generate_hourly_roster(shifts_df, store_demand, engine_params)

                        # Auto-save initial roster
                        if st.session_state.get('auto_save_enabled', True) and shifts_df is not None:
                            history_items, _ = get_persistent_history_display(selected_week, selected_store)
                            if not history_items:
                                gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                                save_to_persistent_history(selected_week, selected_store, shifts_df,
                                                          "Initial roster (Tunable)", int(gap))

            elif current_engine == 'overnight':
                # Use Overnight Shift Engine (v12.7)
                with st.spinner("Generating roster with overnight shift engine..."):
                    transfers = load_transfers()
                    adjusted_das_df = apply_transfer_adjustments(das_df, transfers)
                    store_das = adjusted_das_df[adjusted_das_df['Store'] == selected_store].copy()

                    if store_das.empty or store_das['DA_Count'].sum() == 0:
                        roster_df, shifts_df = None, None
                    else:
                        engine_params = v17_get_params({
                            'night_shift_enabled': params.get('night_shift', True),
                            'flexible_day_off': params.get('flexible_day_off', False),
                            'overnight_enabled': params.get('overnight_enabled', False),
                            'overnight_start': params.get('overnight_start', 22),
                            'skip_sunday_overnight': params.get('skip_sunday_overnight', False),
                            'carryover_mode': params.get('carryover_mode', 'auto'),
                            'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                            'carryover_excel_data': params.get('carryover_excel_data', []),
                            'shift_hours': params.get('shift_hours', 10),
                            'break_hours': params.get('break_hours', 1),
                            'max_continuous': params.get('max_continuous', 5),
                            'min_rest': params.get('min_rest', 12),
                            'working_days': params.get('working_days', 6),
                        })

                        da_list = v17_build_da_list(store_das)
                        store_demand = demand_df[demand_df['Store'] == selected_store]
                        shifts_df = v17_assign_shifts(da_list, store_demand, None, engine_params)
                        roster_df = v17_generate_hourly_roster(shifts_df, store_demand, engine_params)

                        # Auto-save initial roster
                        if st.session_state.get('auto_save_enabled', True) and shifts_df is not None:
                            history_items, _ = get_persistent_history_display(selected_week, selected_store)
                            if not history_items:
                                gap = abs(roster_df[roster_df['Diff'] < 0]['Diff'].sum()) if roster_df is not None else 0
                                save_to_persistent_history(selected_week, selected_store, shifts_df,
                                                          "Initial roster (Overnight)", int(gap))

        # =========================================================================
        # FLEX (PART-TIME) DA GAP FILL — runs after full-time roster is in place
        # =========================================================================
        if 'Flex_Count' in das_df.columns:
            store_flex_count = int(
                das_df[das_df['Store'] == selected_store]['Flex_Count'].sum()
            )
        if store_flex_count > 0 and roster_df is not None and not roster_df.empty:
            st.info(
                f"⚡ {store_flex_count} Flex (Part-Time) DAs available for "
                f"{selected_store} — assigning to cover gaps..."
            )
            flex_shifts_df = assign_flex_shifts(
                das_df, demand_df, selected_store, params, roster_df
            )
            flex_roster_df = generate_flex_hourly_roster(
                flex_shifts_df, demand_df, selected_store, params
            )
            st.session_state[f'flex_shifts_{selected_store}'] = flex_shifts_df
            st.session_state[f'flex_roster_{selected_store}'] = flex_roster_df

        if roster_df is None or roster_df.empty:
            st.warning(f"No DAs available for {selected_store}")
        else:
            # Calculate all metrics
            total_required = roster_df['Required'].sum()
            total_rostered = roster_df['Rostered'].sum()
            total_gap = roster_df[roster_df['Diff'] < 0]['Diff'].sum()
            total_excess = roster_df[roster_df['Diff'] > 0]['Diff'].sum()
            
            # Calculate priority-weighted gap
            priority_gap = 0
            for _, row in roster_df.iterrows():
                if row['Diff'] < 0:
                    priority = st.session_state.store_priorities[selected_store].get(row['Slot'], 5)
                    priority_gap += row['Diff'] * (priority / 5)
            
            # Calculate DAs needed and Excess DAs
            effective_hours_per_da = params.get('shift_hours', 10) - params.get('break_hours', 1)
            working_days = params.get('working_days', 6)
            da_weekly_hours = effective_hours_per_da * working_days
            
            gap_hours = abs(total_gap)  # total_gap is negative, so abs()
            excess_hours = total_excess  # positive
            
            # DAs needed = gap hours / weekly productive hours per DA
            # Even if net is positive (more rostered than required overall),
            # gap still exists at specific hours. DAs needed reflects the gap.
            das_needed = max(0, int(np.ceil(gap_hours / (da_weekly_hours * 0.85)))) if gap_hours > 0 else 0
            # Excess DAs = excess hours / weekly hours per DA
            excess_das = int(excess_hours / da_weekly_hours) if excess_hours > 0 else 0
            
            # Calculate DA utilization (with transfer adjustments)
            transfers = load_transfers()
            adjusted_store_das = apply_transfer_adjustments(das_df, transfers)
            store_das = adjusted_store_das[adjusted_store_das['Store'] == selected_store]
            total_das = store_das['DA_Count'].sum() if not store_das.empty else 0
            
            # Show transfer delta if any
            original_das = das_df[das_df['Store'] == selected_store]['DA_Count'].sum() if not das_df[das_df['Store'] == selected_store].empty else 0
            da_transfer_delta = int(total_das - original_das)
            total_da_hours_available = total_das * da_weekly_hours
            utilized_hours = sum(min(row['Rostered'], row['Required']) for _, row in roster_df.iterrows())
            # Utilization = hours where DAs are productively covering demand / total rostered hours
            # This shows what % of rostered DA-hours are actually useful (not excess)
            utilization_pct = (utilized_hours / total_rostered * 100) if total_rostered > 0 else 0
            
            # Summary metrics section
            st.subheader("📊 Coverage Summary")
            
            # Row 1: Basic metrics
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Required", f"{total_required:,.0f}")
            col2.metric("Rostered", f"{total_rostered:,.0f}")
            col3.metric("Gap", f"{total_gap:,.0f}", delta_color="inverse")
            col4.metric("Excess", f"{total_excess:,.0f}")
            col5.metric("Priority-Weighted Gap", f"{priority_gap:,.0f}")
            
            # Row 2: Advanced metrics
            st.markdown("---")
            adv_col1, adv_col2, adv_col3, adv_col4, adv_col5 = st.columns(5)
            
            with adv_col1:
                st.metric("🚨 DAs Needed", f"+{das_needed}" if das_needed > 0 else "0")
            with adv_col2:
                st.metric("📤 Excess DAs", f"-{excess_das}" if excess_das > 0 else "0")
            with adv_col3:
                st.metric("📈 Utilization", f"{utilization_pct:.1f}%")
            with adv_col4:
                st.metric("👥 Total DAs", f"{int(total_das)}", 
                         delta=f"{da_transfer_delta:+d} transferred" if da_transfer_delta != 0 else None)
            with adv_col5:
                coverage_pct = (total_rostered / total_required * 100) if total_required > 0 else 0
                st.metric("📊 Coverage", f"{coverage_pct:.1f}%")

            # Flex DA impact metrics (only shown when store has Flex DAs assigned)
            if store_flex_count > 0 and flex_roster_df is not None and not flex_roster_df.empty:
                flex_total = int(flex_roster_df['Flex_Rostered'].sum())
                combined_coverage = int(total_rostered) + flex_total
                remaining_gap_after_flex = max(0, int(total_required - combined_coverage))
                st.markdown("**⚡ Flex DA Impact:**")
                fx1, fx2, fx3 = st.columns(3)
                fx1.metric("Flex DA Hours Added", f"{flex_total}")
                fx2.metric("Combined Coverage (FT + Flex)", f"{combined_coverage:,}")
                fx3.metric(
                    "Remaining Gap After Flex",
                    f"{remaining_gap_after_flex}",
                    delta_color="inverse",
                )
            
            # =========================================================================
            # VIOLATION DETECTION (both engines)
            # =========================================================================
            if shifts_df is not None and not shifts_df.empty:
                # Use global params as defaults, resolve per-store below
                _sc = st.session_state.get('store_configs', {})
                _global_shift_hours = params.get('shift_hours', 10)
                _global_break_hours = params.get('break_hours', 1)
                _global_max_cont = params.get('max_continuous', 5)
                _global_min_rest = params.get('min_rest', 14)
                _global_working_days = params.get('working_days', 6)
                
                # Resolve for the selected store
                _resolved_v = resolve_params(params, selected_store, _sc)
                shift_hours_param = _resolved_v.get('shift_hours', _global_shift_hours)
                break_hours_param = _resolved_v.get('break_hours', _global_break_hours)
                max_continuous_param = _resolved_v.get('max_continuous', _global_max_cont)
                min_rest_param = _resolved_v.get('min_rest', _global_min_rest)
                working_days_param = _resolved_v.get('working_days', _global_working_days)
                
                # Show which params are being used (debug)
                _store_cfg = _sc.get(selected_store, {})
                if _store_cfg:
                    _override_parts = [f"{k}={v}" for k, v in _store_cfg.items()]
                    st.info(f"⚙️ Store overrides for {selected_store}: {', '.join(_override_parts)}")
                
                v_no_break = 0
                v_max_cont = 0
                v_rest = 0
                v_details = []
                
                for da_id in shifts_df['DA_ID'].unique():
                    da_shifts = shifts_df[shifts_df['DA_ID'] == da_id].sort_values('Day_Index')
                    prev_end = None
                    prev_day_idx = None
                    prev_was_overnight = False
                    
                    # Initialize from carryover: if this DA has W-1 Saturday data,
                    # set prev_end/prev_day_idx so the Sunday rest check catches violations.
                    _carry_data = params.get('carryover_excel_data', [])
                    if _carry_data:
                        _carry_items = _carry_data
                        if hasattr(_carry_data, 'iterrows'):
                            _carry_items = [r.to_dict() for _, r in _carry_data.iterrows()]
                        for _ci in _carry_items:
                            if _ci.get('DA_ID') == da_id and _ci.get('Store') == selected_store:
                                _sat_start = _ci.get('Sat_Shift_Start', _ci.get('Shift_Start', 0))
                                _sat_end = _ci.get('Sat_Shift_End', 0)
                                if pd.notna(_sat_start) and pd.notna(_sat_end):
                                    prev_end = int(_sat_end)
                                    prev_day_idx = 6  # Saturday
                                    prev_was_overnight = prev_end > 0 and prev_end < int(_sat_start)
                                break
                    
                    for _, shift in da_shifts.iterrows():
                        if shift['Is_Day_Off'] or pd.isna(shift.get('Shift_Start')):
                            # Don't reset — keep tracking for rest calc across off-days
                            continue
                        
                        start = int(shift['Shift_Start'])
                        end = int(shift['Shift_End']) if pd.notna(shift.get('Shift_End')) else (start + shift_hours_param) % 24
                        brk = shift.get('Break_Hour')
                        day_idx = shift['Day_Index']
                        
                        # Determine if this shift is overnight
                        is_overnight = (start + shift_hours_param) >= 24 and end != 0 and end < start
                        
                        # Check missing break
                        if pd.isna(brk) and shift_hours_param > max_continuous_param:
                            v_no_break += 1
                            v_details.append(f"No break: {da_id} on {shift['Day']}")
                        elif not pd.isna(brk):
                            brk = int(brk)
                            brk2 = shift.get('Break_Hour_2')
                            has_brk2 = not pd.isna(brk2) if brk2 is not None else False
                            
                            if has_brk2:
                                brk2 = int(brk2)
                                # Two breaks: compute 3 segments
                                # Sort breaks by position in shift
                                b1_pos = (brk - start) % 24
                                b2_pos = (brk2 - start) % 24
                                if b2_pos < b1_pos:
                                    b1_pos, b2_pos = b2_pos, b1_pos
                                seg1 = b1_pos  # before first break
                                seg2 = b2_pos - b1_pos - 1  # between breaks
                                seg3 = shift_hours_param - b2_pos - 1  # after second break
                                if seg1 > max_continuous_param:
                                    v_max_cont += 1
                                    v_details.append(f"Max continuous: {da_id} on {shift['Day']} — {seg1}h before 1st break > {max_continuous_param}h")
                                if seg2 > max_continuous_param:
                                    v_max_cont += 1
                                    v_details.append(f"Max continuous: {da_id} on {shift['Day']} — {seg2}h between breaks > {max_continuous_param}h")
                                if seg3 > max_continuous_param:
                                    v_max_cont += 1
                                    v_details.append(f"Max continuous: {da_id} on {shift['Day']} — {seg3}h after 2nd break > {max_continuous_param}h")
                            else:
                                # Single break: compute 2 segments
                                hours_before = (brk - start) % 24
                                if hours_before > shift_hours_param:
                                    hours_before = shift_hours_param
                                hours_after = shift_hours_param - hours_before - break_hours_param
                                if hours_before > max_continuous_param:
                                    v_max_cont += 1
                                    v_details.append(f"Max continuous: {da_id} on {shift['Day']} — {hours_before}h before break > {max_continuous_param}h")
                                if hours_after > max_continuous_param:
                                    v_max_cont += 1
                                    v_details.append(f"Max continuous: {da_id} on {shift['Day']} — {hours_after}h after break > {max_continuous_param}h")
                        
                        # Check rest using same logic as engine's _calc_rest
                        if prev_end is not None and prev_day_idx is not None:
                            # Determine which day the previous shift ended on
                            if prev_was_overnight:
                                end_day = (prev_day_idx + 1) % 7
                            else:
                                end_day = prev_day_idx
                            
                            if prev_end == 0:
                                end_day = (end_day + 1) % 7
                                effective_prev_end = 0
                            else:
                                effective_prev_end = prev_end
                            
                            day_gap = (day_idx - end_day) % 7
                            
                            if day_gap == 0:
                                rest = start - effective_prev_end
                            elif day_gap == 1:
                                rest = (24 - effective_prev_end) + start
                            else:
                                rest = (24 - effective_prev_end) + (day_gap - 1) * 24 + start
                            
                            if rest < min_rest_param:
                                v_rest += 1
                                v_details.append(f"Rest: {da_id} — {rest}h between {DAYS[prev_day_idx]} and {shift['Day']} < {min_rest_param}h")
                        
                        prev_end = end
                        prev_day_idx = day_idx
                        prev_was_overnight = is_overnight
                
                total_violations = v_no_break + v_max_cont + v_rest
                
                # Check zero-coverage slots (demand > 0 but 0 DAs rostered)
                v_zero_cov = 0
                zero_cov_details = []
                if roster_df is not None and not roster_df.empty:
                    for _, row in roster_df.iterrows():
                        req = row.get('Required', 0)
                        rostered = row.get('Rostered', 0)
                        if req > 0 and rostered == 0:
                            v_zero_cov += 1
                            zero_cov_details.append(f"Zero coverage: {row['Day']} slot {int(row['Slot']):02d}:00 — demand={int(req)}, rostered=0")
                
                total_violations += v_zero_cov
                
                if total_violations > 0:
                    parts = []
                    if v_no_break: parts.append(f"{v_no_break} missing breaks")
                    if v_max_cont: parts.append(f"{v_max_cont} max continuous")
                    if v_rest: parts.append(f"{v_rest} rest violations")
                    if v_zero_cov: parts.append(f"{v_zero_cov} zero-coverage slots")
                    st.error(f"⚠️ {total_violations} violations: {', '.join(parts)}")
                    
                    with st.expander("🔍 View Violation Details", expanded=False):
                        for d in (v_details + zero_cov_details)[:30]:
                            st.write(f"- {d}")
                        total_details = len(v_details) + len(zero_cov_details)
                        if total_details > 30:
                            st.write(f"... and {total_details - 30} more")
                        st.info("💡 Reset the roster and regenerate, or adjust working rules.")
                else:
                    st.success("✅ No violations detected")
            
            # =========================================================================
            # DAILY BREAKDOWN: DA Count + Gap Hours per day
            # =========================================================================
            if roster_df is not None and not roster_df.empty and shifts_df is not None:
                st.markdown("---")
                st.markdown("**📅 Daily Breakdown**")
                
                day_order = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
                daily_data = []
                
                for day in day_order:
                    # DA count: unique DAs working on this day (not on day off)
                    day_shifts = shifts_df[shifts_df['Day'] == day]
                    das_working = len(day_shifts[~day_shifts['Is_Day_Off']]['DA_ID'].unique()) if not day_shifts.empty else 0
                    das_off = len(day_shifts[day_shifts['Is_Day_Off']]['DA_ID'].unique()) if not day_shifts.empty else 0
                    
                    # Gap hours from roster
                    day_roster = roster_df[roster_df['Day'] == day]
                    day_gap = abs(day_roster[day_roster['Diff'] < 0]['Diff'].sum()) if not day_roster.empty else 0
                    day_excess = day_roster[day_roster['Diff'] > 0]['Diff'].sum() if not day_roster.empty else 0
                    day_demand = day_roster['Required'].sum() if not day_roster.empty else 0
                    
                    daily_data.append({
                        'Day': day,
                        'DAs Working': das_working,
                        'DAs Off': das_off,
                        'Demand (hrs)': int(day_demand),
                        'Gap (hrs)': int(day_gap),
                        'Excess (hrs)': int(day_excess),
                    })
                
                daily_df = pd.DataFrame(daily_data)
                
                # Display as columns for quick view
                day_cols = st.columns(7)
                for i, day in enumerate(day_order):
                    row = daily_data[i]
                    with day_cols[i]:
                        st.markdown(f"**{day}**")
                        st.metric("DAs", row['DAs Working'], delta=f"-{row['DAs Off']} off" if row['DAs Off'] > 0 else None, delta_color="off")
                        gap_val = row['Gap (hrs)']
                        st.metric("Gap", f"{gap_val}", delta_color="inverse")
                
                # Expandable table with full details
                with st.expander("📋 Full Daily Table"):
                    st.dataframe(daily_df, use_container_width=True, hide_index=True)
                
                # Shift Starts Summary (for flexible/proportional/demand_driven engines)
                if current_engine in ('flexible', 'proportional', 'demand_driven', 'demand_driven_ultimate'):
                    working_shifts = shifts_df[~shifts_df['Is_Day_Off'] & shifts_df['Shift_Start'].notna()]
                    if not working_shifts.empty:
                        # Count unique start times and DAs per start
                        da_starts = working_shifts.groupby('DA_ID')['Shift_Start'].agg(lambda x: x.mode().iloc[0] if len(x) > 0 else None).reset_index()
                        da_starts.columns = ['DA_ID', 'Primary_Start']
                        start_counts = da_starts['Primary_Start'].value_counts().sort_index()
                        
                        n_shifts = len(start_counts)
                        total_das = len(da_starts)
                        
                        with st.expander(f"🕐 Shift Starts: {n_shifts} unique shifts across {total_das} DAs", expanded=False):
                            # Build a clean table
                            shift_rows = []
                            for start_hr, count in start_counts.items():
                                start_int = int(start_hr)
                                end_int = (start_int + params.get('shift_hours', 10)) % 24
                                shift_rows.append({
                                    'Shift': f"{start_int:02d}:00 → {end_int:02d}:00",
                                    'Start': f"{start_int:02d}:00",
                                    'End': f"{end_int:02d}:00",
                                    'DAs': int(count),
                                    '% of Total': f"{count/total_das*100:.1f}%"
                                })
                            shift_starts_df = pd.DataFrame(shift_rows)
                            st.dataframe(shift_starts_df, use_container_width=True, hide_index=True)
                        
                        # DA Schedule Report: show each DA's primary start, off day, and any post-off change
                        # Build per-DA schedule info
                        da_schedule_rows = []
                        das_with_change = 0
                        for da_id in sorted(working_shifts['DA_ID'].unique()):
                            da_shifts = working_shifts[working_shifts['DA_ID'] == da_id]
                            da_off_row = shifts_df[(shifts_df['DA_ID'] == da_id) & (shifts_df['Is_Day_Off'])]
                            off_day = da_off_row['Day'].iloc[0] if not da_off_row.empty else '-'
                            
                            # Get starts per day
                            day_starts = {}
                            for _, row in da_shifts.iterrows():
                                day_starts[row['Day']] = int(row['Shift_Start'])
                            
                            unique_starts = set(day_starts.values())
                            primary_start = da_starts[da_starts['DA_ID'] == da_id]['Primary_Start'].iloc[0] if da_id in da_starts['DA_ID'].values else None
                            
                            if primary_start is not None:
                                primary_int = int(primary_start)
                                primary_end = (primary_int + params.get('shift_hours', 10)) % 24
                                primary_str = f"{primary_int:02d}:00→{primary_end:02d}:00"
                            else:
                                primary_str = '-'
                                primary_int = None
                            
                            # Find the changed day (if any)
                            changed_day = '-'
                            changed_shift = '-'
                            if len(unique_starts) > 1 and primary_int is not None:
                                das_with_change += 1
                                for day, st_hr in day_starts.items():
                                    if st_hr != primary_int:
                                        end_hr = (st_hr + params.get('shift_hours', 10)) % 24
                                        changed_day = day
                                        changed_shift = f"{st_hr:02d}:00→{end_hr:02d}:00"
                                        break
                            
                            # Get DSP if available
                            dsp = '-'
                            if 'DSP' in shifts_df.columns:
                                dsp_vals = shifts_df[shifts_df['DA_ID'] == da_id]['DSP']
                                if not dsp_vals.empty:
                                    dsp = str(dsp_vals.iloc[0])
                            
                            da_schedule_rows.append({
                                'DA_ID': da_id,
                                'DSP': dsp,
                                'Primary Shift': primary_str,
                                'Off Day': off_day,
                                'Changed Day': changed_day,
                                'Changed Shift': changed_shift,
                            })
                        
                        # Show summary metric + expandable table
                        if das_with_change > 0:
                            st.info(f"📋 {das_with_change} DA(s) have a different shift on their post-off day")
                        
                        with st.expander(f"👥 DA Schedule Report ({total_das} DAs)", expanded=False):
                            da_report_df = pd.DataFrame(da_schedule_rows)
                            st.dataframe(da_report_df, use_container_width=True, hide_index=True)
                
                # Find Optimal Shift Count button (demand_driven engine only)
                if current_engine == 'demand_driven':
                    if st.button("🔍 Find Optimal Shift Count", key="dd_find_optimal",
                                 help="Sweeps 1-24 shifts to find the minimum number with near-optimal gap"):
                        with st.spinner("Searching for optimal shift count (running 24 engine passes)..."):
                            from roster_engine_v12_4_original import find_optimal_shifts as v14_find_optimal
                            from roster_engine_v12_4_original import build_demand_matrix as v14_bdm
                            store_demand_opt = demand_df[demand_df['Store'] == selected_store]
                            demand_matrix = v14_bdm(store_demand_opt)
                            transfers = load_transfers()
                            adjusted = apply_transfer_adjustments(das_df, transfers)
                            store_das_opt = adjusted[adjusted['Store'] == selected_store]
                            da_list_opt = v14_build_da_list(store_das_opt)
                            n_das = len(da_list_opt)
                            search_params = v14_get_params({
                                'night_shift_enabled': params.get('night_shift', True),
                                'flexible_day_off': params.get('flexible_day_off', False),
                                'shift_hours': params.get('shift_hours', 10),
                                'break_hours': params.get('break_hours', 1),
                                'max_continuous': params.get('max_continuous', 5),
                                'min_rest': params.get('min_rest', 12),
                                'working_days': params.get('working_days', 6),
                                'fixed_start_optimizer': params.get('fixed_start_optimizer', 'post_off'),
                                'max_shifts': params.get('max_shifts', 0),
                            })
                            result = v14_find_optimal(demand_matrix, n_das, search_params)
                            
                            st.success(f"✅ Optimal: {result['optimal_n']} shifts (gap={result['optimal_gap']}) — "
                                      f"Best possible: {result['best_n']} shifts (gap={result['best_gap']})")
                            
                            sweep_data = [{'Shifts': e['n'], 'Gap': e['gap'], 'Zero Slots': e['zeros']}
                                         for e in result['sweep']]
                            st.dataframe(pd.DataFrame(sweep_data), use_container_width=True, hide_index=True)
            
            # =========================================================================
            # FIXED SHIFTS: Shift Distribution Controls (only show for fixed engine)
            # =========================================================================
            if current_engine == 'fixed' and shifts_df is not None and not shifts_df.empty:
                # Get current distribution - pass engine_params to include custom shifts
                shift_summary = get_shift_distribution_summary(shifts_df, engine_params)
                
                # Only show if we have valid shift data
                if shift_summary:
                    st.markdown("---")
                    st.subheader("📌 Fixed Shift Distribution")
                    
                    # Show current distribution - dynamic columns based on shift count
                    num_shifts = len(shift_summary)
                    num_cols = min(num_shifts, 8)  # Max 8 columns for readability
                    dist_cols = st.columns(num_cols) if num_cols > 0 else [st.container()]
                    
                    for i, (shift_id, info) in enumerate(sorted(shift_summary.items())):
                        with dist_cols[i % num_cols]:
                            st.metric(
                                f"Shift {shift_id}",
                                f"{info['da_count']} DAs",
                                help=f"{info['name']} - starts at {info['start']:02d}:00"
                            )
                    
                    # Shift share adjustment expander
                    with st.expander("🎚️ Adjust Shift Shares", expanded=False):
                        st.markdown("*Adjust the percentage of DAs assigned to each shift. Total must equal 100%.*")
                        
                        shift_shares_key = f'shift_shares_{selected_store}'
                        
                        # Get custom shifts (includes any added shifts)
                        custom_shifts = st.session_state.get('custom_fixed_shifts', FIXED_SHIFTS.copy())
                        num_shifts = len(custom_shifts)
                        default_share = 100 / num_shifts if num_shifts > 0 else 16.7
                        
                        # Initialize shift shares if not set or if shift count changed
                        current_shares = st.session_state.get(shift_shares_key, {})
                        if not current_shares or set(current_shares.keys()) != set(custom_shifts.keys()):
                            # Calculate current percentages from shift_summary or use equal distribution
                            total_in_shifts = sum(info['da_count'] for info in shift_summary.values()) if shift_summary else 0
                            if total_in_shifts > 0:
                                new_shares_init = {
                                    shift_id: info['da_count'] / total_in_shifts * 100
                                    for shift_id, info in shift_summary.items()
                                    if shift_id in custom_shifts
                                }
                                # Add any new shifts - give them equal share by redistributing
                                missing_shifts = [sid for sid in custom_shifts if sid not in new_shares_init]
                                if missing_shifts:
                                    # Redistribute: give new shifts equal portion, scale down existing
                                    num_existing = len(new_shares_init)
                                    num_new = len(missing_shifts)
                                    total_shifts = num_existing + num_new
                                    # Each shift gets 100/total_shifts, scale existing proportionally
                                    scale_factor = num_existing / total_shifts if num_existing > 0 else 0
                                    for sid in new_shares_init:
                                        new_shares_init[sid] *= scale_factor
                                    new_share_per_new = 100 / total_shifts
                                    for sid in missing_shifts:
                                        new_shares_init[sid] = new_share_per_new
                                # Normalize to exactly 100%
                                total_pct = sum(new_shares_init.values())
                                if total_pct > 0:
                                    st.session_state[shift_shares_key] = {
                                        sid: round(pct / total_pct * 100, 1) 
                                        for sid, pct in new_shares_init.items()
                                    }
                                else:
                                    st.session_state[shift_shares_key] = {shift_id: default_share for shift_id in custom_shifts}
                            else:
                                st.session_state[shift_shares_key] = {shift_id: default_share for shift_id in custom_shifts}
                        
                        # Sliders for each shift (use custom_shifts, not FIXED_SHIFTS)
                        new_shares = {}
                        num_cols = min(3, num_shifts)
                        share_cols = st.columns(num_cols) if num_cols > 0 else [st.container()]
                        
                        for i, (shift_id, info) in enumerate(sorted(custom_shifts.items())):
                            with share_cols[i % num_cols]:
                                current_share = st.session_state[shift_shares_key].get(shift_id, default_share)
                                new_shares[shift_id] = st.slider(
                                    f"Shift {shift_id}: {info['name']}",
                                    min_value=0.0,
                                    max_value=100.0,
                                    value=float(current_share),
                                    step=1.0,
                                    key=f"share_slider_{selected_store}_{shift_id}"
                                )
                        
                        # Show total and warning if not 100%
                        total_share = sum(new_shares.values())
                        
                        share_status_col1, share_status_col2 = st.columns([1, 2])
                        with share_status_col1:
                            if abs(total_share - 100) < 0.1:
                                st.success(f"✅ Total: {total_share:.1f}%")
                            else:
                                st.warning(f"⚠️ Total: {total_share:.1f}% (should be 100%)")
                        
                        with share_status_col2:
                            if st.button("🔄 Apply New Shares", key="apply_shares"):
                                if abs(total_share - 100) < 5:  # Allow small deviation
                                    save_state_for_undo()
                                    # Normalize to 100%
                                    normalized = {k: v / total_share * 100 for k, v in new_shares.items()}
                                    st.session_state[shift_shares_key] = normalized
                                    # Clear optimized shifts to regenerate
                                    if optimized_key in st.session_state:
                                        del st.session_state[optimized_key]
                                    st.success("✅ Shares updated! Regenerating roster...")
                                    st.rerun()
                                else:
                                    st.error("Total must be close to 100%")
                            
                            if st.button("↩️ Reset to Auto", key="reset_shares"):
                                save_state_for_undo()
                                if shift_shares_key in st.session_state:
                                    del st.session_state[shift_shares_key]
                                if optimized_key in st.session_state:
                                    del st.session_state[optimized_key]
                                st.success("✅ Reset to automatic distribution")
                                st.rerun()
            
            # Visualizations
            st.subheader("📈 Coverage Visualization")
            
            viz_tab1, viz_tab2, viz_tab3 = st.tabs(["🗓️ Heatmap Dashboard", "📊 Hourly Comparison", "📋 Detailed Data"])
            
            with viz_tab1:
                heatmap_fig = create_coverage_heatmap(roster_df, st.session_state.store_priorities[selected_store])
                if heatmap_fig:
                    st.plotly_chart(heatmap_fig, use_container_width=True)
            
            with viz_tab2:
                # Day selector for hourly comparison
                day_options = ['All Week', 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
                selected_day = st.selectbox(
                    "Select Day",
                    options=day_options,
                    index=0,
                    key=f"hourly_day_selector_{selected_store}"
                )
                
                hourly_fig = create_hourly_comparison_chart(
                    roster_df,
                    st.session_state.store_priorities[selected_store],
                    selected_day,
                    flex_roster_df=flex_roster_df,
                )
                if hourly_fig:
                    st.plotly_chart(hourly_fig, use_container_width=True)
                else:
                    st.warning(f"No data available for {selected_day}")
                
                # Show daily summary metrics
                if selected_day != 'All Week':
                    day_map = {'Sunday': 'Sun', 'Monday': 'Mon', 'Tuesday': 'Tue', 
                               'Wednesday': 'Wed', 'Thursday': 'Thu', 'Friday': 'Fri', 'Saturday': 'Sat'}
                    short_day = day_map.get(selected_day, selected_day[:3])
                    day_data = roster_df[roster_df['Day'].str[:3] == short_day]
                    if not day_data.empty:
                        col1, col2, col3, col4 = st.columns(4)
                        col1.metric("Required", f"{int(day_data['Required'].sum()):,}")
                        col2.metric("Rostered", f"{int(day_data['Rostered'].sum()):,}")
                        col3.metric("Gap", f"{int(day_data[day_data['Diff'] < 0]['Diff'].sum()):,}")
                        if 'Orders' in day_data.columns:
                            col4.metric("Orders", f"{int(day_data['Orders'].sum()):,}")
                
                # Gap by priority level
                st.markdown("#### Gap Analysis by Priority Level")
                high_priority_gap = sum(
                    roster_df[(roster_df['Slot'] == h) & (roster_df['Diff'] < 0)]['Diff'].sum()
                    for h in range(24) if st.session_state.store_priorities[selected_store].get(h, 5) >= 8
                )
                medium_priority_gap = sum(
                    roster_df[(roster_df['Slot'] == h) & (roster_df['Diff'] < 0)]['Diff'].sum()
                    for h in range(24) if 4 <= st.session_state.store_priorities[selected_store].get(h, 5) < 8
                )
                low_priority_gap = sum(
                    roster_df[(roster_df['Slot'] == h) & (roster_df['Diff'] < 0)]['Diff'].sum()
                    for h in range(24) if st.session_state.store_priorities[selected_store].get(h, 5) < 4
                )
                
                gap_col1, gap_col2, gap_col3 = st.columns(3)
                gap_col1.metric("🔴 High Priority Gap", f"{high_priority_gap:,.0f}")
                gap_col2.metric("🟠 Medium Priority Gap", f"{medium_priority_gap:,.0f}")
                gap_col3.metric("🟢 Low Priority Gap", f"{low_priority_gap:,.0f}")
            
            with viz_tab3:
                st.dataframe(roster_df, use_container_width=True, height=400)
            
            # Download section
            st.markdown("---")
            
            # Save & Reset buttons
            save_reset_col1, save_reset_col2, save_reset_col3 = st.columns([2, 2, 1])
            
            with save_reset_col1:
                save_name = st.text_input("Save name", value=f"{selected_store} roster", 
                                         key="manual_save_name", label_visibility="collapsed",
                                         placeholder="Enter a name for this save...")
            
            with save_reset_col2:
                current_shifts_to_save = st.session_state.get(f'optimized_shifts_{selected_store}', shifts_df)
                has_shifts = current_shifts_to_save is not None and not current_shifts_to_save.empty
                
                if st.button("💾 Save Roster", disabled=not has_shifts, key="manual_save_btn", type="primary"):
                    # Calculate current gap
                    store_demand_save = demand_df[demand_df['Store'] == selected_store]
                    if current_engine == 'fixed':
                        save_engine_params = fixed_get_params({
                            'shift_hours': params.get('shift_hours', 10),
                            'break_hours': params.get('break_hours', 1),
                            'max_continuous': params.get('max_continuous', 5),
                            'min_rest': params.get('min_rest', 12),
                            'working_days': params.get('working_days', 6),
                            'custom_shifts': st.session_state.get('custom_fixed_shifts', FIXED_SHIFTS.copy())
                        })
                        save_roster = fixed_generate_hourly_roster(current_shifts_to_save, store_demand_save, save_engine_params)
                    else:
                        save_engine_params = engine_get_params(params)
                        save_roster = engine_generate_hourly_roster(current_shifts_to_save, store_demand_save, save_engine_params)
                    
                    gap = abs(save_roster[save_roster['Diff'] < 0]['Diff'].sum()) if save_roster is not None else 0
                    
                    # Save as checkpoint (persists to disk)
                    if save_checkpoint(selected_week, selected_store, current_shifts_to_save, save_name, int(gap)):
                        # Also save to history
                        save_to_persistent_history(selected_week, selected_store, current_shifts_to_save,
                                                  f"Manual save: {save_name}", int(gap))
                        st.success(f"💾 Saved '{save_name}' (Gap: {int(gap)})")
                        st.rerun()
            
            with save_reset_col3:
                optimized_key = f'optimized_shifts_{selected_store}'
                if optimized_key in st.session_state and st.session_state[optimized_key] is not None:
                    if st.button("🔄 Reset", key="reset_opts_top", help="Clear optimized shifts and regenerate from scratch"):
                        del st.session_state[optimized_key]
                        st.rerun()
            

            download_shifts = st.session_state.get(f'optimized_shifts_{selected_store}', shifts_df)
            
            # =================================================================
            # Week Continuity Report
            # =================================================================
            _prev_data = st.session_state.get('prev_week_data')
            _wk_cont = st.session_state.get('week_continuity')
            if _prev_data and _prev_data.get('total_overnight_das', 0) > 0:
                st.markdown("---")
                st.subheader("🔗 Week Continuity Report")
                _prev_wk_lbl = _prev_data.get('week_detected') or 'Previous week'
                _cur_wk_lbl = selected_week or 'Current'
                st.caption(f"{_prev_wk_lbl} → {_cur_wk_lbl}")

                _report_df = build_continuity_report_df(_prev_data, _wk_cont)
                if not _report_df.empty:
                    total_overnight = int(_report_df['Overnight_DAs'].sum())
                    total_connected = int(_report_df['Connected'].sum())
                    total_violations = int(_report_df['Violations'].sum())
                    total_da_hours = sum(
                        sum(s.get('coverage_hours', {}).values())
                        for s in _prev_data.get('stores', {}).values()
                    )
                    _strategy = (_wk_cont or {}).get('strategy', 'full_continuity')

                    render_kpi_grid([
                        {'title': 'Overnight DAs', 'value': total_overnight,
                         'color': 'blue', 'icon': '🌙',
                         'caption': f"from {_prev_wk_lbl}"},
                        {'title': 'Connected', 'value': total_connected,
                         'color': 'green', 'icon': '🔗'},
                        {'title': 'Violations', 'value': total_violations,
                         'color': 'red' if total_violations else 'green',
                         'icon': '⚠️' if total_violations else '✅'},
                        {'title': 'DA-Hours Boost', 'value': int(total_da_hours),
                         'color': 'orange', 'icon': '📈',
                         'caption': 'Sunday coverage added'},
                        {'title': 'Strategy', 'value': _strategy.replace('_', ' ').title(),
                         'color': 'purple', 'icon': '🎯'},
                    ])
                    st.dataframe(_report_df, use_container_width=True, hide_index=True)

                    if total_violations > 0:
                        st.warning(
                            f"⚠️ {total_violations} DA(s) require manual review — "
                            "their minimum Sunday start is later than 22:00."
                        )
                    else:
                        st.success("✅ All overnight DAs have valid Sunday rest windows.")

            st.markdown("---")
            st.subheader("📥 Download Options")

            # Initialize DSP Mix defaults so they are always defined regardless
            # of which expander the user opens.
            enable_dsp_mix = False
            min_dsps_per_slot = 2

            # LM Cap Generation
            with st.expander("📊 Generate LM Cap", expanded=False):
                st.markdown("*Generate Last Mile Capacity report: Rostered DAs × DPH = Max Orders per slot*")
                
                lm_col1, lm_col2 = st.columns([1, 1])
                with lm_col1:
                    lm_scope = st.radio("Scope", ['selected', 'all'], 
                                       format_func=lambda x: f"📍 {selected_store}" if x == 'selected' else "🌐 All Stores",
                                       horizontal=True, key="lm_scope")
                with lm_col2:
                    custom_dph = st.number_input("Override DPH (0 = use file)", min_value=0.0, max_value=10.0, 
                                                value=0.0, step=0.1, key="custom_dph",
                                                help="Set to 0 to use DPH from the uploaded file")
                
                absenteeism_pct = st.slider("📉 Absenteeism Factor (%)", min_value=0, max_value=50, value=0, step=1,
                                            key="lm_absenteeism",
                                            help="Reduce rostered DAs by this percentage to account for absenteeism. E.g., 15% means only 85% of rostered DAs are available.")
                
                if st.button("📊 Generate LM Cap", key="gen_lm_cap"):
                    with st.spinner("Generating LM Cap..."):
                        lm_stores = [selected_store] if lm_scope == 'selected' else None
                        lm_cap_df = generate_lm_cap(demand_df, das_df, params, uploaded_file, selected_week, stores=lm_stores)
                        
                        if lm_cap_df is not None and not lm_cap_df.empty:
                            days_order = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
                            
                            # Apply absenteeism factor to rostered DAs
                            if absenteeism_pct > 0:
                                abs_factor = 1 - (absenteeism_pct / 100)
                                for day in days_order:
                                    lm_cap_df[day] = (lm_cap_df[day] * abs_factor).apply(lambda x: max(0, int(round(x))))
                            
                            # Apply custom DPH override if set
                            if custom_dph > 0:
                                lm_cap_df['Input DPH'] = custom_dph
                                for day in days_order:
                                    lm_cap_df[f'{day}_Orders'] = (lm_cap_df[day] * custom_dph).round().astype(int)
                            elif absenteeism_pct > 0:
                                # Recalculate orders with reduced DAs using existing DPH
                                for day in days_order:
                                    lm_cap_df[f'{day}_Orders'] = (lm_cap_df[day] * lm_cap_df['Input DPH']).round().astype(int)
                            
                            st.session_state['lm_cap_df'] = lm_cap_df
                            st.success(f"✅ LM Cap generated for {lm_cap_df['Store'].nunique()} stores")
                        else:
                            st.warning("No roster data available. Generate rosters first.")
                
                # Show preview and download if available
                if 'lm_cap_df' in st.session_state:
                    lm_cap_df = st.session_state['lm_cap_df']
                    
                    st.dataframe(lm_cap_df.head(48), use_container_width=True, height=300)
                    
                    # Download as Excel
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        lm_cap_df.to_excel(writer, sheet_name='LM Cap', index=False)
                    output.seek(0)
                    
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    st.download_button(
                        label="📥 Download LM Cap",
                        data=output,
                        file_name=f"LM_Cap_{selected_week}_{timestamp}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
            
            # DSP Mix Options — opt-in sheets in downloaded Excel
            with st.expander("🔀 DSP Mix Options", expanded=False):
                st.markdown(
                    "*Include a DSP coverage breakdown per hour slot in the "
                    "downloaded file. Flags slots covered by only one DSP as a "
                    "resilience risk.*"
                )
                enable_dsp_mix = st.checkbox(
                    "🔀 Include DSP Mix Report in download",
                    value=False,
                    key="enable_dsp_mix",
                    help="Adds DSP_Mix_Report and DSP_Slot_Matrix sheets to the Excel download",
                )
                if enable_dsp_mix:
                    min_dsps_per_slot = st.number_input(
                        "Minimum DSPs per slot (target)",
                        min_value=1, max_value=5, value=2,
                        key="min_dsps_per_slot",
                        help="Slots with fewer than this many DSPs will be flagged as risk",
                    )
                else:
                    min_dsps_per_slot = 2

            # Download mode selection
            download_mode = st.radio(
                "Download Scope",
                options=['selected', 'all'],
                format_func=lambda x: f"📍 Selected Store ({selected_store})" if x == 'selected' else "🌐 All Stores",
                horizontal=True,
                key="download_mode"
            )
            
            if download_mode == 'all':
                # Check if all stores have been generated
                all_stores_ready = all(f'optimized_shifts_{s}' in st.session_state or f'shifts_{s}' in st.session_state for s in stores)
                if not all_stores_ready:
                    if st.button("🚀 Generate All Stores Roster", type="primary", key="run_all_stores"):
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        
                        total_stores = len(stores)
                        _report_rows = []
                        
                        for i, store in enumerate(stores):
                            progress_bar.progress((i + 1) / total_stores)
                            status_text.text(f"Processing {i + 1}/{total_stores}: {store}")
                            
                            try:
                                store_das = das_df[das_df['Store'] == store].copy()
                                
                                # Apply transfers
                                transfers = load_transfers()
                                store_adjustment = get_store_da_adjustment(store, transfers)
                                if store_adjustment != 0 and not store_das.empty:
                                    store_das.iloc[0, store_das.columns.get_loc('DA_Count')] += store_adjustment
                                
                                if store_das.empty or store_das['DA_Count'].sum() <= 0:
                                    continue
                                
                                store_demand = demand_df[demand_df['Store'] == store]
                                
                                # Resolve store-level parameter overrides
                                _sc = st.session_state.get('store_configs', {})
                                _resolved = resolve_params(params, store, _sc)
                                
                                # Apply operating window
                                apply_operating_window(demand_df, store, _sc)
                                
                                # Warn on incompatible params
                                for _w in validate_resolved_params(_resolved, store):
                                    st.warning(_w)
                                
                                # Check engine type
                                current_engine = st.session_state.get('engine_type', 'demand_driven')
                                
                                if current_engine == 'demand_driven':
                                    engine_params = v14_get_params({
                                        'night_shift_enabled': _resolved.get('night_shift_enabled', _resolved.get('night_shift', True)),
                                        'flexible_day_off': _resolved.get('flexible_day_off', False),
                                        'skip_sunday_overnight': _resolved.get('skip_sunday_overnight', False),
                                        'shift_hours': _resolved.get('shift_hours', 10),
                                        'break_hours': _resolved.get('break_hours', 1),
                                        'max_continuous': _resolved.get('max_continuous', 5),
                                        'min_rest': _resolved.get('min_rest', 12),
                                        'working_days': _resolved.get('working_days', 6),
                                        'carryover_mode': _resolved.get('carryover_mode', 'auto'),
                                        'sunday_carryover_das': _resolved.get('sunday_carryover_das', 0),
                                        'carryover_excel_data': _resolved.get('carryover_excel_data', []),
                                        'fixed_start_optimizer': _resolved.get('fixed_start_optimizer', 'post_off'),
                                        'max_shifts': _resolved.get('max_shifts', 0),
                                    })
                                    inject_operating_window_params(engine_params, store, _sc)
                                    da_list = v14_build_da_list(store_das)
                                    store_shifts = v14_assign_shifts(da_list, store_demand, None, engine_params)
                                else:
                                    # Fallback to demand_driven
                                    engine_params = v14_get_params({
                                        'night_shift_enabled': _resolved.get('night_shift_enabled', _resolved.get('night_shift', True)),
                                        'flexible_day_off': _resolved.get('flexible_day_off', False),
                                        'skip_sunday_overnight': _resolved.get('skip_sunday_overnight', False),
                                        'shift_hours': _resolved.get('shift_hours', 10),
                                        'break_hours': _resolved.get('break_hours', 1),
                                        'max_continuous': _resolved.get('max_continuous', 5),
                                        'min_rest': _resolved.get('min_rest', 12),
                                        'working_days': _resolved.get('working_days', 6),
                                        'carryover_mode': _resolved.get('carryover_mode', 'auto'),
                                        'sunday_carryover_das': _resolved.get('sunday_carryover_das', 0),
                                        'carryover_excel_data': _resolved.get('carryover_excel_data', []),
                                        'fixed_start_optimizer': _resolved.get('fixed_start_optimizer', 'post_off'),
                                        'max_shifts': _resolved.get('max_shifts', 0),
                                    })
                                    inject_operating_window_params(engine_params, store, _sc)
                                    da_list = v14_build_da_list(store_das)
                                    store_shifts = v14_assign_shifts(da_list, store_demand, None, engine_params)
                                
                                st.session_state[f'optimized_shifts_{store}'] = store_shifts
                                
                                # Build report row
                                _rdf = v14_generate_hourly_roster(store_shifts, store_demand, engine_params)
                                _n_das = store_shifts['DA_ID'].nunique()
                                _demand_total = int(_rdf['Required'].sum())
                                _rostered_total = int(_rdf['Rostered'].sum())
                                _gap = int(abs(_rdf[_rdf['Diff'] < 0]['Diff'].sum()))
                                _excess = int(_rdf[_rdf['Diff'] > 0]['Diff'].sum())
                                _util = round(_demand_total / _rostered_total * 100, 1) if _rostered_total > 0 else 0
                                _day_gaps = {}
                                for _day in DAYS:
                                    _dr = _rdf[_rdf['Day'] == _day]
                                    _day_gaps[_day] = int(abs(_dr[_dr['Diff'] < 0]['Diff'].sum()))
                                _worst_day = max(_day_gaps, key=_day_gaps.get)
                                # Violations
                                _is_valid, _violations = validate_sacred_rules(store_shifts, engine_params, store_configs=_sc)
                                _rest_v = sum(1 for v in _violations if 'rest' in v.lower())
                                _working_v = sum(1 for v in _violations if 'works' in v.lower() and 'days' in v.lower())
                                _overnight_v = sum(1 for v in _violations if 'overnight' in v.lower() or 'night shift' in v.lower())
                                # Zero coverage slots
                                _zero_cov = len(_rdf[(_rdf['Required'] > 0) & (_rdf['Rostered'] == 0)])
                                _report_rows.append({
                                    'Store': store, 'DAs': _n_das,
                                    'Demand': _demand_total, 'Rostered': _rostered_total,
                                    'Gap': _gap, 'Excess': _excess,
                                    'Utilization %': _util,
                                    'Worst Day': f"{_worst_day} ({_day_gaps[_worst_day]})",
                                    'Zero Cov Slots': _zero_cov,
                                    'Rest Violations': _rest_v,
                                    'Working Day Violations': _working_v,
                                    'Overnight Violations': _overnight_v,
                                })
                                
                            except Exception as e:
                                st.warning(f"Error processing {store}: {e}")
                                continue
                        
                        progress_bar.empty()
                        status_text.empty()
                        if _report_rows:
                            st.session_state['network_report'] = pd.DataFrame(_report_rows)
                        st.success(f"✅ Generated roster for {total_stores} stores!")
                        st.rerun()
                
                # Network Report (shown after generation)
                if 'network_report' in st.session_state and st.session_state.get('network_report') is not None:
                    nr = st.session_state['network_report']
                    with st.expander(f"📊 Network Report — {len(nr)} stores, Gap: {int(nr['Gap'].sum())}", expanded=True):
                        st.dataframe(nr, use_container_width=True, hide_index=True)
            
            # Download buttons
            if download_mode == 'selected':
                # Single store download
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    # Get carryover DA IDs for this store
                    carryover_da_ids = set()
                    if params.get('carryover_mode') == 'excel':
                        carryover_data = params.get('carryover_excel_data', [])
                        carryover_da_ids = {c['DA_ID'] for c in carryover_data if c.get('Store') == selected_store}
                    
                    # Create a copy of roster_df to add source row
                    roster_export = roster_df.copy()
                    
                    # Identify DA columns (columns that are not standard columns)
                    standard_cols = ['Store', 'Day', 'Slot', 'Orders', 'Required', 'Rostered', 'Diff']
                    da_columns = [col for col in roster_export.columns if col not in standard_cols]
                    
                    # Create source row
                    source_row = {}
                    for col in roster_export.columns:
                        if col in standard_cols:
                            source_row[col] = 'Source'
                        elif col in carryover_da_ids:
                            source_row[col] = 'Carryover'
                        else:
                            source_row[col] = 'Current Week'
                    
                    # Insert source row at the beginning
                    source_df = pd.DataFrame([source_row])
                    roster_with_source = pd.concat([source_df, roster_export], ignore_index=True)
                    
                    roster_with_source.to_excel(writer, sheet_name='Hourly_Roster', index=False)
                    if download_shifts is not None:
                        download_shifts.to_excel(writer, sheet_name='Shift_Details', index=False)
                    priority_df = pd.DataFrame([
                        {'Hour': h, 'Priority': p} 
                        for h, p in st.session_state.store_priorities[selected_store].items()
                    ])
                    priority_df.to_excel(writer, sheet_name='Slot_Priorities', index=False)
                    
                    # Sunday Carryover Report — DAs whose Saturday overnight shift spills into Sunday
                    if download_shifts is not None:
                        sat_shifts = download_shifts[
                            (download_shifts['Day'] == 'Sat') & 
                            (~download_shifts['Is_Day_Off']) & 
                            (download_shifts['Shift_Start'].notna())
                        ].copy()
                        shift_hrs = params.get('shift_hours', 10)
                        carryover_rows = []
                        for _, row in sat_shifts.iterrows():
                            start = int(row['Shift_Start'])
                            end = int(row['Shift_End']) if pd.notna(row.get('Shift_End')) else (start + shift_hrs) % 24
                            if start + shift_hrs > 24:  # overnight — spills into Sunday
                                carryover_rows.append({
                                    'DA_ID': row['DA_ID'],
                                    'Store': row.get('Store', selected_store),
                                    'DSP': row.get('DSP', ''),
                                    'Sat_Shift_Start': f"{start:02d}:00",
                                    'Sunday_Spillover_Hours': f"00:00-{end:02d}:00",
                                    'Spillover_Duration': end if end > 0 else shift_hrs - (24 - start),
                                })
                        if carryover_rows:
                            carryover_df = pd.DataFrame(carryover_rows)
                            carryover_df.to_excel(writer, sheet_name='Sunday_Carryover', index=False)

                    # Week_Continuity sheet — previous-week DA identity map
                    _prev_data_dl = st.session_state.get('prev_week_data')
                    _wk_cont_dl = st.session_state.get('week_continuity')
                    if _prev_data_dl and _wk_cont_dl and _prev_data_dl.get('total_overnight_das', 0) > 0:
                        # Build DA_ID -> Sunday shift start map from the current shifts
                        _sun_starts = {}
                        if download_shifts is not None and not download_shifts.empty:
                            _sun_rows = download_shifts[
                                (download_shifts['Day'] == 'Sun')
                                & (~download_shifts['Is_Day_Off'])
                                & (download_shifts['Shift_Start'].notna())
                            ]
                            for _, _r in _sun_rows.iterrows():
                                _da_id = _r.get('DA_ID')
                                if _da_id:
                                    try:
                                        _sun_starts[_da_id] = int(_r['Shift_Start'])
                                    except (ValueError, TypeError):
                                        pass
                        _wc_df = build_week_continuity_sheet(
                            _prev_data_dl,
                            _wk_cont_dl.get('strategy', STRATEGY_FULL),
                            sunday_shifts_by_da=_sun_starts,
                            min_rest=int(params.get('min_rest', 12)),
                        )
                        if _wc_df is not None and not _wc_df.empty:
                            # Filter to the selected store for single-store exports
                            _wc_store = _wc_df[_wc_df['Store'] == selected_store]
                            if not _wc_store.empty:
                                _wc_store.to_excel(
                                    writer, sheet_name='Week_Continuity', index=False
                                )
                    
                    # Shift Starts Summary — distribution of shift start times
                    if download_shifts is not None:
                        working_shifts = download_shifts[
                            (~download_shifts['Is_Day_Off']) & 
                            (download_shifts['Shift_Start'].notna())
                        ]
                        if not working_shifts.empty:
                            da_starts = working_shifts.groupby('DA_ID')['Shift_Start'].agg(
                                lambda x: x.mode().iloc[0] if len(x) > 0 else None
                            ).reset_index()
                            da_starts.columns = ['DA_ID', 'Primary_Start']
                            start_counts = da_starts['Primary_Start'].value_counts().sort_index()
                            total_das = len(da_starts)
                            shift_summary_rows = []
                            for start_hr, count in start_counts.items():
                                s = int(start_hr)
                                e = (s + shift_hrs) % 24
                                shift_summary_rows.append({
                                    'Shift': f"{s:02d}:00 → {e:02d}:00",
                                    'Start': f"{s:02d}:00",
                                    'End': f"{e:02d}:00",
                                    'DAs': int(count),
                                    '% of Total': f"{count/total_das*100:.1f}%",
                                })
                            if shift_summary_rows:
                                shift_summary_df = pd.DataFrame(shift_summary_rows)
                                shift_summary_df.to_excel(writer, sheet_name='Shift_Starts_Summary', index=False)

                    # Flex (part-time) DA sheets — only present if any flex DAs were assigned
                    flex_dl = st.session_state.get(f'flex_shifts_{selected_store}')
                    if flex_dl is not None and not flex_dl.empty:
                        flex_dl.to_excel(writer, sheet_name='Flex_Shifts', index=False)
                    flex_roster_dl = st.session_state.get(f'flex_roster_{selected_store}')
                    if flex_roster_dl is not None and not flex_roster_dl.empty:
                        flex_roster_dl.to_excel(writer, sheet_name='Flex_Hourly_Roster', index=False)

                    # DSP Mix sheets — opt-in via the DSP Mix Options expander
                    if enable_dsp_mix and download_shifts is not None:
                        dsp_matrix = generate_dsp_slot_matrix(
                            download_shifts, demand_df, selected_store, params
                        )
                        if not dsp_matrix.empty:
                            dsp_matrix.to_excel(writer, sheet_name='DSP_Slot_Matrix', index=False)
                            # Build the human-readable mix report (DSP columns
                            # sit between 'Total_Rostered' and 'DSPs_Active')
                            dsp_cols = [
                                c for c in dsp_matrix.columns
                                if c not in (
                                    'Store', 'Day', 'Slot', 'Required',
                                    'Total_Rostered', 'DSPs_Active', 'Mix_Status',
                                )
                            ]
                            mix_report_rows = []
                            for _, row in dsp_matrix.iterrows():
                                covering = [str(c) for c in dsp_cols if row.get(c, 0) > 0]
                                mix_report_rows.append({
                                    'Store': row['Store'], 'Day': row['Day'],
                                    'Slot': row['Slot'],
                                    'Required': row['Required'],
                                    'Total_Rostered': row['Total_Rostered'],
                                    'DSPs_Active': row['DSPs_Active'],
                                    'Mix_Status': row['Mix_Status'],
                                    'DSPs_Covering': ', '.join(covering),
                                })
                            mix_report_df = pd.DataFrame(mix_report_rows)
                            mix_report_df.to_excel(writer, sheet_name='DSP_Mix_Report', index=False)

                    # DA Transition Map — W-1 to W+1 DA identity mapping
                    if 'da_transition_map' in st.session_state:
                        _tm = st.session_state['da_transition_map']
                        if _tm is not None and not _tm.empty:
                            _tm.to_excel(writer, sheet_name='DA_Transition_Map', index=False)
                
                output.seek(0)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                st.download_button(
                    label=f"📥 Download {selected_store} Roster",
                    data=output,
                    file_name=f"DA_Roster_{selected_week}_{selected_store}_{timestamp}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

                # DSP Mix preview (only when toggle is enabled)
                if enable_dsp_mix and download_shifts is not None:
                    dsp_preview = generate_dsp_slot_matrix(
                        download_shifts, demand_df, selected_store, params
                    )
                    if not dsp_preview.empty:
                        single_dsp_slots = len(
                            dsp_preview[dsp_preview['Mix_Status'] == '⚠️ Single DSP']
                        )
                        mixed_slots = len(
                            dsp_preview[dsp_preview['Mix_Status'] == '✅ Mixed']
                        )
                        total_active_slots = len(dsp_preview)
                        if single_dsp_slots > 0:
                            st.warning(
                                f"🔀 DSP Mix: {mixed_slots} mixed slots ✅ | "
                                f"{single_dsp_slots} single-DSP risk slots ⚠️ "
                                f"out of {total_active_slots} active slots"
                            )
                        else:
                            st.success(
                                f"🔀 DSP Mix: All {total_active_slots} active slots "
                                f"have 2+ DSPs ✅"
                            )
                        with st.expander("🔀 Preview DSP Slot Matrix"):
                            st.dataframe(
                                dsp_preview, use_container_width=True, height=300
                            )
            else:
                # All stores download — uses optimized shifts if available, generates base roster otherwise
                if st.button("📥 Prepare All Stores Download", key="prep_all_download"):
                    with st.spinner("Preparing all stores..."):
                        output = io.BytesIO()
                        with pd.ExcelWriter(output, engine='openpyxl') as writer:
                            summary_data = []
                            current_engine = st.session_state.get('engine_type', 'flexible')
                            
                            for store in stores:
                                store_shifts = st.session_state.get(f'optimized_shifts_{store}')
                                store_demand = demand_df[demand_df['Store'] == store]
                                
                                # Build engine params
                                if current_engine == 'fixed':
                                    custom_shifts = st.session_state.get('custom_fixed_shifts', FIXED_SHIFTS.copy())
                                    engine_params = fixed_get_params({
                                        'shift_hours': params.get('shift_hours', 10),
                                        'break_hours': params.get('break_hours', 1),
                                        'max_continuous': params.get('max_continuous', 5),
                                        'min_rest': params.get('min_rest', 12),
                                        'working_days': params.get('working_days', 6),
                                        'custom_shifts': custom_shifts
                                    })
                                elif current_engine == 'proportional':
                                    engine_params = v13_get_params({
                                        'night_shift_enabled': params.get('night_shift', True),
                                        'flexible_day_off': params.get('flexible_day_off', False),
                                        'shift_hours': params.get('shift_hours', 10),
                                        'break_hours': params.get('break_hours', 1),
                                        'max_continuous': params.get('max_continuous', 5),
                                        'min_rest': params.get('min_rest', 12),
                                        'working_days': params.get('working_days', 6),
                                        'carryover_mode': params.get('carryover_mode', 'auto'),
                                        'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                                        'carryover_excel_data': params.get('carryover_excel_data', []),
                                    })
                                elif current_engine == 'demand_driven':
                                    engine_params = v14_get_params({
                                        'night_shift_enabled': params.get('night_shift', True),
                                        'flexible_day_off': params.get('flexible_day_off', False),
                                        'shift_hours': params.get('shift_hours', 10),
                                        'break_hours': params.get('break_hours', 1),
                                        'max_continuous': params.get('max_continuous', 5),
                                        'min_rest': params.get('min_rest', 12),
                                        'working_days': params.get('working_days', 6),
                                        'carryover_mode': params.get('carryover_mode', 'auto'),
                                        'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                                        'carryover_excel_data': params.get('carryover_excel_data', []),
                                        'fixed_start_optimizer': params.get('fixed_start_optimizer', 'post_off'),
                                        'max_shifts': params.get('max_shifts', 0),
                                    })
                                elif current_engine == 'demand_driven_ultimate':
                                    engine_params = v14u_get_params({
                                        'night_shift_enabled': params.get('night_shift', True),
                                        'flexible_day_off': params.get('flexible_day_off', False),
                                        'shift_hours': params.get('shift_hours', 10),
                                        'break_hours': params.get('break_hours', 1),
                                        'max_continuous': params.get('max_continuous', 5),
                                        'min_rest': params.get('min_rest', 12),
                                        'working_days': params.get('working_days', 6),
                                        'carryover_mode': params.get('carryover_mode', 'auto'),
                                        'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                                        'carryover_excel_data': params.get('carryover_excel_data', []),
                                    })
                                elif current_engine == 'overnight':
                                    engine_params = v17_get_params({
                                        'night_shift_enabled': params.get('night_shift', True),
                                        'flexible_day_off': params.get('flexible_day_off', False),
                                        'overnight_enabled': params.get('overnight_enabled', False),
                                        'overnight_start': params.get('overnight_start', 22),
                                        'shift_hours': params.get('shift_hours', 10),
                                        'break_hours': params.get('break_hours', 1),
                                        'max_continuous': params.get('max_continuous', 5),
                                        'min_rest': params.get('min_rest', 12),
                                        'working_days': params.get('working_days', 6),
                                        'carryover_mode': params.get('carryover_mode', 'auto'),
                                        'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                                        'carryover_excel_data': params.get('carryover_excel_data', []),
                                    })
                                else:
                                    engine_params = engine_get_params({
                                        'night_shift_enabled': params.get('night_shift', True),
                                        'flexible_day_off': params.get('flexible_day_off', False),
                                        'shift_hours': params.get('shift_hours', 10),
                                        'break_hours': params.get('break_hours', 1),
                                        'max_continuous': params.get('max_continuous', 5),
                                        'min_rest': params.get('min_rest', 12),
                                        'working_days': params.get('working_days', 6),
                                        'carryover_mode': params.get('carryover_mode', 'auto'),
                                        'sunday_carryover_das': params.get('sunday_carryover_das', 0),
                                        'carryover_excel_data': params.get('carryover_excel_data', []),
                                        'shift_hours_per_day': params.get('shift_hours_per_day', {}),
                                    })
                                
                                # Generate base roster if no optimized shifts exist
                                if store_shifts is None:
                                    try:
                                        transfers = load_transfers()
                                        adjusted = apply_transfer_adjustments(das_df, transfers)
                                        store_das = adjusted[adjusted['Store'] == store].copy()
                                        if store_das.empty or store_das['DA_Count'].sum() <= 0:
                                            continue
                                        
                                        if current_engine == 'fixed':
                                            da_list = fixed_build_da_list(store_das)
                                            store_shifts = assign_shifts_fixed(da_list, demand_df, store, engine_params)
                                        elif current_engine == 'proportional':
                                            da_list = v13_build_da_list(store_das)
                                            store_shifts = v13_assign_shifts(da_list, demand_df[demand_df['Store'] == store], None, engine_params)
                                        elif current_engine == 'demand_driven':
                                            da_list = v14_build_da_list(store_das)
                                            store_shifts = v14_assign_shifts(da_list, demand_df[demand_df['Store'] == store], None, engine_params)
                                        elif current_engine == 'demand_driven_ultimate':
                                            da_list = v14u_build_da_list(store_das)
                                            store_shifts = v14u_assign_shifts(da_list, demand_df[demand_df['Store'] == store], None, engine_params)
                                        elif current_engine == 'tunable':
                                            da_list = v15_build_da_list(store_das)
                                            store_shifts = v15_assign_shifts(da_list, demand_df[demand_df['Store'] == store], None, engine_params)
                                        elif current_engine == 'overnight':
                                            da_list = v17_build_da_list(store_das)
                                            store_shifts = v17_assign_shifts(da_list, demand_df[demand_df['Store'] == store], None, engine_params)
                                        else:
                                            store_priorities = st.session_state.get('store_priorities', {}).get(store, {h: 5 for h in range(24)})
                                            _, store_shifts = generate_roster_with_priorities(
                                                demand_df, das_df, store, store_priorities, params,
                                                day_multipliers=st.session_state.get('day_multipliers', {}),
                                                scale_mode=params.get('scale_mode', 'exponential'),
                                                intensity=params.get('intensity', 2.0)
                                            )
                                    except:
                                        continue
                                
                                if store_shifts is None or store_shifts.empty:
                                    continue
                                
                                # Generate roster
                                if current_engine == 'fixed':
                                    store_roster = fixed_generate_hourly_roster(store_shifts, store_demand, engine_params)
                                elif current_engine == 'proportional':
                                    store_roster = v13_generate_hourly_roster(store_shifts, store_demand, engine_params)
                                elif current_engine == 'demand_driven':
                                    store_roster = v14_generate_hourly_roster(store_shifts, store_demand, engine_params)
                                elif current_engine == 'demand_driven_ultimate':
                                    store_roster = v14u_generate_hourly_roster(store_shifts, store_demand, engine_params)
                                elif current_engine == 'tunable':
                                    store_roster = v15_generate_hourly_roster(store_shifts, store_demand, engine_params)
                                elif current_engine == 'overnight':
                                    store_roster = v17_generate_hourly_roster(store_shifts, store_demand, engine_params)
                                else:
                                    store_roster = engine_generate_hourly_roster(store_shifts, store_demand, engine_params)
                                
                                if store_roster is None or store_roster.empty:
                                    continue
                                
                                total_das = store_shifts['DA_ID'].nunique()
                                gap = abs(store_roster[store_roster['Diff'] < 0]['Diff'].sum())
                                excess = store_roster[store_roster['Diff'] > 0]['Diff'].sum()
                                is_optimized = f'optimized_shifts_{store}' in st.session_state
                                
                                summary_data.append({
                                    'Store': store, 'Total_DAs': total_das,
                                    'Gap': int(gap), 'Excess': int(excess),
                                    'Status': 'Optimized' if is_optimized else 'Base'
                                })
                                
                                store_roster.to_excel(writer, sheet_name=f'{store}_Roster', index=False)
                                store_shifts.to_excel(writer, sheet_name=f'{store}_Shifts', index=False)

                                # DSP Mix matrix per store (opt-in)
                                if enable_dsp_mix and store_shifts is not None and not store_shifts.empty:
                                    try:
                                        dsp_mat = generate_dsp_slot_matrix(
                                            store_shifts, demand_df, store, engine_params
                                        )
                                        if not dsp_mat.empty:
                                            sheet_name = f'{store[:12]}_DSPMix'
                                            dsp_mat.to_excel(writer, sheet_name=sheet_name, index=False)
                                    except Exception:
                                        pass
                            
                            summary_df = pd.DataFrame(summary_data)
                            summary_df.to_excel(writer, sheet_name='Summary', index=False)
                        
                        output.seek(0)
                        st.session_state['all_stores_download'] = output.getvalue()
                        st.session_state['all_stores_count'] = len(summary_data)
                        st.success(f"✅ Prepared {len(summary_data)} stores for download")
                
                if 'all_stores_download' in st.session_state:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    st.download_button(
                        label=f"📥 Download All Stores Roster ({st.session_state.get('all_stores_count', 0)} stores)",
                        data=st.session_state['all_stores_download'],
                        file_name=f"DA_Roster_{selected_week}_AllStores_{timestamp}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
    
    # =============================================================================
    # TAB 2: DA TRANSFERS
    # =============================================================================
    with tab2:
        st.subheader("🔄 DA Transfer Management")
        st.markdown("Track and manage DA transfers between stores.")
        
        transfers = load_transfers()
        
        # New Transfer Form
        st.markdown("### ➕ Record New Transfer")
        
        # DSP selection outside form for dynamic updates
        transfer_col1, transfer_col2 = st.columns(2)
        with transfer_col1:
            from_store_select = st.selectbox("From Store", [''] + list(stores), key="from_store_select")
        with transfer_col2:
            to_store_select = st.selectbox("To Store", [''] + list(stores), key="to_store_select")
        
        # Get DSPs for selected store
        if from_store_select:
            store_dsps = das_df[das_df['Store'] == from_store_select]['DSP'].unique().tolist()
            from_dsp_select = st.selectbox("From DSP (optional)", ['Any'] + store_dsps, key="from_dsp_select")
        else:
            from_dsp_select = 'Any'
            st.info("Select a 'From Store' to see available DSPs")
        
        with st.form("new_transfer"):
            form_col1, form_col2 = st.columns(2)
            
            with form_col1:
                da_count = st.number_input("Number of DAs", min_value=1, max_value=50, value=1)
            
            with form_col2:
                transfer_date = st.date_input("Transfer Date", datetime.now())
            
            notes = st.text_input("Notes (optional)")
            
            submitted = st.form_submit_button("📝 Record Transfer", type="primary")
            
            if submitted:
                if from_store_select and to_store_select and from_store_select != to_store_select:
                    new_transfer = {
                        'date': transfer_date.isoformat(),
                        'from_store': from_store_select,
                        'from_dsp': from_dsp_select if from_dsp_select != 'Any' else None,
                        'to_store': to_store_select,
                        'das': da_count,
                        'notes': notes,
                        'status': 'completed'
                    }
                    transfers['transfers'].append(new_transfer)
                    
                    if from_store_select not in transfers['adjustments']:
                        transfers['adjustments'][from_store_select] = 0
                    if to_store_select not in transfers['adjustments']:
                        transfers['adjustments'][to_store_select] = 0
                    
                    transfers['adjustments'][from_store_select] -= da_count
                    transfers['adjustments'][to_store_select] += da_count
                    
                    save_transfers(transfers)
                    st.success(f"✅ Recorded transfer of {da_count} DA(s) from {from_store_select} to {to_store_select}")
                    st.rerun()
                else:
                    st.error("Please select valid source and destination stores (must be different)")
        
        # Add DAs to Store
        st.markdown("### ➕ Add New DAs to Store")
        with st.form("add_das"):
            add_col1, add_col2, add_col3 = st.columns(3)
            
            with add_col1:
                add_store = st.selectbox("Store", stores, key="add_store")
            with add_col2:
                add_count = st.number_input("Number of New DAs", min_value=1, max_value=100, value=1)
            with add_col3:
                add_notes = st.text_input("Notes", placeholder="e.g., New hires from DSP X")
            
            add_submitted = st.form_submit_button("➕ Add DAs", type="primary")
            
            if add_submitted:
                new_addition = {
                    'date': datetime.now().isoformat(),
                    'from_store': 'NEW_HIRE',
                    'to_store': add_store,
                    'das': add_count,
                    'notes': add_notes,
                    'status': 'completed'
                }
                transfers['transfers'].append(new_addition)
                
                if add_store not in transfers['adjustments']:
                    transfers['adjustments'][add_store] = 0
                transfers['adjustments'][add_store] += add_count
                
                save_transfers(transfers)
                st.success(f"✅ Added {add_count} new DA(s) to {add_store}")
                st.rerun()
        
        # Current Adjustments Summary
        st.markdown("### 📊 Current DA Adjustments")
        if transfers['adjustments']:
            adj_data = []
            for store, adj in transfers['adjustments'].items():
                if adj != 0:
                    original = das_df[das_df['Store'] == store]['DA_Count'].sum() if not das_df[das_df['Store'] == store].empty else 0
                    adj_data.append({
                        'Store': store,
                        'Original_DAs': int(original),
                        'Adjustment': adj,
                        'Current_DAs': int(original + adj),
                        'Change': f"+{adj}" if adj > 0 else str(adj)
                    })
            
            if adj_data:
                adj_df = pd.DataFrame(adj_data)
                
                def highlight_adj(row):
                    if row['Adjustment'] > 0:
                        return ['background-color: #d4edda'] * len(row)
                    elif row['Adjustment'] < 0:
                        return ['background-color: #f8d7da'] * len(row)
                    return [''] * len(row)
                
                st.dataframe(adj_df.style.apply(highlight_adj, axis=1), use_container_width=True)
            else:
                st.info("No adjustments recorded yet.")
        else:
            st.info("No adjustments recorded yet.")
        
        # Transfer History
        st.markdown("### 📜 Transfer History")
        if transfers['transfers']:
            st.markdown("*Click ↩️ to undo a specific transfer*")
            
            # Display each transfer with an undo button
            for idx, transfer in enumerate(reversed(transfers['transfers'])):
                real_idx = len(transfers['transfers']) - 1 - idx  # Get actual index
                
                # Format the transfer info
                from_store = transfer.get('from_store', 'Unknown')
                to_store = transfer.get('to_store', 'Unknown')
                das = transfer.get('das', 0)
                date = transfer.get('date', '')[:10]  # Just the date part
                notes = transfer.get('notes', '')
                
                if from_store == 'NEW_HIRE':
                    transfer_text = f"➕ **{date}**: Added **{das}** DA(s) to **{to_store}**"
                    if notes:
                        transfer_text += f" - _{notes}_"
                else:
                    transfer_text = f"🔄 **{date}**: Transferred **{das}** DA(s) from **{from_store}** → **{to_store}**"
                    if notes:
                        transfer_text += f" - _{notes}_"
                
                col1, col2 = st.columns([5, 1])
                with col1:
                    st.markdown(transfer_text)
                with col2:
                    if st.button("↩️", key=f"undo_transfer_{real_idx}", help="Undo this transfer"):
                        # Reverse the adjustment
                        if from_store == 'NEW_HIRE':
                            # Undo new hire - subtract from to_store
                            if to_store in transfers['adjustments']:
                                transfers['adjustments'][to_store] -= das
                                if transfers['adjustments'][to_store] == 0:
                                    del transfers['adjustments'][to_store]
                        else:
                            # Undo transfer - add back to from_store, subtract from to_store
                            if from_store in transfers['adjustments']:
                                transfers['adjustments'][from_store] += das
                                if transfers['adjustments'][from_store] == 0:
                                    del transfers['adjustments'][from_store]
                            else:
                                transfers['adjustments'][from_store] = das
                            
                            if to_store in transfers['adjustments']:
                                transfers['adjustments'][to_store] -= das
                                if transfers['adjustments'][to_store] == 0:
                                    del transfers['adjustments'][to_store]
                            else:
                                transfers['adjustments'][to_store] = -das
                        
                        # Remove the transfer from history
                        transfers['transfers'].pop(real_idx)
                        save_transfers(transfers)
                        st.success(f"✅ Undone: {transfer_text}")
                        st.rerun()
            
            st.markdown("---")
            
            # Clear all button
            clear_col1, clear_col2 = st.columns([3, 1])
            with clear_col1:
                confirm_clear = st.checkbox("I confirm I want to clear ALL transfer history", key="confirm_clear_all")
            with clear_col2:
                if st.button("🗑️ Clear All", type="secondary", disabled=not confirm_clear):
                    save_transfers({'transfers': [], 'adjustments': {}})
                    st.success("✅ All transfer history cleared")
                    st.rerun()
        else:
            st.info("No transfers recorded yet.")

if __name__ == "__main__":
    main()
