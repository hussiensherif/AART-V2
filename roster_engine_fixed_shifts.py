"""
FIXED SHIFTS ROSTER ENGINE
===========================
6 predefined shift start times with automatic DA allocation based on demand.

SHIFTS:
- Shift 1: 05:00 (Dawn)
- Shift 2: 07:00 (Early Morning)
- Shift 3: 11:00 (Late Morning)
- Shift 4: 12:00 (Noon)
- Shift 5: 15:00 (Afternoon)
- Shift 6: 19:00 (Evening)

SACRED RULES (enforced):
- 12h minimum rest between shifts
- 5h max continuous before break (break at hour 4 or 5)
- 6 working days per week (1 day off)
- Fri/Sat must be working days

FEATURES:
- Auto-calculates optimal DA count per shift based on demand coverage
- Allows manual adjustment of shift shares
- Respects all sacred rules
- Violation detection and prevention
"""

import pandas as pd
import numpy as np
from collections import defaultdict

# Days of week
DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

# Fixed shift definitions (start hour, name)
FIXED_SHIFTS = {
    1: {'start': 5, 'name': 'Dawn (05:00)'},
    2: {'start': 7, 'name': 'Early Morning (07:00)'},
    3: {'start': 11, 'name': 'Late Morning (11:00)'},
    4: {'start': 12, 'name': 'Noon (12:00)'},
    5: {'start': 15, 'name': 'Afternoon (15:00)'},
    6: {'start': 19, 'name': 'Evening (19:00)'},
}

DEFAULT_PARAMS = {
    'shift_hours': 10,
    'break_hours': 1,
    'max_continuous': 5,
    'min_rest': 12,
    'working_days': 6,
    'flexible_day_off': False,  # Fri/Sat must work
}

def get_params(overrides=None):
    """Get engine parameters with optional overrides."""
    params = DEFAULT_PARAMS.copy()
    if overrides:
        params.update(overrides)
    # Set custom_shifts to default if not provided
    if 'custom_shifts' not in params:
        params['custom_shifts'] = FIXED_SHIFTS.copy()
    return params

def get_active_shifts(params):
    """Get the active shifts configuration (custom or default)."""
    return params.get('custom_shifts', FIXED_SHIFTS)


# =============================================================================
# VIOLATION DETECTION AND PREVENTION
# =============================================================================

def get_valid_break_positions(shift_start, shift_hours, max_continuous):
    """
    Get ALL valid break hour positions for a shift.
    A position is valid if neither work segment exceeds max_continuous.
    
    For a 9h shift with max_continuous=5:
    - Position 3: 3h work + break + 5h work ✓
    - Position 4: 4h work + break + 4h work ✓
    - Position 5: 5h work + break + 3h work ✓
    
    Returns: list of valid break hours (0-23)
    """
    valid = []
    break_hours_duration = 1  # break is 1 slot in the roster grid
    
    for pos in range(1, shift_hours):
        hours_before = pos
        hours_after = shift_hours - pos - break_hours_duration
        if hours_after < 0:
            continue
        if hours_before <= max_continuous and hours_after <= max_continuous:
            valid.append((shift_start + pos) % 24)
    
    return valid if valid else [(shift_start + max_continuous) % 24]


def calculate_valid_break_hour(shift_start, shift_hours, max_continuous):
    """
    Calculate a valid break hour that respects max_continuous rule.
    Returns the default position (at max_continuous).
    For smarter placement, use get_valid_break_positions() and pick the best.
    
    Returns: break hour (0-23)
    """
    positions = get_valid_break_positions(shift_start, shift_hours, max_continuous)
    # Default: pick the one at max_continuous, or the last valid position
    default = (shift_start + max_continuous) % 24
    if default in positions:
        return default
    return positions[-1] if positions else (shift_start + max_continuous) % 24


def detect_violations(shifts_df, params):
    """
    Detect all violations in a shifts DataFrame.
    
    Returns: dict with violation counts and details
    {
        'total_violations': int,
        'no_break': [{'da_id': str, 'day': str, 'shift_start': int, 'shift_hours': int}],
        'max_continuous_exceeded': [{'da_id': str, 'day': str, 'continuous_hours': int, 'max_allowed': int}],
        'insufficient_rest': [{'da_id': str, 'day1': str, 'day2': str, 'rest_hours': int, 'min_required': int}],
        'fri_sat_off': [{'da_id': str, 'day': str}],
        'summary': str
    }
    """
    if shifts_df is None or shifts_df.empty:
        return {'total_violations': 0, 'no_break': [], 'max_continuous_exceeded': [], 
                'insufficient_rest': [], 'fri_sat_off': [], 'summary': 'No shifts to check'}
    
    shift_hours = params.get('shift_hours', 10)
    break_hours = params.get('break_hours', 1)
    max_continuous = params.get('max_continuous', 5)
    min_rest = params.get('min_rest', 12)
    flexible_day_off = params.get('flexible_day_off', False)
    
    violations = {
        'total_violations': 0,
        'no_break': [],
        'max_continuous_exceeded': [],
        'insufficient_rest': [],
        'fri_sat_off': [],
        'summary': ''
    }
    
    # Check each DA
    for da_id in shifts_df['DA_ID'].unique():
        da_shifts = shifts_df[shifts_df['DA_ID'] == da_id].sort_values('Day_Index')
        
        prev_shift_end = None
        prev_day = None
        
        for _, shift in da_shifts.iterrows():
            day = shift['Day']
            is_off = shift['Is_Day_Off']
            
            # Check Fri/Sat off violation (if not flexible)
            if not flexible_day_off and is_off and day in ['Fri', 'Sat']:
                violations['fri_sat_off'].append({
                    'da_id': da_id,
                    'day': day
                })
                violations['total_violations'] += 1
            
            if is_off or pd.isna(shift.get('Shift_Start')):
                # Don't reset — keep tracking last working shift for rest calc across off-days
                prev_day = day
                continue
            
            shift_start = int(shift['Shift_Start'])
            shift_end = int(shift['Shift_End']) if pd.notna(shift.get('Shift_End')) else (shift_start + shift_hours) % 24
            break_hour = shift.get('Break_Hour')
            
            # Check for missing break
            if pd.isna(break_hour) and shift_hours > max_continuous:
                violations['no_break'].append({
                    'da_id': da_id,
                    'day': day,
                    'shift_start': shift_start,
                    'shift_hours': shift_hours
                })
                violations['total_violations'] += 1
            
            # Check max continuous violation
            if not pd.isna(break_hour):
                break_hour = int(break_hour)
                break_hour_2 = shift.get('Break_Hour_2')
                
                if break_hours >= 2 and pd.notna(break_hour_2):
                    # 2 breaks: check all 3 segments
                    break_hour_2 = int(break_hour_2)
                    breaks_sorted = sorted([(break_hour - shift_start) % 24, (break_hour_2 - shift_start) % 24])
                    seg1 = breaks_sorted[0]
                    seg2 = breaks_sorted[1] - breaks_sorted[0] - 1
                    seg3 = shift_hours - breaks_sorted[1] - 1
                    for seg_name, seg_len in [('segment_1', seg1), ('segment_2', seg2), ('segment_3', seg3)]:
                        if seg_len > max_continuous:
                            violations['max_continuous_exceeded'].append({
                                'da_id': da_id,
                                'day': day,
                                'continuous_hours': seg_len,
                                'max_allowed': max_continuous,
                                'segment': seg_name
                            })
                            violations['total_violations'] += 1
                else:
                    # Single break
                    hours_before_break = (break_hour - shift_start) % 24
                    if hours_before_break > shift_hours:
                        hours_before_break = shift_hours
                    hours_after_break = shift_hours - hours_before_break - 1
                    if hours_after_break < 0:
                        hours_after_break = 0
                    
                    if hours_before_break > max_continuous:
                        violations['max_continuous_exceeded'].append({
                            'da_id': da_id,
                            'day': day,
                            'continuous_hours': hours_before_break,
                            'max_allowed': max_continuous,
                            'segment': 'before_break'
                        })
                        violations['total_violations'] += 1
                    
                    if hours_after_break > max_continuous:
                        violations['max_continuous_exceeded'].append({
                            'da_id': da_id,
                            'day': day,
                            'continuous_hours': hours_after_break,
                            'max_allowed': max_continuous,
                            'segment': 'after_break'
                        })
                        violations['total_violations'] += 1
            
            # Check rest between working shifts (handles gaps from off-days)
            if prev_shift_end is not None and prev_day is not None:
                prev_day_idx = DAYS.index(prev_day)
                curr_day_idx = DAYS.index(day)
                
                # Detect overnight by checking if prev shift crossed midnight
                prev_was_overnight = (prev_shift_end < 12) and (prev_shift_end != 0) and (prev_shift_end < shift_start)
                
                if prev_was_overnight:
                    end_day_idx = (prev_day_idx + 1) % 7
                else:
                    end_day_idx = prev_day_idx
                
                if prev_shift_end == 0:
                    end_day_idx = (end_day_idx + 1) % 7
                    effective_prev_end = 0
                else:
                    effective_prev_end = prev_shift_end
                
                day_gap = (curr_day_idx - end_day_idx) % 7
                
                if day_gap == 0:
                    rest_hours = shift_start - effective_prev_end
                elif day_gap == 1:
                    rest_hours = (24 - effective_prev_end) + shift_start
                else:
                    rest_hours = (24 - effective_prev_end) + (day_gap - 1) * 24 + shift_start
                
                if rest_hours < min_rest and rest_hours >= 0:
                    violations['insufficient_rest'].append({
                        'da_id': da_id,
                        'day1': prev_day,
                        'day2': day,
                        'rest_hours': rest_hours,
                        'min_required': min_rest
                    })
                    violations['total_violations'] += 1
            
            prev_shift_end = shift_end
            prev_day = day
    
    # Generate summary
    summary_parts = []
    if violations['no_break']:
        summary_parts.append(f"{len(violations['no_break'])} missing breaks")
    if violations['max_continuous_exceeded']:
        summary_parts.append(f"{len(violations['max_continuous_exceeded'])} max continuous violations")
    if violations['insufficient_rest']:
        summary_parts.append(f"{len(violations['insufficient_rest'])} rest violations")
    if violations['fri_sat_off']:
        summary_parts.append(f"{len(violations['fri_sat_off'])} Fri/Sat off violations")
    
    if summary_parts:
        violations['summary'] = f"⚠️ {violations['total_violations']} violations: " + ", ".join(summary_parts)
    else:
        violations['summary'] = "✅ No violations detected"
    
    return violations


def fix_violations(shifts_df, params):
    """
    Fix violations in shifts DataFrame.
    
    Returns: (fixed_shifts_df, changes_made, violations_fixed)
    """
    if shifts_df is None or shifts_df.empty:
        return shifts_df, 0, []
    
    fixed_df = shifts_df.copy()
    shift_hours = params.get('shift_hours', 10)
    max_continuous = params.get('max_continuous', 5)
    
    changes_made = 0
    violations_fixed = []
    
    # Fix break violations
    for idx, row in fixed_df.iterrows():
        if row['Is_Day_Off'] or pd.isna(row.get('Shift_Start')):
            continue
        
        shift_start = int(row['Shift_Start'])
        break_hour = row.get('Break_Hour')
        
        # Check if break is missing or invalid
        needs_fix = False
        
        if pd.isna(break_hour):
            needs_fix = True
            reason = 'missing_break'
        else:
            break_hour = int(break_hour)
            hours_before = (break_hour - shift_start) % 24
            if hours_before > shift_hours:
                hours_before = shift_hours
            
            if hours_before > max_continuous or hours_before == 0:
                needs_fix = True
                reason = 'invalid_break_position'
        
        if needs_fix:
            # Calculate valid break hour
            valid_break = calculate_valid_break_hour(shift_start, shift_hours, max_continuous)
            fixed_df.at[idx, 'Break_Hour'] = valid_break
            changes_made += 1
            violations_fixed.append({
                'da_id': row['DA_ID'],
                'day': row['Day'],
                'old_break': break_hour if not pd.isna(break_hour) else None,
                'new_break': valid_break,
                'reason': reason
            })
    
    return fixed_df, changes_made, violations_fixed


def validate_and_fix_shifts(shifts_df, params):
    """
    Validate shifts and fix any violations.
    
    Returns: (validated_shifts_df, violation_report)
    """
    # First detect violations
    violations = detect_violations(shifts_df, params)
    
    if violations['total_violations'] == 0:
        return shifts_df, violations
    
    # Fix violations
    fixed_df, changes, fixes = fix_violations(shifts_df, params)
    
    # Re-check for remaining violations
    remaining = detect_violations(fixed_df, params)
    
    return fixed_df, remaining

def get_shift_coverage_hours(shift_start, params):
    """Get list of hours covered by a shift (excluding break)."""
    shift_hours = params.get('shift_hours', 10)
    max_continuous = params.get('max_continuous', 5)
    break_hour = calculate_valid_break_hour(shift_start, shift_hours, max_continuous)
    
    covered = []
    for h in range(shift_hours):  # shift_hours includes break time
        hour = (shift_start + h) % 24
        if hour != break_hour:
            covered.append(hour)
    return covered

def calculate_hourly_demand(demand_df, store):
    """Calculate total weekly demand per hour slot."""
    store_demand = demand_df[demand_df['Store'] == store]
    
    hourly_demand = {}
    for hour in range(24):
        # Sum demand across all days for this hour
        hour_data = store_demand[store_demand['Slot'] == hour]
        hourly_demand[hour] = hour_data['DA Required'].sum() if not hour_data.empty else 0
    
    return hourly_demand

def calculate_shift_coverage_value(shift_start, hourly_demand, params):
    """Calculate how much demand a single DA on this shift covers."""
    covered_hours = get_shift_coverage_hours(shift_start, params)
    total_coverage = sum(hourly_demand.get(h, 0) for h in covered_hours)
    return total_coverage

def calculate_optimal_shift_distribution(demand_df, store, total_das, params, shift_shares=None):
    """
    Calculate optimal number of DAs per shift based on demand.
    
    If shift_shares is provided, use those percentages.
    Otherwise, calculate based on demand coverage.
    
    Returns: dict {shift_id: da_count}
    """
    hourly_demand = calculate_hourly_demand(demand_df, store)
    active_shifts = get_active_shifts(params)
    num_shifts = len(active_shifts)
    
    if shift_shares:
        # Use provided shares (percentages)
        distribution = {}
        remaining = total_das
        
        # Sort by share descending to handle rounding
        sorted_shifts = sorted(shift_shares.items(), key=lambda x: x[1], reverse=True)
        
        for i, (shift_id, share) in enumerate(sorted_shifts):
            if shift_id not in active_shifts:
                continue  # Skip shares for deleted shifts
            if i == len(sorted_shifts) - 1:
                # Last shift gets remainder
                distribution[shift_id] = max(0, remaining)
            else:
                count = int(total_das * share / 100)  # Use floor instead of round
                count = min(count, remaining)
                count = max(count, 0)
                distribution[shift_id] = count
                remaining -= count
        
        return distribution
    
    # Auto-calculate based on demand coverage
    shift_values = {}
    for shift_id, shift_info in active_shifts.items():
        value = calculate_shift_coverage_value(shift_info['start'], hourly_demand, params)
        shift_values[shift_id] = value
    
    total_value = sum(shift_values.values())
    
    if total_value == 0:
        # Equal distribution if no demand data
        per_shift = total_das // num_shifts
        distribution = {i: per_shift for i in active_shifts.keys()}
        first_shift = min(active_shifts.keys())
        distribution[first_shift] += total_das - (per_shift * num_shifts)  # Remainder to first shift
        return distribution
    
    # Distribute proportionally to coverage value
    distribution = {}
    remaining = total_das
    
    sorted_shifts = sorted(shift_values.items(), key=lambda x: x[1], reverse=True)
    
    for i, (shift_id, value) in enumerate(sorted_shifts):
        if i == len(sorted_shifts) - 1:
            distribution[shift_id] = max(0, remaining)
        else:
            proportion = value / total_value
            count = int(total_das * proportion)  # Use floor instead of round
            count = min(count, remaining)
            count = max(count, 0)
            distribution[shift_id] = count
            remaining -= count
    
    return distribution

def assign_off_days(da_list, params):
    """
    Assign off days to DAs.
    Fri/Sat must be working days (flexible_day_off=False enforced).
    Distributes off days across Sun-Thu evenly.
    """
    working_days = params.get('working_days', 6)
    off_days_needed = 7 - working_days
    
    # Available off days (not Fri/Sat)
    available_off_days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu']
    
    off_day_assignments = {}
    
    for i, da_id in enumerate(da_list):
        # Rotate through available off days
        off_day = available_off_days[i % len(available_off_days)]
        off_day_assignments[da_id] = off_day
    
    return off_day_assignments

def build_da_list(das_df):
    """Build list of DA IDs from available DAs dataframe.
    Format: {Store}-{DSP_Code}-{number} (same as flexible engine)
    """
    da_list = []
    for _, row in das_df.iterrows():
        store = row.get('Store', 'Unknown')
        dsp_code = row.get('DSP_Code', row.get('DSP', 'Unknown'))
        count = int(row.get('DA_Count', 0))
        for i in range(count):
            da_id = f"{store}-{dsp_code}-{str(i+1).zfill(3)}"
            da_list.append(da_id)
    return da_list

def assign_shifts_fixed(da_list, demand_df, store, params, shift_shares=None):
    """
    Assign fixed shifts to DAs.
    
    Returns: DataFrame with columns:
    - DA_ID, Day, Day_Index, Shift_ID, Shift_Start, Shift_End, Break_Hour, Is_Day_Off
    """
    total_das = len(da_list)
    active_shifts = get_active_shifts(params)
    
    if total_das == 0:
        return pd.DataFrame()
    
    # Calculate DA distribution per shift
    distribution = calculate_optimal_shift_distribution(
        demand_df, store, total_das, params, shift_shares
    )
    
    # Assign off days
    off_day_assignments = assign_off_days(da_list, params)
    
    # Build shift assignments
    records = []
    da_index = 0
    shift_hours = params.get('shift_hours', 10)
    max_continuous = params.get('max_continuous', 5)
    
    for shift_id, da_count in distribution.items():
        if shift_id not in active_shifts:
            continue
        shift_start = active_shifts[shift_id]['start']
        shift_end = (shift_start + shift_hours) % 24
        # Use valid break calculation instead of hardcoded hour 5
        break_hour = calculate_valid_break_hour(shift_start, shift_hours, max_continuous)
        
        for _ in range(da_count):
            if da_index >= len(da_list):
                break
            
            da_id = da_list[da_index]
            off_day = off_day_assignments[da_id]
            
            for day_idx, day in enumerate(DAYS):
                is_off = (day == off_day)
                
                records.append({
                    'DA_ID': da_id,
                    'Store': store,  # Add Store column
                    'Day': day,
                    'Day_Index': day_idx,
                    'Shift_ID': shift_id,
                    'Shift_Name': active_shifts[shift_id]['name'],
                    'Shift_Start': None if is_off else shift_start,
                    'Shift_End': None if is_off else shift_end,
                    'Break_Hour': None if is_off else break_hour,
                    'Is_Day_Off': is_off
                })
            
            da_index += 1
    
    shifts_df = pd.DataFrame(records)
    
    # Smart break placement: test all valid positions per DA per day, pick lowest-demand hour
    if not shifts_df.empty and not demand_df.empty:
        store_demand = demand_df[demand_df['Store'] == store] if 'Store' in demand_df.columns else demand_df
        # Build demand lookup
        demand_lookup = {}
        for _, row in store_demand.iterrows():
            day = str(row.get('Day', ''))[:3]
            slot = int(row.get('Slot', 0))
            demand_lookup[(day, slot)] = row.get('DA Required', 0) if pd.notna(row.get('DA Required')) else 0
        
        for idx, row in shifts_df.iterrows():
            if row['Is_Day_Off'] or pd.isna(row.get('Shift_Start')):
                continue
            start = int(row['Shift_Start'])
            day = row['Day']
            
            valid_positions = get_valid_break_positions(start, shift_hours, max_continuous)
            if len(valid_positions) <= 1:
                continue
            
            # Pick the break position with lowest demand
            best_pos = valid_positions[0]
            best_demand = float('inf')
            for pos in valid_positions:
                d = demand_lookup.get((day, pos), 0)
                if d < best_demand:
                    best_demand = d
                    best_pos = pos
            
            shifts_df.at[idx, 'Break_Hour'] = best_pos
    
    # Validate and fix any violations before returning
    if not shifts_df.empty:
        shifts_df, _ = validate_and_fix_shifts(shifts_df, params)
    
    return shifts_df

def generate_hourly_roster(shifts_df, demand_df, params):
    """
    Generate hourly roster showing coverage vs demand.
    Compatible with v12.2 format.
    """
    if shifts_df is None or shifts_df.empty:
        return pd.DataFrame()
    
    store = demand_df['Store'].iloc[0] if 'Store' in demand_df.columns else 'Unknown'
    store_demand = demand_df.copy()
    shift_hours = params.get('shift_hours', 10)
    skip_sunday_overnight = params.get('skip_sunday_overnight', False)
    carryover_mode = params.get('carryover_mode', 'auto')
    sunday_carryover_das = params.get('sunday_carryover_das', 0)
    carryover_excel_data = params.get('carryover_excel_data', [])  # List of {DA_ID, Store, Sat_Shift_End}
    
    # Get store-specific carryover DAs from Excel data
    store_carryover_das = [c for c in carryover_excel_data if c.get('Store') == store]
    
    # Get all unique DAs
    all_das = sorted(shifts_df['DA_ID'].unique())
    
    records = []
    
    for day_idx, day in enumerate(DAYS):
        # Get previous day for overnight carryover
        prev_day = DAYS[(day_idx - 1) % 7]
        prev_shifts = shifts_df[shifts_df['Day'] == prev_day]
        
        # Determine carryover handling for Sunday
        is_sunday = (day == 'Sun')
        use_manual_carryover = is_sunday and carryover_mode == 'manual'
        use_excel_carryover = is_sunday and carryover_mode == 'excel'
        skip_prev_overnight = skip_sunday_overnight and is_sunday
        
        for slot in range(24):
            # Get demand for this slot
            demand_row = store_demand[
                (store_demand['Day'].str[:3] == day) &
                (store_demand['Slot'] == slot)
            ]
            required = int(demand_row['DA Required'].values[0]) if len(demand_row) > 0 and pd.notna(demand_row['DA Required'].values[0]) else 0
            
            # Get orders (try multiple column names, handle duplicate columns)
            orders = 0
            if len(demand_row) > 0:
                for col_name in ['Final Orders', 'Hourly Orders', 'Orders']:
                    if col_name in demand_row.columns:
                        orders_val = demand_row[col_name].values
                        # Handle duplicate columns - take first value
                        if hasattr(orders_val, '__len__') and len(orders_val) > 0:
                            first_val = orders_val.flat[0] if hasattr(orders_val, 'flat') else orders_val[0]
                            orders = int(first_val) if pd.notna(first_val) else 0
                        break
            
            # Calculate coverage
            da_status = {da: '-' for da in all_das}
            rostered = 0
            
            # Handle manual carryover for Sunday early morning (00:00-05:00)
            if use_manual_carryover and slot < 5 and sunday_carryover_das > 0:
                rostered += sunday_carryover_das
            # Handle Excel carryover for Sunday - individual DAs with their end times
            elif use_excel_carryover and is_sunday:
                for carryover_da in store_carryover_das:
                    sat_end = carryover_da.get('Sat_Shift_End', 5)
                    if slot < sat_end:
                        # This DA is still working from Saturday night
                        # Find matching DA in roster by ID
                        carryover_da_id = carryover_da.get('DA_ID', '')
                        if carryover_da_id in da_status:
                            da_status[carryover_da_id] = '1'
                            rostered += 1
                        else:
                            # DA not found in roster - still count for coverage
                            rostered += 1
            
            # Check previous day's overnight shifts (carryover) - only if not skipping and not manual/excel
            if not skip_prev_overnight and not use_manual_carryover and not use_excel_carryover:
                for _, shift in prev_shifts.iterrows():
                    da_id = shift['DA_ID']
                    
                    if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
                        continue
                    
                    start = int(shift['Shift_Start'])
                    end = int(shift['Shift_End']) if pd.notna(shift['Shift_End']) else (start + shift_hours) % 24
                    brk = int(shift['Break_Hour']) if pd.notna(shift['Break_Hour']) else calculate_valid_break_hour(start, shift_hours, params.get('max_continuous', 5))
                    
                    # Check if this is an overnight shift (ends next day)
                    is_overnight = end < start
                    
                    if is_overnight and slot < end:
                        # This slot is covered by previous day's overnight shift
                        if slot == brk:
                            da_status[da_id] = 'B'
                        else:
                            da_status[da_id] = '1'
                            rostered += 1
            
            day_shifts = shifts_df[shifts_df['Day'] == day]
            
            for _, shift in day_shifts.iterrows():
                da_id = shift['DA_ID']
                
                # Skip if already covered by overnight carryover
                if da_status[da_id] in ['1', 'B']:
                    continue
                
                if shift['Is_Day_Off']:
                    da_status[da_id] = 'OFF'
                    continue
                
                if pd.isna(shift['Shift_Start']):
                    continue
                
                start = int(shift['Shift_Start'])
                end = int(shift['Shift_End']) if pd.notna(shift['Shift_End']) else (start + shift_hours) % 24
                brk = int(shift['Break_Hour']) if pd.notna(shift['Break_Hour']) else calculate_valid_break_hour(start, shift_hours, params.get('max_continuous', 5))
                
                # Check if this is an overnight shift
                is_overnight = end < start
                
                # Determine which hours of this shift belong to TODAY vs NEXT DAY
                # For overnight shifts: hours >= start are today, hours < start are next day
                shift_covers_slot = False
                slot_is_break = False
                
                for h in range(shift_hours):
                    hour = (start + h) % 24
                    
                    # For overnight shifts, skip hours that belong to next day
                    if is_overnight and hour < start and hour >= end:
                        continue  # This hour is beyond the shift end
                    if is_overnight and hour < start:
                        continue  # This hour is on the next day
                    
                    if hour == slot:
                        if hour == brk:
                            slot_is_break = True
                        else:
                            shift_covers_slot = True
                        break
                
                if shift_covers_slot:
                    da_status[da_id] = '1'
                    rostered += 1
                elif slot_is_break:
                    da_status[da_id] = 'B'
            
            record = {
                'Store': store,
                'Day': day,
                'Slot': slot,
                'Orders': orders,
                'Required': required,
                'Rostered': rostered,
                'Diff': rostered - required
            }
            record.update(da_status)
            records.append(record)
    
    return pd.DataFrame(records)

def generate_da_summary(shifts_df, params):
    """Generate DA summary showing shift assignments."""
    if shifts_df is None or shifts_df.empty:
        return pd.DataFrame()
    
    shift_hours = params.get('shift_hours', 10)
    break_hours = params.get('break_hours', 1)
    effective_hours = shift_hours - break_hours
    working_days = params.get('working_days', 6)
    
    records = []
    
    for da_id in shifts_df['DA_ID'].unique():
        da_shifts = shifts_df[shifts_df['DA_ID'] == da_id]
        
        working_count = len(da_shifts[~da_shifts['Is_Day_Off']])
        off_day = da_shifts[da_shifts['Is_Day_Off']]['Day'].values[0] if any(da_shifts['Is_Day_Off']) else 'None'
        
        # Get shift info (same shift all week for fixed)
        working_shift = da_shifts[~da_shifts['Is_Day_Off']].iloc[0] if working_count > 0 else None
        
        if working_shift is not None:
            shift_id = working_shift['Shift_ID']
            shift_name = working_shift['Shift_Name']
            shift_start = int(working_shift['Shift_Start'])
            shift_end = int(working_shift['Shift_End'])
        else:
            shift_id = None
            shift_name = 'N/A'
            shift_start = None
            shift_end = None
        
        records.append({
            'DA_ID': da_id,
            'Shift_ID': shift_id,
            'Shift_Name': shift_name,
            'Shift_Start': f"{shift_start:02d}:00" if shift_start is not None else 'N/A',
            'Shift_End': f"{shift_end:02d}:00" if shift_end is not None else 'N/A',
            'Off_Day': off_day,
            'Working_Days': working_count,
            'Weekly_Hours': working_count * effective_hours
        })
    
    return pd.DataFrame(records)

def get_shift_distribution_summary(shifts_df, params=None):
    """Get summary of DA count per shift."""
    if shifts_df is None or shifts_df.empty:
        return {}
    
    # Check if Shift_ID column exists (only for fixed shifts engine)
    if 'Shift_ID' not in shifts_df.columns:
        return {}
    
    # Get active shifts
    if params:
        active_shifts = get_active_shifts(params)
    else:
        active_shifts = FIXED_SHIFTS
    
    # Count unique DAs per shift
    da_per_shift = shifts_df.groupby('Shift_ID')['DA_ID'].nunique().to_dict()
    
    summary = {}
    for shift_id, info in active_shifts.items():
        summary[shift_id] = {
            'name': info['name'],
            'start': info['start'],
            'da_count': da_per_shift.get(shift_id, 0)
        }
    
    return summary
