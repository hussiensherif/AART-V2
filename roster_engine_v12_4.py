"""
DA ROSTERING ENGINE v12.4
==========================
V12.4 Demand-Driven Proportional Scoring Engine

KEY FEATURES:
- V12's demand-weighted scoring (prioritizes peak hours)
- NEW: Smart break selection for shifts covering BOTH dawn & late peak
- NEW: Forces breaks into dawn hours (2-8) when shift covers both zones
- Toggle: flexible_day_off parameter
  - False (default): Fri/Sat must work
  - True: All days allowed for day-off

SACRED RULES (Non-negotiable):
1. 12h minimum rest between shifts
2. 5h max continuous before break (break at hour 4 or 5)
3. 6 working days per week (1 day off)

OUTPUT: Same format as v8 for webapp compatibility
"""

import pandas as pd
import numpy as np
from datetime import datetime
from break_utils import place_breaks

DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

DEFAULT_PARAMS = {
    'shift_hours': 10,
    'break_hours': 1,
    'max_continuous': 5,
    'min_rest': 12,
    'max_rest': None,  # None = no upper limit; set equal to min_rest to force same start time daily
    'working_days': 6,
    'night_shift_enabled': True,
    'flexible_day_off': True,  # v12.4: Default to flexible (all days allowed for off-day)
    'fixed_start_optimizer': 'post_off',  # 'strict' | 'post_off' | 'flexible'
    'max_shifts': 0,  # 0 = unlimited; >0 = limit to N best start times
}

def get_params(custom_params=None):
    params = DEFAULT_PARAMS.copy()
    if custom_params:
        params.update(custom_params)
    params['effective_hours'] = params['shift_hours'] - params['break_hours']
    # Convenience: if max_rest not set but min_rest makes shift+rest=24, auto-set
    if params.get('max_rest') is None and params['shift_hours'] + params['min_rest'] == 24:
        params['max_rest'] = params['min_rest']
    return params

# =============================================================================
# DATA LOADING
# =============================================================================
def load_demand(file_path, store=None):
    df = pd.read_excel(file_path, sheet_name='Slot Level DA Requirement')
    if store:
        df = df[df['Store'] == store]
    return df

def load_available_das(file_path, store=None):
    df = pd.read_excel(file_path, sheet_name='Available DAs')
    df = df.rename(columns={
        'Station': 'Store', 'DSP Name': 'DSP',
        'DSP Code': 'DSP_Code', 'Actual': 'DA_Count'
    })
    if store:
        df = df[df['Store'] == store]
    return df[df['DA_Count'] > 0]

def load_carryover(file_path):
    try:
        df = pd.read_excel(file_path, sheet_name='Next_Week_Carryover')
        return df[df['Sat_Shift_End'].notna()]
    except:
        return pd.DataFrame()

def build_da_list(available_das_df):
    das = []
    for _, row in available_das_df.iterrows():
        for i in range(int(row['DA_Count'])):
            das.append({
                'DA_ID': f"{row['Store']}-{row['DSP_Code']}-{str(i+1).zfill(3)}",
                'Store': row['Store'],
                'DSP': row['DSP'],
                'DSP_Code': row['DSP_Code']
            })
    return pd.DataFrame(das)

def build_demand_matrix(demand_df):
    matrix = {day: [0] * 24 for day in DAYS}
    for _, row in demand_df.iterrows():
        day = str(row['Day'])[:3]
        slot = int(row['Slot'])
        if pd.notna(row['DA Required']):
            matrix[day][slot] = int(row['DA Required'])
    return matrix

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
def _get_shift_hours(params, day_idx=None):
    """Get shift hours, optionally per-day. Falls back to global."""
    if day_idx is not None and 'shift_hours_per_day' in params:
        day_name = DAYS[day_idx] if isinstance(day_idx, int) else day_idx
        return params['shift_hours_per_day'].get(day_name, params['shift_hours'])
    return params['shift_hours']

def _shift_end(start, params, day_idx=None):
    sh = _get_shift_hours(params, day_idx)
    return (start + sh) % 24

def _is_overnight(start, params, day_idx=None):
    end = _shift_end(start, params, day_idx)
    if end == 0:
        return False
    return end < start

def _valid_breaks(start, params, day_idx=None):
    """Return ALL valid break positions where both pre-break and post-break segments satisfy max_continuous."""
    mc = params['max_continuous']
    sh = _get_shift_hours(params, day_idx)
    positions = []
    for pos in range(1, sh):
        if pos <= mc and (sh - pos - 1) <= mc:
            positions.append((start + pos) % 24)
    return positions if positions else [(start + mc) % 24]

def _place_breaks(start, params, day_idx=None, demand_row=None):
    """Wrapper around break_utils.place_breaks that extracts params and returns break hour list.

    Returns list of break hours (length 0, 1, or 2 depending on break_hours).
    """
    sh = _get_shift_hours(params, day_idx)
    break_hours = params.get('break_hours', 1)
    max_continuous = params['max_continuous']
    return place_breaks(start, sh, break_hours, max_continuous, demand_row)

def _calc_rest(prev_end, prev_day_idx, prev_overnight, curr_day_idx, curr_start):
    if prev_end is None:
        return 999
    
    end_day = (prev_day_idx + 1) % 7 if prev_overnight else prev_day_idx
    
    if prev_end == 0:
        end_day = (end_day + 1) % 7
        effective_prev_end = 0
    else:
        effective_prev_end = prev_end
    
    day_gap = (curr_day_idx - end_day) % 7
    
    if day_gap == 0:
        return curr_start - effective_prev_end
    elif day_gap == 1:
        return (24 - effective_prev_end) + curr_start
    else:
        return (24 - effective_prev_end) + (day_gap - 1) * 24 + curr_start

def _valid_starts(day_idx, prev_end, prev_day_idx, prev_overnight, params):
    if prev_end is None:
        valid = list(range(24))
    else:
        valid = [s for s in range(24) if _calc_rest(prev_end, prev_day_idx, prev_overnight, day_idx, s) >= params['min_rest']]
    
    if not params.get('night_shift_enabled', True):
        sh = _get_shift_hours(params, day_idx)
        max_start = 24 - sh
        valid = [s for s in valid if s <= max_start]
    
    # Operating window constraint: restrict to valid start times if provided
    allowed = params.get('valid_start_times')
    if allowed is not None:
        valid = [s for s in valid if s in allowed]
    
    return valid if valid else None

def _covers(start, break_hr, day_idx, params, break_hr_2=None):
    coverage = []
    overnight = _is_overnight(start, params, day_idx)
    sh = _get_shift_hours(params, day_idx)
    
    for h in range(sh):
        hour = (start + h) % 24
        if break_hr is not None and break_hr >= 0 and hour == break_hr:
            continue
        if break_hr_2 is not None and break_hr_2 >= 0 and hour == break_hr_2:
            continue
        if overnight and hour < start:
            coverage.append(((day_idx + 1) % 7, hour))
        else:
            coverage.append((day_idx, hour))
    return coverage

def _calc_coverage(shifts, n, params):
    cov = [[0] * 24 for _ in range(7)]
    for i in range(n):
        for di in range(7):
            if shifts[i][di]:
                st, breaks = shifts[i][di]
                br = breaks[0] if breaks else None
                br2 = breaks[1] if len(breaks) > 1 else None
                for td, hr in _covers(st, br, di, params, break_hr_2=br2):
                    cov[td][hr] += 1
    return cov

def _calc_gap(demand, coverage):
    return sum(max(0, demand[DAYS[di]][h] - coverage[di][h]) for di in range(7) for h in range(24))

def _count_zeros(demand, coverage):
    return sum(1 for di in range(7) for h in range(24) 
               if demand[DAYS[di]][h] > 0 and coverage[di][h] == 0)

# =============================================================================
# DEMAND ANALYSIS
# =============================================================================
def _classify_hours(demand, day):
    """Classify hours into peak/off-peak based on demand percentiles.
    
    Computes 75th percentile of nonzero hourly demand values for the day.
    Returns {'peak': set, 'off_peak': set} based on threshold.
    Handles all-zero demand day (returns all hours as off_peak).
    """
    hourly = demand[day]  # list of 24 values
    nonzero = [h for h in hourly if h > 0]
    if not nonzero:
        return {'peak': set(), 'off_peak': set(range(24))}
    threshold = sorted(nonzero)[int(len(nonzero) * 0.75)]  # top 25% = peak
    peak = {h for h in range(24) if hourly[h] >= threshold}
    off_peak = {h for h in range(24) if hourly[h] < threshold or hourly[h] == 0}
    return {'peak': peak, 'off_peak': off_peak}

# =============================================================================
# DEMAND-PROPORTIONAL DISTRIBUTION
# =============================================================================
def _calculate_optimal_start_distribution(demand, n, params, day_idx=None):
    """Calculate optimal shift start distribution based on demand curve.

    v12.4: Derives targets proportionally from the demand curve for each start time.
    If day_idx is provided, computes per-day targets (used during assignment).
    Otherwise computes weekly aggregate targets.
    """
    shift_hours = params['shift_hours']
    working_days_param = params.get('working_days', 6)

    if day_idx is not None:
        # Per-day distribution: score each start by how much demand it covers on THIS day
        day_name = DAYS[day_idx]
        start_demand = {}
        for start in range(24):
            hours_covered = [(start + h) % 24 for h in range(shift_hours)]
            total = sum(demand[day_name][h] for h in hours_covered)
            start_demand[start] = total
        
        total_demand = sum(start_demand.values())
        # Estimate DAs working this day from off-day quota
        daily_dem = [sum(demand[DAYS[di]]) for di in range(7)]
        total_weekly = sum(daily_dem)
        if total_weekly > 0:
            day_share = daily_dem[day_idx] / total_weekly
            n_working = max(1, round(n * working_days_param * day_share / working_days_param * (working_days_param / 7 * 7 / working_days_param)))
            n_working = min(n, max(1, n - round(n * (1 - day_share) / (7 - working_days_param)) if 7 - working_days_param > 0 else n))
        else:
            n_working = n
        # Simpler: just use n as upper bound, targets are relative anyway
        n_working = n
        
        target_distribution = {}
        for start, dem in start_demand.items():
            proportion = dem / total_demand if total_demand > 0 else 1/24
            target_distribution[start] = max(1, int(proportion * n_working))
        
        return target_distribution

    # Weekly aggregate (fallback)
    start_demand = {}
    for start in range(24):
        hours_covered = [(start + h) % 24 for h in range(shift_hours)]
        total = sum(demand[DAYS[di]][h] for di in range(7) for h in hours_covered)
        start_demand[start] = total

    total_demand = sum(start_demand.values())
    total_shifts = n * working_days_param

    target_distribution = {}
    for start, dem in start_demand.items():
        proportion = dem / total_demand if total_demand > 0 else 1/24
        target_distribution[start] = int(proportion * total_shifts)

    allocated = sum(target_distribution.values())
    if allocated < total_shifts:
        sorted_starts = sorted(start_demand.keys(), key=lambda x: (-start_demand[x], x))
        for i in range(total_shifts - allocated):
            target_distribution[sorted_starts[i % len(sorted_starts)]] += 1

    return target_distribution


def _initial_assign_fixed_start(demand, n, params, allowed_off_days):
    """
    Fixed Start Time Assignment — single-pass greedy.
    Each DA gets (start_time, off_day) assigned together considering current coverage.
    """
    daily_demand = [sum(demand[day]) for day in DAYS]
    total_weekly_demand = sum(daily_demand)
    working_days_param = params.get('working_days', 6)
    total_da_days = n * working_days_param
    flexible = params.get('flexible_day_off', False)

    # Off-day quotas (equal surplus rate)
    forced_working_days = set() if flexible else {5, 6}
    eligible_days = [di for di in range(7) if di not in forced_working_days]

    target_working = [0] * 7
    if total_weekly_demand > 0:
        if forced_working_days:
            for di in forced_working_days:
                target_working[di] = n
            remaining = total_da_days - len(forced_working_days) * n
            elig_dem = sum(daily_demand[di] for di in eligible_days)
            if elig_dem > 0 and remaining > 0:
                for di in eligible_days:
                    target_working[di] = max(0, min(n, round(remaining * daily_demand[di] / elig_dem)))
            diff = total_da_days - sum(target_working)
            sorted_e = sorted(eligible_days, key=lambda d: (-daily_demand[d], d))
            for i in range(abs(diff)):
                d = sorted_e[i % len(sorted_e)]
                if diff > 0 and target_working[d] < n:
                    target_working[d] += 1
                elif diff < 0 and target_working[d] > 0:
                    target_working[d] -= 1
        else:
            for di in range(7):
                target_working[di] = max(0, min(n, round(total_da_days * daily_demand[di] / total_weekly_demand)))
            diff = total_da_days - sum(target_working)
            sorted_a = sorted(range(7), key=lambda d: (-daily_demand[d], d))
            for i in range(abs(diff)):
                d = sorted_a[i % 7]
                if diff > 0 and target_working[d] < n:
                    target_working[d] += 1
                elif diff < 0 and target_working[d] > 0:
                    target_working[d] -= 1
    else:
        per_day = total_da_days // 7
        for di in range(7):
            target_working[di] = per_day
        for i in range(total_da_days - per_day * 7):
            target_working[i] += 1

    target_off = [max(0, n - target_working[di]) for di in range(7)]

    # Valid starts
    sh = params['shift_hours']
    night_enabled = params.get('night_shift_enabled', True)
    max_shifts = params.get('max_shifts', 0)

    valid_starts = list(range(24))
    if not night_enabled:
        valid_starts = [s for s in valid_starts if s <= 24 - sh]
    
    # Operating window constraint: restrict to valid start times if provided
    allowed_starts = params.get('valid_start_times')
    if allowed_starts is not None:
        valid_starts = [s for s in valid_starts if s in allowed_starts]
    
    # Non-operating days: force day-off on non-operating days
    non_op_days = params.get('non_operating_day_indices', [])
    if non_op_days:
        # Non-operating days must be off-days — add them to allowed_off_days
        for nod in non_op_days:
            if nod not in allowed_off_days:
                allowed_off_days.append(nod)
        allowed_off_days = sorted(set(allowed_off_days))
        # Reduce working days count by non-operating days
        effective_working_days = min(working_days_param, 7 - len(non_op_days))
        total_da_days = n * effective_working_days
        # Force target_working to 0 on non-operating days
        for nod in non_op_days:
            target_working[nod] = 0
        # Recalculate target_off
        target_off = [max(0, n - target_working[di]) for di in range(7)]

    # Compute per-start weekly demand coverage
    start_weekly_demand = {}
    for st_c in valid_starts:
        breaks_c = _place_breaks(st_c, params)
        br_c = breaks_c[0] if breaks_c else None
        br2_c = breaks_c[1] if len(breaks_c) > 1 else None
        total_c = 0
        for di_c in range(7):
            for td_c, hr_c in _covers(st_c, br_c, di_c, params, break_hr_2=br2_c):
                total_c += demand[DAYS[td_c]][hr_c]
        start_weekly_demand[st_c] = total_c

    # If max_shifts is set, select N start times using marginal demand
    if max_shifts > 0 and len(valid_starts) > max_shifts:
        selected = []
        hour_shift_count = [0] * 24
        remaining_starts = list(valid_starts)

        while len(selected) < max_shifts and remaining_starts:
            best_st = None
            best_score = -1
            for st in remaining_starts:
                breaks_s = _place_breaks(st, params)
                br_s = breaks_s[0] if breaks_s else None
                br2_s = breaks_s[1] if len(breaks_s) > 1 else None
                score = 0
                for di in range(7):
                    for td, hr in _covers(st, br_s, di, params, break_hr_2=br2_s):
                        score += demand[DAYS[td]][hr] / (1 + hour_shift_count[hr])
                if score > best_score:
                    best_score = score
                    best_st = st

            if best_st is None:
                break
            selected.append(best_st)
            remaining_starts.remove(best_st)
            breaks_s = _place_breaks(best_st, params)
            br_s = breaks_s[0] if breaks_s else None
            br2_s = breaks_s[1] if len(breaks_s) > 1 else None
            for di in range(7):
                for td, hr in _covers(best_st, br_s, di, params, break_hr_2=br2_s):
                    hour_shift_count[hr] += 1

        valid_starts = sorted(selected)

    # Greedy assignment: for each DA, pick the (start, break, off_day) that reduces gap most
    coverage = [[0] * 24 for _ in range(7)]
    shifts = [[None] * 7 for _ in range(n)]
    day_off = []
    day_off_count = [0] * 7
    start_count = {s: 0 for s in valid_starts}
    
    total_start_demand = sum(start_weekly_demand.values())
    target_start = {}
    for st in valid_starts:
        if total_start_demand > 0:
            target_start[st] = max(1, round(n * start_weekly_demand[st] / total_start_demand))
        else:
            target_start[st] = max(1, n // len(valid_starts))
    
    for i in range(n):
        best_start, best_breaks, best_off, best_score = None, None, None, -1e9
        
        for st in valid_starts:
            breaks_s = _place_breaks(st, params)
            br = breaks_s[0] if breaks_s else None
            br2 = breaks_s[1] if len(breaks_s) > 1 else None
            for off_day in allowed_off_days:
                # Check off-day quota
                if day_off_count[off_day] >= target_off[off_day] and any(
                    day_off_count[d] < target_off[d] for d in allowed_off_days
                ):
                    continue
                
                # Score: total gap reduction across all working days
                gap_score = 0
                for di in range(7):
                    if di == off_day:
                        continue
                    for td, hr in _covers(st, br, di, params, break_hr_2=br2):
                        gap_score += max(0, demand[DAYS[td]][hr] - coverage[td][hr])
                
                # Distribution bonus: prefer under-allocated start times
                target_s = target_start.get(st, 1)
                current_s = start_count.get(st, 0)
                dist_bonus = (target_s - current_s) / max(1, target_s)
                
                # Break penalty
                max_dem = max(max(demand[DAYS[di]]) for di in range(7)) or 1
                br_penalty = 0
                for di in range(7):
                    if di == off_day:
                        continue
                    overnight = _is_overnight(st, params, di)
                    if br is not None:
                        br_day = (di + 1) % 7 if (overnight and br < st) else di
                        br_penalty -= demand[DAYS[br_day]][br] / max_dem
                    if br2 is not None:
                        br2_day = (di + 1) % 7 if (overnight and br2 < st) else di
                        br_penalty -= demand[DAYS[br2_day]][br2] / max_dem
                
                score = gap_score / max_dem + dist_bonus + br_penalty * 0.1
                
                if score > best_score:
                    best_start, best_breaks, best_off, best_score = st, breaks_s, off_day, score
        
        if best_start is not None:
            day_off.append(best_off)
            day_off_count[best_off] += 1
            start_count[best_start] = start_count.get(best_start, 0) + 1
            
            br = best_breaks[0] if best_breaks else None
            br2 = best_breaks[1] if len(best_breaks) > 1 else None
            for di in range(7):
                if di == best_off:
                    shifts[i][di] = None
                else:
                    shifts[i][di] = (best_start, best_breaks)
                    for td, hr in _covers(best_start, br, di, params, break_hr_2=br2):
                        coverage[td][hr] += 1
    
    return shifts, day_off, coverage



# =============================================================================
# V12 INITIAL ASSIGNMENT (per-day variable start times)
# =============================================================================
def _initial_assign_v12(demand, n, params):
    """
    V12.4 Initial Assignment — Demand-Driven Proportional Scoring.
    
    When max_rest == min_rest (shift+rest=24h), uses FIXED START TIME mode:
    each DA gets one start time for all working days. This eliminates rest-blocking
    and ensures the scheduling system constraint (24h cycle) is met.
    
    Otherwise uses the standard per-day assignment with flexibility penalty.
    """
    flexible = params.get('flexible_day_off', False)
    max_rest = params.get('max_rest')
    min_rest = params.get('min_rest', 12)
    
    # Determine allowed off days
    if flexible:
        allowed_off_days = [0, 1, 2, 3, 4, 5, 6]  # All days
    else:
        allowed_off_days = [0, 1, 2, 3, 4]  # Sun-Thu only (Fri=5, Sat=6 must work)
    
    # Non-operating days: force as off-days
    non_op_days = params.get('non_operating_day_indices', [])
    if non_op_days:
        for nod in non_op_days:
            if nod not in allowed_off_days:
                allowed_off_days.append(nod)
        allowed_off_days = sorted(set(allowed_off_days))
    
    # ── FIXED START TIME MODE ──────────────────────────────────────────────
    # When max_rest == min_rest, each DA works the same start time every day.
    # This is much simpler: decide (start_time, off_day) per DA.
    if max_rest is not None and max_rest == min_rest:
        return _initial_assign_fixed_start(demand, n, params, allowed_off_days)
    
    # Calculate target start distribution (weekly aggregate for fallback)
    target_dist_weekly = _calculate_optimal_start_distribution(demand, n, params)
    current_dist = {h: 0 for h in range(24)}
    
    # Calculate demand-proportional off-day QUOTAS using EQUAL SURPLUS RATE
    # Goal: every day gets the same surplus percentage (DA-hours - demand) / demand
    # This means: target_working[di] = round(demand[di] * total_working_hours / total_demand / eff_hours)
    daily_demand = [sum(demand[day]) for day in DAYS]
    total_weekly_demand = sum(daily_demand)
    working_days_param = params.get('working_days', 6)
    total_da_days = n * working_days_param
    total_off_days = n * (7 - working_days_param)
    eff_hours = params['shift_hours'] - params.get('break_hours', 1)
    
    # Step 1: Determine which days can have off-days
    forced_working_days = set()
    if not flexible:
        forced_working_days = {5, 6}  # Fri, Sat must work
    
    # Non-operating days cannot be forced working days
    if non_op_days:
        forced_working_days -= set(non_op_days)
        # Reduce effective working days by non-operating days
        effective_working = min(working_days_param, 7 - len(non_op_days))
        total_da_days = n * effective_working
        total_off_days = n * (7 - effective_working)
    
    eligible_days = [di for di in range(7) if di not in forced_working_days]
    
    # Step 2: Equal surplus rate — working DAs proportional to demand
    # For forced working days, all n DAs work. Remaining DA-days go to eligible days
    # proportional to their demand share.
    target_working = [0] * 7
    target_off = [0] * 7
    
    if total_weekly_demand > 0:
        if forced_working_days:
            # Non-flexible: forced days get all n DAs, rest share remaining
            for di in forced_working_days:
                target_working[di] = n
            remaining_da_days = total_da_days - len(forced_working_days) * n
            eligible_demand = sum(daily_demand[di] for di in eligible_days)
            if eligible_demand > 0 and remaining_da_days > 0:
                for di in eligible_days:
                    share = daily_demand[di] / eligible_demand
                    target_working[di] = max(0, min(n, round(remaining_da_days * share)))
            # Adjust to hit exact total
            current_total = sum(target_working)
            diff = total_da_days - current_total
            sorted_elig = sorted(eligible_days, key=lambda d: (-daily_demand[d], d))
            for i in range(abs(diff)):
                d = sorted_elig[i % len(sorted_elig)]
                if diff > 0 and target_working[d] < n:
                    target_working[d] += 1
                elif diff < 0 and target_working[d] > 0:
                    target_working[d] -= 1
        else:
            # Flexible: all 7 days eligible, distribute proportional to demand
            for di in range(7):
                share = daily_demand[di] / total_weekly_demand
                target_working[di] = max(0, min(n, round(total_da_days * share)))
            # Adjust to hit exact total
            current_total = sum(target_working)
            diff = total_da_days - current_total
            sorted_all = sorted(range(7), key=lambda d: (-daily_demand[d], d))
            for i in range(abs(diff)):
                d = sorted_all[i % 7]
                if diff > 0 and target_working[d] < n:
                    target_working[d] += 1
                elif diff < 0 and target_working[d] > 0:
                    target_working[d] -= 1
    else:
        # No demand — equal distribution
        per_day = total_da_days // 7
        for di in range(7):
            target_working[di] = per_day
        remainder = total_da_days - per_day * 7
        for i in range(remainder):
            target_working[i] += 1
    
    # Derive off-day quotas from working targets
    for di in range(7):
        target_off[di] = max(0, n - target_working[di])
    
    # Force non-operating days: all DAs must be off on non-operating days
    if non_op_days:
        for nod in non_op_days:
            target_working[nod] = 0
            target_off[nod] = n
    
    # Assign off-days using quotas (round-robin filling)
    day_off = []
    day_off_count = [0] * 7
    for i in range(n):
        best_day = min(
            allowed_off_days,
            key=lambda d: (day_off_count[d] / max(1, target_off[d]), d)
        )
        day_off.append(best_day)
        day_off_count[best_day] += 1
    
    coverage = [[0] * 24 for _ in range(7)]
    shifts = [[None] * 7 for _ in range(n)]
    
    # v12.4: Smart day order — process days to maximize flexibility for the
    # highest-demand day. Low-demand days first, but the highest-demand day's
    # neighbors are processed just before it, so the flexibility penalty can
    # steer them away from starts that would block the high-demand day.
    daily_totals = [(di, sum(demand[DAYS[di]])) for di in range(7)]
    sorted_days = [di for di, _ in sorted(daily_totals, key=lambda x: (x[1], x[0]))]
    
    # Find the highest-demand day and its neighbors
    highest_demand_day = sorted_days[-1]
    hd_prev = (highest_demand_day - 1) % 7
    hd_next = (highest_demand_day + 1) % 7
    
    # Build order: all other days (ascending demand), then hd_prev, hd_next, then highest
    day_order = []
    deferred = {highest_demand_day, hd_prev, hd_next}
    for di in sorted_days:
        if di not in deferred:
            day_order.append(di)
    # Add neighbors of highest-demand day, lower demand first
    if sum(demand[DAYS[hd_prev]]) <= sum(demand[DAYS[hd_next]]):
        day_order.extend([hd_prev, hd_next])
    else:
        day_order.extend([hd_next, hd_prev])
    day_order.append(highest_demand_day)
    
    # Per-day distribution tracking
    day_dist = {di: {h: 0 for h in range(24)} for di in range(7)}
    
    # Precompute which days are NOT yet processed (will be processed later)
    # These are the days we need to preserve flexibility for
    days_remaining = set(day_order)  # will remove as we process
    
    for di in day_order:
        days_remaining.discard(di)
        
        # Skip non-operating days — all DAs are off
        if di in non_op_days:
            continue
        
        # v12.4: Deterministic DA ordering (no random)
        order = sorted(range(n), key=lambda i: (0 if day_off[i] == (di - 1) % 7 else 1, i))
        
        # Per-day distribution target
        target_dist = _calculate_optimal_start_distribution(demand, n, params, day_idx=di)
        current_day_dist = day_dist[di]
        
        # Precompute hour classification and max demand for this day
        hour_classes = _classify_hours(demand, DAYS[di])
        max_demand_today = max(demand[DAYS[di]]) if max(demand[DAYS[di]]) > 0 else 1
        
        # Identify unprocessed adjacent days that need flexibility
        prev_di = (di - 1) % 7
        next_di = (di + 1) % 7
        unprocessed_prev = prev_di in days_remaining
        unprocessed_next = next_di in days_remaining
        
        for i in order:
            if day_off[i] == di:
                continue
            
            # v12.4: Check rest against ALL already-assigned adjacent days
            base_valid = list(range(24))
            if not params.get('night_shift_enabled', True):
                sh = _get_shift_hours(params, di)
                max_start = 24 - sh
                base_valid = [s for s in base_valid if s <= max_start]
            
            # Operating window constraint
            allowed_starts = params.get('valid_start_times')
            if allowed_starts is not None:
                base_valid = [s for s in base_valid if s in allowed_starts]
            
            # Check rest with previous day (di-1)
            if shifts[i][prev_di] is not None:
                prev_st, prev_breaks = shifts[i][prev_di]
                prev_end_hr = _shift_end(prev_st, params, prev_di)
                prev_on = _is_overnight(prev_st, params, prev_di)
                base_valid = [s for s in base_valid if _calc_rest(prev_end_hr, prev_di, prev_on, di, s) >= params['min_rest']]
            
            # Check rest with next day (di+1)
            if shifts[i][next_di] is not None:
                next_st, next_breaks = shifts[i][next_di]
                filtered = []
                for s in base_valid:
                    s_end = _shift_end(s, params, di)
                    s_on = _is_overnight(s, params, di)
                    rest_to_next = _calc_rest(s_end, di, s_on, next_di, next_st)
                    if rest_to_next >= params['min_rest']:
                        filtered.append(s)
                base_valid = filtered
            
            valid = base_valid if base_valid else None
            if not valid:
                continue
            
            best_start, best_breaks_result, best_score = None, None, -1e9
            
            for st in valid:
                # Build residual demand (demand - current coverage) so break
                # placement adapts as DAs are assigned and coverage fills up.
                raw_demand = demand.get(DAYS[di])
                if raw_demand is not None:
                    residual = [max(0, raw_demand[h] - coverage[di][h]) for h in range(24)]
                else:
                    residual = None
                breaks_s = _place_breaks(st, params, di, residual)
                overnight = _is_overnight(st, params, di)
                br = breaks_s[0] if breaks_s else None
                br2 = breaks_s[1] if len(breaks_s) > 1 else None
                
                covered_slots = _covers(st, br, di, params, break_hr_2=br2)
                
                # v12.4: ABSOLUTE gap scoring
                gap_score = sum(
                    max(0, demand[DAYS[td]][hr] - coverage[td][hr])
                    for td, hr in covered_slots
                )
                gap_score = gap_score / max_demand_today
                
                # v12.4: Off-peak bonus
                late_bonus = 0.0
                for td, hr in covered_slots:
                    if hr in hour_classes['off_peak'] and demand[DAYS[td]][hr] > coverage[td][hr]:
                        late_bonus += max(0, demand[DAYS[td]][hr] - coverage[td][hr]) / max_demand_today * 0.1
                
                # v12.4: Relative break penalty
                break_penalty = 0.0
                if br is not None:
                    br_day = (di + 1) % 7 if (overnight and br < st) else di
                    break_penalty -= demand[DAYS[br_day]][br] / max_demand_today
                if br2 is not None:
                    br2_day = (di + 1) % 7 if (overnight and br2 < st) else di
                    break_penalty -= demand[DAYS[br2_day]][br2] / max_demand_today
                
                # v12.4: Per-day distribution scoring
                target = target_dist.get(st, 0)
                current = current_day_dist.get(st, 0)
                if target > 0:
                    dist_score = (target - current) / target
                else:
                    dist_score = -1.0 if current > 0 else 0.0
                
                # v12.4: FLEXIBILITY PENALTY — penalize starts that restrict
                # unprocessed adjacent days' valid start windows.
                flex_penalty = 0.0
                s_end = _shift_end(st, params, di)
                s_on = _is_overnight(st, params, di)
                
                if unprocessed_next and day_off[i] != next_di:
                    next_valid_count = 0
                    for ns in range(24):
                        if not params.get('night_shift_enabled', True):
                            nsh = _get_shift_hours(params, next_di)
                            if ns > 24 - nsh:
                                continue
                        if _calc_rest(s_end, di, s_on, next_di, ns) >= params['min_rest']:
                            next_valid_count += 1
                    if next_valid_count == 0:
                        flex_penalty -= 3.0
                    elif next_valid_count < 6:
                        flex_penalty -= (6 - next_valid_count) * 0.25
                
                if unprocessed_prev and day_off[i] != prev_di:
                    prev_valid_count = 0
                    for ps in range(24):
                        if not params.get('night_shift_enabled', True):
                            psh = _get_shift_hours(params, prev_di)
                            if ps > 24 - psh:
                                continue
                        p_end = _shift_end(ps, params, prev_di)
                        p_on = _is_overnight(ps, params, prev_di)
                        if _calc_rest(p_end, prev_di, p_on, di, st) >= params['min_rest']:
                            prev_valid_count += 1
                    if prev_valid_count == 0:
                        flex_penalty -= 3.0
                    elif prev_valid_count < 6:
                        flex_penalty -= (6 - prev_valid_count) * 0.25
                
                score = gap_score + late_bonus + break_penalty + dist_score + flex_penalty
                
                if score > best_score:
                    best_start, best_breaks_result, best_score = st, breaks_s, score
            
            if best_start is not None:
                shifts[i][di] = (best_start, best_breaks_result)
                current_day_dist[best_start] = current_day_dist.get(best_start, 0) + 1
                current_dist[best_start] += 1
                br = best_breaks_result[0] if best_breaks_result else None
                br2 = best_breaks_result[1] if len(best_breaks_result) > 1 else None
                for td, hr in _covers(best_start, br, di, params, break_hr_2=br2):
                    coverage[td][hr] += 1
                

        
        # (coverage is updated incrementally within the DA loop above)
    

    # ── REPAIR PASS: Unblock rest-blocked DAs ──────────────────────────────
    # When a DA has shift=None on a day that's NOT their off-day, it means
    # no valid start time existed due to 12h rest constraint with adjacent days.
    # Fix: try adjusting the adjacent day's shift to a start time compatible
    # with BOTH the adjacent day's neighbors AND the blocked day.
    max_repair_rounds = 5
    for repair_round in range(max_repair_rounds):
        repaired_any = False
        for i in range(n):
            for di in range(7):
                if shifts[i][di] is not None or day_off[i] == di:
                    continue  # not rest-blocked
                
                # DA i is rest-blocked on day di — try to fix by adjusting neighbors
                prev_di = (di - 1) % 7
                next_di = (di + 1) % 7
                
                # Try adjusting previous day's shift first (most common blocker)
                adj_days = []
                if shifts[i][prev_di] is not None:
                    adj_days.append(prev_di)
                if shifts[i][next_di] is not None:
                    adj_days.append(next_di)
                
                repaired = False
                for adj_di in adj_days:
                    old_adj_start, old_adj_breaks = shifts[i][adj_di]
                    old_adj_br = old_adj_breaks[0] if old_adj_breaks else None
                    old_adj_br2 = old_adj_breaks[1] if len(old_adj_breaks) > 1 else None
                    
                    # Find valid starts for the adjacent day considering ITS neighbors
                    adj_prev_di = (adj_di - 1) % 7
                    adj_next_di = (adj_di + 1) % 7
                    
                    # Get valid starts for adj_di respecting adj_di's own neighbors
                    # (excluding the blocked day di, since it has no shift yet)
                    adj_valid = list(range(24))
                    if not params.get('night_shift_enabled', True):
                        sh = _get_shift_hours(params, adj_di)
                        adj_valid = [s for s in adj_valid if s <= 24 - sh]
                    
                    # Rest from adj_prev_di → adj_di
                    if adj_prev_di != di and shifts[i][adj_prev_di] is not None:
                        ps, _pb = shifts[i][adj_prev_di]
                        p_end = _shift_end(ps, params, adj_prev_di)
                        p_on = _is_overnight(ps, params, adj_prev_di)
                        adj_valid = [s for s in adj_valid if _calc_rest(p_end, adj_prev_di, p_on, adj_di, s) >= params['min_rest']]
                    
                    # Rest from adj_di → adj_next_di (if adj_next_di != di and has shift)
                    if adj_next_di != di and shifts[i][adj_next_di] is not None:
                        ns, _nb = shifts[i][adj_next_di]
                        adj_valid = [s for s in adj_valid if _calc_rest(
                            _shift_end(s, params, adj_di), adj_di, _is_overnight(s, params, adj_di),
                            adj_next_di, ns
                        ) >= params['min_rest']]
                    
                    if not adj_valid:
                        continue
                    
                    # Now for each candidate adj start, check if it opens up a valid start on day di
                    best_combo = None
                    best_combo_score = -1e9
                    
                    for adj_st in adj_valid:
                        adj_end = _shift_end(adj_st, params, adj_di)
                        adj_on = _is_overnight(adj_st, params, adj_di)
                        
                        # Find valid starts for day di given this adj shift
                        di_valid = list(range(24))
                        if not params.get('night_shift_enabled', True):
                            sh = _get_shift_hours(params, di)
                            di_valid = [s for s in di_valid if s <= 24 - sh]
                        
                        # Rest constraint: adj_di → di (if adj_di is prev_di)
                        if adj_di == prev_di:
                            di_valid = [s for s in di_valid if _calc_rest(adj_end, adj_di, adj_on, di, s) >= params['min_rest']]
                        
                        # Rest constraint: di → adj_di (if adj_di is next_di)
                        if adj_di == next_di:
                            di_valid = [s for s in di_valid if _calc_rest(
                                _shift_end(s, params, di), di, _is_overnight(s, params, di),
                                adj_di, adj_st
                            ) >= params['min_rest']]
                        
                        # Also check rest with the OTHER neighbor of di (not adj_di)
                        other_di = next_di if adj_di == prev_di else prev_di
                        if shifts[i][other_di] is not None:
                            os, _ob = shifts[i][other_di]
                            if other_di == prev_di:
                                o_end = _shift_end(os, params, other_di)
                                o_on = _is_overnight(os, params, other_di)
                                di_valid = [s for s in di_valid if _calc_rest(o_end, other_di, o_on, di, s) >= params['min_rest']]
                            else:  # other_di == next_di
                                di_valid = [s for s in di_valid if _calc_rest(
                                    _shift_end(s, params, di), di, _is_overnight(s, params, di),
                                    other_di, os
                                ) >= params['min_rest']]
                        
                        if not di_valid:
                            continue
                        
                        # Score the best (adj_st, di_st) combo by total gap reduction
                        for di_st in di_valid:
                            di_breaks = _place_breaks(di_st, params, di)
                            di_br = di_breaks[0] if di_breaks else None
                            di_br2 = di_breaks[1] if len(di_breaks) > 1 else None
                            di_covered = _covers(di_st, di_br, di, params, break_hr_2=di_br2)
                            di_gap_score = sum(
                                max(0, demand[DAYS[td]][hr] - coverage[td][hr]) / max(1, demand[DAYS[td]][hr])
                                for td, hr in di_covered
                            )
                            # Also account for adj shift change cost
                            adj_breaks = _place_breaks(adj_st, params, adj_di)
                            adj_br = adj_breaks[0] if adj_breaks else None
                            adj_br2 = adj_breaks[1] if len(adj_breaks) > 1 else None
                            adj_new_covered = _covers(adj_st, adj_br, adj_di, params, break_hr_2=adj_br2)
                            adj_old_covered = _covers(old_adj_start, old_adj_br, adj_di, params, break_hr_2=old_adj_br2)
                            
                            # Net change in coverage from adj shift swap
                            adj_loss = sum(
                                max(0, demand[DAYS[td]][hr] - coverage[td][hr] + 1) / max(1, demand[DAYS[td]][hr])
                                for td, hr in adj_old_covered if (td, hr) not in set(adj_new_covered)
                            )
                            adj_gain = sum(
                                max(0, demand[DAYS[td]][hr] - coverage[td][hr]) / max(1, demand[DAYS[td]][hr])
                                for td, hr in adj_new_covered if (td, hr) not in set(adj_old_covered)
                            )
                            
                            combo_score = di_gap_score + adj_gain - adj_loss
                            if combo_score > best_combo_score:
                                best_combo_score = combo_score
                                best_combo = (adj_st, adj_breaks, di_st, di_breaks)
                            break  # take first valid di_st
                    
                    if best_combo is not None:
                        new_adj_st, new_adj_breaks, new_di_st, new_di_breaks = best_combo
                        
                        # Remove old adj coverage
                        for td, hr in _covers(old_adj_start, old_adj_br, adj_di, params, break_hr_2=old_adj_br2):
                            coverage[td][hr] -= 1
                        
                        # Apply new adj shift
                        new_adj_br = new_adj_breaks[0] if new_adj_breaks else None
                        new_adj_br2 = new_adj_breaks[1] if len(new_adj_breaks) > 1 else None
                        shifts[i][adj_di] = (new_adj_st, new_adj_breaks)
                        for td, hr in _covers(new_adj_st, new_adj_br, adj_di, params, break_hr_2=new_adj_br2):
                            coverage[td][hr] += 1
                        
                        # Apply new shift on blocked day
                        new_di_br = new_di_breaks[0] if new_di_breaks else None
                        new_di_br2 = new_di_breaks[1] if len(new_di_breaks) > 1 else None
                        shifts[i][di] = (new_di_st, new_di_breaks)
                        for td, hr in _covers(new_di_st, new_di_br, di, params, break_hr_2=new_di_br2):
                            coverage[td][hr] += 1
                        
                        repaired = True
                        repaired_any = True
                        break  # move to next blocked DA
                
                # If couldn't repair by adjusting neighbors, skip this DA/day
        
        if not repaired_any:
            break  # no more repairs possible
    
    # ── DUAL-NEIGHBOR REPAIR: For DAs still blocked, try adjusting BOTH neighbors ──
    # This handles the case where Fri overnight + Sun early = no room for Sat.
    # We try all (Fri_start, Sun_start) combos that leave a valid Sat window.
    for i in range(n):
        for di in range(7):
            if shifts[i][di] is not None or day_off[i] == di:
                continue
            
            prev_di = (di - 1) % 7
            next_di = (di + 1) % 7
            
            # Only try dual repair if BOTH neighbors have shifts
            if shifts[i][prev_di] is None or shifts[i][next_di] is None:
                continue
            
            old_prev_st, old_prev_breaks = shifts[i][prev_di]
            old_prev_br = old_prev_breaks[0] if old_prev_breaks else None
            old_prev_br2 = old_prev_breaks[1] if len(old_prev_breaks) > 1 else None
            old_next_st, old_next_breaks = shifts[i][next_di]
            old_next_br = old_next_breaks[0] if old_next_breaks else None
            old_next_br2 = old_next_breaks[1] if len(old_next_breaks) > 1 else None
            
            # Get valid starts for prev_di (respecting prev_di's own prev neighbor)
            pp_di = (prev_di - 1) % 7
            prev_valid = list(range(24))
            if not params.get('night_shift_enabled', True):
                sh = _get_shift_hours(params, prev_di)
                prev_valid = [s for s in prev_valid if s <= 24 - sh]
            if pp_di != di and shifts[i][pp_di] is not None:
                pps, _ppb = shifts[i][pp_di]
                pp_end = _shift_end(pps, params, pp_di)
                pp_on = _is_overnight(pps, params, pp_di)
                prev_valid = [s for s in prev_valid if _calc_rest(pp_end, pp_di, pp_on, prev_di, s) >= params['min_rest']]
            
            # Get valid starts for next_di (respecting next_di's own next neighbor)
            nn_di = (next_di + 1) % 7
            next_valid = list(range(24))
            if not params.get('night_shift_enabled', True):
                sh = _get_shift_hours(params, next_di)
                next_valid = [s for s in next_valid if s <= 24 - sh]
            if nn_di != di and shifts[i][nn_di] is not None:
                nns, _nnb = shifts[i][nn_di]
                next_valid = [s for s in next_valid if _calc_rest(
                    _shift_end(s, params, next_di), next_di, _is_overnight(s, params, next_di),
                    nn_di, nns
                ) >= params['min_rest']]
            
            if not prev_valid or not next_valid:
                continue
            
            # Try all (prev_start, next_start) combos to find one that opens a Sat window
            best_dual = None
            best_dual_score = -1e9
            
            for ps in prev_valid:
                p_end = _shift_end(ps, params, prev_di)
                p_on = _is_overnight(ps, params, prev_di)
                
                for ns in next_valid:
                    # Check prev→next rest (they're 2 days apart, so usually fine)
                    # Find valid starts for di given both neighbors
                    di_valid = list(range(24))
                    if not params.get('night_shift_enabled', True):
                        sh = _get_shift_hours(params, di)
                        di_valid = [s for s in di_valid if s <= 24 - sh]
                    
                    # Rest: prev_di → di
                    di_valid = [s for s in di_valid if _calc_rest(p_end, prev_di, p_on, di, s) >= params['min_rest']]
                    # Rest: di → next_di
                    di_valid = [s for s in di_valid if _calc_rest(
                        _shift_end(s, params, di), di, _is_overnight(s, params, di),
                        next_di, ns
                    ) >= params['min_rest']]
                    # Also: prev_di → next_di rest (they must be compatible)
                    if _calc_rest(p_end, prev_di, p_on, next_di, ns) < params['min_rest']:
                        # prev and next are only 2 days apart — check rest through di
                        pass  # This is fine since di is between them
                    
                    if not di_valid:
                        continue
                    
                    # Found a valid combo — score it
                    di_st = di_valid[0]  # take first valid
                    di_breaks_r = _place_breaks(di_st, params, di)
                    di_br = di_breaks_r[0] if di_breaks_r else None
                    di_br2 = di_breaks_r[1] if len(di_breaks_r) > 1 else None
                    ps_breaks = _place_breaks(ps, params, prev_di)
                    ps_br = ps_breaks[0] if ps_breaks else None
                    ps_br2 = ps_breaks[1] if len(ps_breaks) > 1 else None
                    ns_breaks = _place_breaks(ns, params, next_di)
                    ns_br = ns_breaks[0] if ns_breaks else None
                    ns_br2 = ns_breaks[1] if len(ns_breaks) > 1 else None
                    
                    # Score: gap reduction from adding di shift - cost of changing prev/next
                    di_covered = _covers(di_st, di_br, di, params, break_hr_2=di_br2)
                    di_gain = sum(max(0, demand[DAYS[td]][hr] - coverage[td][hr]) for td, hr in di_covered)
                    
                    # Cost of changing prev
                    old_prev_cov = set(_covers(old_prev_st, old_prev_br, prev_di, params, break_hr_2=old_prev_br2))
                    new_prev_cov = set(_covers(ps, ps_br, prev_di, params, break_hr_2=ps_br2))
                    prev_loss = sum(max(0, demand[DAYS[td]][hr] - coverage[td][hr] + 1) for td, hr in old_prev_cov - new_prev_cov)
                    
                    # Cost of changing next
                    old_next_cov = set(_covers(old_next_st, old_next_br, next_di, params, break_hr_2=old_next_br2))
                    new_next_cov = set(_covers(ns, ns_br, next_di, params, break_hr_2=ns_br2))
                    next_loss = sum(max(0, demand[DAYS[td]][hr] - coverage[td][hr] + 1) for td, hr in old_next_cov - new_next_cov)
                    
                    score = di_gain - prev_loss - next_loss
                    if score > best_dual_score:
                        best_dual_score = score
                        best_dual = (ps, ps_breaks, di_st, di_breaks_r, ns, ns_breaks)
            
            if best_dual is not None:
                new_ps, new_pb, new_ds, new_db, new_ns, new_nb = best_dual
                
                # Remove old coverage
                for td, hr in _covers(old_prev_st, old_prev_br, prev_di, params, break_hr_2=old_prev_br2):
                    coverage[td][hr] -= 1
                for td, hr in _covers(old_next_st, old_next_br, next_di, params, break_hr_2=old_next_br2):
                    coverage[td][hr] -= 1
                
                # Apply new shifts
                _new_ps_br = new_pb[0] if new_pb else None
                _new_ps_br2 = new_pb[1] if len(new_pb) > 1 else None
                shifts[i][prev_di] = (new_ps, new_pb)
                for td, hr in _covers(new_ps, _new_ps_br, prev_di, params, break_hr_2=_new_ps_br2):
                    coverage[td][hr] += 1
                
                _new_ds_br = new_db[0] if new_db else None
                _new_ds_br2 = new_db[1] if len(new_db) > 1 else None
                shifts[i][di] = (new_ds, new_db)
                for td, hr in _covers(new_ds, _new_ds_br, di, params, break_hr_2=_new_ds_br2):
                    coverage[td][hr] += 1
                
                _new_ns_br = new_nb[0] if new_nb else None
                _new_ns_br2 = new_nb[1] if len(new_nb) > 1 else None
                shifts[i][next_di] = (new_ns, new_nb)
                for td, hr in _covers(new_ns, _new_ns_br, next_di, params, break_hr_2=_new_ns_br2):
                    coverage[td][hr] += 1
    
    # ── OFF-DAY REDISTRIBUTION: For DAs still blocked, swap their off-day ──
    # If DA can't work Saturday (blocked), make Saturday their off-day and
    # try to assign a shift on their original off-day instead.
    for i in range(n):
        for di in range(7):
            if shifts[i][di] is not None or day_off[i] == di:
                continue
            
            # DA i is still blocked on day di — swap off-day to di
            original_off = day_off[i]
            
            # Check if we can assign a shift on the original off-day
            orig_valid = list(range(24))
            if not params.get('night_shift_enabled', True):
                sh = _get_shift_hours(params, original_off)
                orig_valid = [s for s in orig_valid if s <= 24 - sh]
            
            # Check rest with neighbors of original_off
            orig_prev = (original_off - 1) % 7
            orig_next = (original_off + 1) % 7
            
            if shifts[i][orig_prev] is not None:
                ps, _pb = shifts[i][orig_prev]
                p_end = _shift_end(ps, params, orig_prev)
                p_on = _is_overnight(ps, params, orig_prev)
                orig_valid = [s for s in orig_valid if _calc_rest(p_end, orig_prev, p_on, original_off, s) >= params['min_rest']]
            
            if shifts[i][orig_next] is not None:
                ns, _nb = shifts[i][orig_next]
                orig_valid = [s for s in orig_valid if _calc_rest(
                    _shift_end(s, params, original_off), original_off, _is_overnight(s, params, original_off),
                    orig_next, ns
                ) >= params['min_rest']]
            
            if not orig_valid:
                continue  # can't work original off-day either
            
            # Find best start on original off-day
            best_st, best_breaks_r, best_score = None, None, -1e9
            max_dem = max(demand[DAYS[original_off]]) if max(demand[DAYS[original_off]]) > 0 else 1
            for st in orig_valid:
                breaks_s = _place_breaks(st, params, original_off)
                br = breaks_s[0] if breaks_s else None
                br2 = breaks_s[1] if len(breaks_s) > 1 else None
                covered = _covers(st, br, original_off, params, break_hr_2=br2)
                score = sum(max(0, demand[DAYS[td]][hr] - coverage[td][hr]) for td, hr in covered) / max_dem
                if score > best_score:
                    best_st, best_breaks_r, best_score = st, breaks_s, score
            
            if best_st is not None:
                # Swap: make di the off-day, work on original_off
                day_off[i] = di
                br = best_breaks_r[0] if best_breaks_r else None
                br2 = best_breaks_r[1] if len(best_breaks_r) > 1 else None
                shifts[i][original_off] = (best_st, best_breaks_r)
                for td, hr in _covers(best_st, br, original_off, params, break_hr_2=br2):
                    coverage[td][hr] += 1
    
    return shifts, day_off, coverage


# =============================================================================
# OPTIMIZATION
# =============================================================================
def _optimize(shifts, demand, n, coverage, params, max_iter=50):
    best_gap = _calc_gap(demand, coverage)
    best_zeros = _count_zeros(demand, coverage)
    
    for iteration in range(max_iter):
        improved = False
        
        # Priority 1: Eliminate zero-coverage slots
        zero_slots = [(di, h, demand[DAYS[di]][h]) 
                     for di in range(7) for h in range(24) 
                     if demand[DAYS[di]][h] > 0 and coverage[di][h] == 0]
        
        if zero_slots:
            for gap_day, gap_hour, _ in zero_slots:
                # v12.4: Deterministic DA ordering by coverage contribution (ascending)
                da_order = sorted(range(n), key=lambda i: (
                    sum(1 for td, hr in _covers(shifts[i][gap_day][0],
                        shifts[i][gap_day][1][0] if shifts[i][gap_day][1] else None,
                        gap_day, params,
                        break_hr_2=shifts[i][gap_day][1][1] if len(shifts[i][gap_day][1]) > 1 else None)
                        if coverage[td][hr] > demand[DAYS[td]][hr]) if shifts[i][gap_day] else 0,
                    i
                ))
                
                for i in da_order:
                    if not shifts[i][gap_day]:
                        continue
                    
                    old_start, old_breaks = shifts[i][gap_day]
                    old_br = old_breaks[0] if old_breaks else None
                    old_br2 = old_breaks[1] if len(old_breaks) > 1 else None
                    
                    if any((td, hr) == (gap_day, gap_hour) for td, hr in _covers(old_start, old_br, gap_day, params, break_hr_2=old_br2)):
                        continue
                    
                    prev_shift = shifts[i][(gap_day - 1) % 7]
                    valid = _valid_starts(
                        gap_day,
                        _shift_end(prev_shift[0], params, (gap_day - 1) % 7) if prev_shift else None,
                        (gap_day - 1) % 7,
                        _is_overnight(prev_shift[0], params, (gap_day - 1) % 7) if prev_shift else False,
                        params
                    )
                    if not valid:
                        continue
                    
                    next_shift = shifts[i][(gap_day + 1) % 7]
                    if next_shift:
                        valid = [s for s in valid if _calc_rest(
                            _shift_end(s, params, gap_day), gap_day, _is_overnight(s, params, gap_day),
                            (gap_day + 1) % 7, next_shift[0]
                        ) >= params['min_rest']]
                    
                    if not valid:
                        continue
                    
                    for st in valid:
                        breaks_s = _place_breaks(st, params, gap_day)
                        br = breaks_s[0] if breaks_s else None
                        br2 = breaks_s[1] if len(breaks_s) > 1 else None
                        if not any((td, hr) == (gap_day, gap_hour) for td, hr in _covers(st, br, gap_day, params, break_hr_2=br2)):
                            continue
                        
                        for td, hr in _covers(old_start, old_br, gap_day, params, break_hr_2=old_br2):
                            coverage[td][hr] -= 1
                        for td, hr in _covers(st, br, gap_day, params, break_hr_2=br2):
                            coverage[td][hr] += 1
                        
                        new_zeros = _count_zeros(demand, coverage)
                        
                        if new_zeros < best_zeros:
                            shifts[i][gap_day] = (st, breaks_s)
                            best_zeros = new_zeros
                            best_gap = _calc_gap(demand, coverage)
                            improved = True
                            break
                        else:
                            for td, hr in _covers(st, br, gap_day, params, break_hr_2=br2):
                                coverage[td][hr] -= 1
                            for td, hr in _covers(old_start, old_br, gap_day, params, break_hr_2=old_br2):
                                coverage[td][hr] += 1
                        
                        if improved:
                            break
                    if improved:
                        break
                if improved:
                    break
            
            if improved:
                continue
        
        # Priority 2: Reduce gaps (demand-weighted)
        gaps = [(di, h, (demand[DAYS[di]][h] - coverage[di][h]) * demand[DAYS[di]][h]) 
                for di in range(7) for h in range(24) 
                if demand[DAYS[di]][h] > coverage[di][h]]
        
        if not gaps:
            break
        
        gaps.sort(key=lambda x: -x[2])
        
        for gap_day, gap_hour, _ in gaps[:10]:
            # v12.4: Deterministic DA ordering by coverage contribution (ascending)
            da_order = sorted(range(n), key=lambda i: (
                sum(1 for td, hr in _covers(shifts[i][gap_day][0],
                    shifts[i][gap_day][1][0] if shifts[i][gap_day][1] else None,
                    gap_day, params,
                    break_hr_2=shifts[i][gap_day][1][1] if len(shifts[i][gap_day][1]) > 1 else None)
                    if coverage[td][hr] > demand[DAYS[td]][hr]) if shifts[i][gap_day] else 0,
                i
            ))
            
            for i in da_order:
                if not shifts[i][gap_day]:
                    continue
                
                old_start, old_breaks = shifts[i][gap_day]
                old_br = old_breaks[0] if old_breaks else None
                old_br2 = old_breaks[1] if len(old_breaks) > 1 else None
                
                if any((td, hr) == (gap_day, gap_hour) for td, hr in _covers(old_start, old_br, gap_day, params, break_hr_2=old_br2)):
                    continue
                
                prev_shift = shifts[i][(gap_day - 1) % 7]
                valid = _valid_starts(
                    gap_day,
                    _shift_end(prev_shift[0], params, (gap_day - 1) % 7) if prev_shift else None,
                    (gap_day - 1) % 7,
                    _is_overnight(prev_shift[0], params, (gap_day - 1) % 7) if prev_shift else False,
                    params
                )
                if not valid:
                    continue
                
                next_shift = shifts[i][(gap_day + 1) % 7]
                if next_shift:
                    valid = [s for s in valid if _calc_rest(
                        _shift_end(s, params, gap_day), gap_day, _is_overnight(s, params, gap_day),
                        (gap_day + 1) % 7, next_shift[0]
                    ) >= params['min_rest']]
                
                if not valid:
                    continue
                
                for st in valid:
                    breaks_s = _place_breaks(st, params, gap_day)
                    br = breaks_s[0] if breaks_s else None
                    br2 = breaks_s[1] if len(breaks_s) > 1 else None
                    if not any((td, hr) == (gap_day, gap_hour) for td, hr in _covers(st, br, gap_day, params, break_hr_2=br2)):
                        continue
                    
                    for td, hr in _covers(old_start, old_br, gap_day, params, break_hr_2=old_br2):
                        coverage[td][hr] -= 1
                    for td, hr in _covers(st, br, gap_day, params, break_hr_2=br2):
                        coverage[td][hr] += 1
                    
                    new_gap = _calc_gap(demand, coverage)
                    new_zeros = _count_zeros(demand, coverage)
                    
                    if new_gap < best_gap and new_zeros <= best_zeros:
                        best_gap = new_gap
                        shifts[i][gap_day] = (st, breaks_s)
                        improved = True
                        break
                    else:
                        for td, hr in _covers(st, br, gap_day, params, break_hr_2=br2):
                            coverage[td][hr] -= 1
                        for td, hr in _covers(old_start, old_br, gap_day, params, break_hr_2=old_br2):
                            coverage[td][hr] += 1
                    
                    if improved:
                        break
                if improved:
                    break
            if improved:
                break
        
        if not improved:
            break
    
    return shifts, best_gap, coverage

def _run_store_optimization(demand, n, params, passes=3):
    """Run optimization — single path for both flexible and non-flexible modes.
    
    v12.4: Removed _generate_demand_based_configs and config iteration.
    Uses single initial assignment with quota-based off-days + optimization passes.
    """
    max_rest = params.get('max_rest')
    min_rest = params.get('min_rest', 12)
    is_fixed = max_rest is not None and max_rest == min_rest
    
    shifts, day_off, coverage = _initial_assign_v12(demand, n, params)
    
    # In fixed start mode, choose optimizer behavior
    if is_fixed:
        gap = _calc_gap(demand, coverage)
        zeros = _count_zeros(demand, coverage)
        
        fs_mode = params.get('fixed_start_optimizer', 'post_off')
        
        if fs_mode == 'strict':
            # No optimization — return as-is
            return gap, shifts, day_off, zeros
        
        elif fs_mode == 'post_off':
            # Only change start on the day after each DA's weekly off
            # If max_shifts is set, restrict post-off candidates to the same N starts
            max_shifts_param = params.get('max_shifts', 0)
            allowed_starts = None
            if max_shifts_param > 0:
                # Use the ACTUAL starts from the initial assignment (not recomputed)
                actual_starts = set()
                for i in range(n):
                    for di in range(7):
                        if shifts[i][di]:
                            actual_starts.add(shifts[i][di][0])
                allowed_starts = actual_starts
            
            improved = True
            for _round in range(3):
                if not improved:
                    break
                improved = False
                for i in range(n):
                    off_di = day_off[i]
                    post_off_di = (off_di + 1) % 7
                    
                    if shifts[i][post_off_di] is None:
                        continue
                    
                    old_st, old_breaks_r = shifts[i][post_off_di]
                    old_br = old_breaks_r[0] if old_breaks_r else None
                    old_br2 = old_breaks_r[1] if len(old_breaks_r) > 1 else None
                    next_di = (post_off_di + 1) % 7
                    
                    candidates = list(range(24))
                    if not params.get('night_shift_enabled', True):
                        sh = _get_shift_hours(params, post_off_di)
                        candidates = [s for s in candidates if s <= 24 - sh]
                    
                    # Restrict to allowed starts if max_shifts is set
                    if allowed_starts is not None:
                        candidates = [s for s in candidates if s in allowed_starts]
                    
                    if shifts[i][next_di] is not None:
                        next_st = shifts[i][next_di][0]
                        candidates = [s for s in candidates if _calc_rest(
                            _shift_end(s, params, post_off_di), post_off_di,
                            _is_overnight(s, params, post_off_di),
                            next_di, next_st
                        ) >= params['min_rest']]
                    
                    if not candidates:
                        continue
                    
                    old_covered = _covers(old_st, old_br, post_off_di, params, break_hr_2=old_br2)
                    for td, hr in old_covered:
                        coverage[td][hr] -= 1
                    
                    best_new_st, best_new_breaks, best_new_gap = None, None, gap
                    for st in candidates:
                        breaks_s = _place_breaks(st, params, post_off_di)
                        br = breaks_s[0] if breaks_s else None
                        br2 = breaks_s[1] if len(breaks_s) > 1 else None
                        new_covered = _covers(st, br, post_off_di, params, break_hr_2=br2)
                        for td, hr in new_covered:
                            coverage[td][hr] += 1
                        new_gap = _calc_gap(demand, coverage)
                        if new_gap < best_new_gap:
                            best_new_gap = new_gap
                            best_new_st, best_new_breaks = st, breaks_s
                        for td, hr in new_covered:
                            coverage[td][hr] -= 1
                    
                    if best_new_st is not None and best_new_gap < gap:
                        best_br = best_new_breaks[0] if best_new_breaks else None
                        best_br2 = best_new_breaks[1] if len(best_new_breaks) > 1 else None
                        shifts[i][post_off_di] = (best_new_st, best_new_breaks)
                        for td, hr in _covers(best_new_st, best_br, post_off_di, params, break_hr_2=best_br2):
                            coverage[td][hr] += 1
                        gap = best_new_gap
                        improved = True
                    else:
                        for td, hr in old_covered:
                            coverage[td][hr] += 1
            
            zeros = _count_zeros(demand, coverage)
            return gap, shifts, day_off, zeros
        
        # else fs_mode == 'flexible': fall through to normal optimizer below
    
    for p in range(passes):
        shifts, gap, coverage = _optimize(shifts, demand, n, coverage, params, max_iter=100)
        zeros = _count_zeros(demand, coverage)
        if gap == 0 and zeros == 0:
            break
    
    # Extra passes if still have zeros
    if zeros > 0:
        for _ in range(3):
            shifts, gap, coverage = _optimize(shifts, demand, n, coverage, params, max_iter=100)
            zeros = _count_zeros(demand, coverage)
            if zeros == 0:
                break
    
    return gap, shifts, day_off, zeros

def find_optimal_shifts(demand, n, params, max_search=24, gap_threshold_pct=5):
    """
    Find the minimum number of shifts that achieves near-optimal gap.
    
    Sweeps from 1 to max_search shifts, runs the engine for each,
    and returns the smallest N where gap is within threshold_pct of the best gap.
    
    Returns: dict with 'optimal_n', 'optimal_gap', 'best_gap', 'best_n', 'sweep' (list of results)
    """
    sweep = []
    best_gap = float('inf')
    best_n = 0
    
    for ms in range(1, max_search + 1):
        test_params = params.copy()
        test_params['max_shifts'] = ms
        gap, shifts, day_off, zeros = _run_store_optimization(demand, n, test_params)
        starts = set()
        for i in range(n):
            for di in range(7):
                if shifts[i][di]:
                    starts.add(shifts[i][di][0])
        sweep.append({'n': ms, 'gap': gap, 'zeros': zeros, 'actual_starts': len(starts)})
        if gap < best_gap:
            best_gap = gap
            best_n = ms
    
    # Find the smallest N where gap is within threshold of best
    threshold = best_gap * (1 + gap_threshold_pct / 100)
    optimal_n = best_n
    for entry in sweep:
        if entry['gap'] <= threshold:
            optimal_n = entry['n']
            break
    
    return {
        'optimal_n': optimal_n,
        'optimal_gap': sweep[optimal_n - 1]['gap'],
        'best_gap': best_gap,
        'best_n': best_n,
        'sweep': sweep
    }


# =============================================================================
# MAIN ASSIGNMENT
# =============================================================================
def assign_shifts(da_list_df, demand_df, carryover_df=None, params=None):
    if params is None:
        params = get_params()
    
    flexible = params.get('flexible_day_off', False)
    is_fixed = params.get('max_rest') is not None and params.get('max_rest') == params.get('min_rest', 12)
    mode = "flexible day-off" if flexible else "Fri/Sat enforced"
    print(f"Assigning shifts (v12.4, {mode}, fixed_start={is_fixed}, "
          f"shift={params.get('shift_hours')}, rest={params.get('min_rest')}, "
          f"max_rest={params.get('max_rest')}, max_shifts={params.get('max_shifts', 0)}, "
          f"night={params.get('night_shift_enabled')})...")
    
    all_schedules = []
    stores = da_list_df['Store'].unique()
    
    for store in stores:
        store_das = da_list_df[da_list_df['Store'] == store].reset_index(drop=True)
        store_demand = demand_df[demand_df['Store'] == store]
        n = len(store_das)
        
        if n == 0:
            continue
        
        demand = build_demand_matrix(store_demand)
        
        print(f"   Store {store}: {n} DAs")
        
        best_gap, best_shifts, best_day_off, zeros = _run_store_optimization(demand, n, params)
        
        print(f"      Gap: {best_gap}, Zero-coverage slots: {zeros}")
        
        # Enforce non-operating days as day-off
        non_op_days = set(params.get('non_operating_day_indices', []))
        
        for i, da_row in store_das.iterrows():
            da_id = da_row['DA_ID']
            
            for di in range(7):
                day = DAYS[di]
                shift = best_shifts[i][di]
                
                # Force day-off on non-operating days
                if di in non_op_days:
                    shift = None
                
                if shift is None:
                    all_schedules.append({
                        'DA_ID': da_id, 'Store': store, 'DSP': da_row['DSP'],
                        'Day': day, 'Day_Index': di,
                        'Shift_Start': None, 'Shift_End': None,
                        'Break_Hour': None, 'Break_Hour_2': None,
                        'Is_Day_Off': True
                    })
                else:
                    st, breaks = shift
                    br = breaks[0] if breaks else None
                    br2 = breaks[1] if len(breaks) > 1 else None
                    all_schedules.append({
                        'DA_ID': da_id, 'Store': store, 'DSP': da_row['DSP'],
                        'Day': day, 'Day_Index': di,
                        'Shift_Start': st, 'Shift_End': _shift_end(st, params, di),
                        'Break_Hour': br, 'Break_Hour_2': br2,
                        'Is_Day_Off': False
                    })
    
    return pd.DataFrame(all_schedules)


# =============================================================================
# OUTPUT GENERATION
# =============================================================================
def generate_hourly_roster(shifts_df, demand_df, params=None):
    if params is None:
        params = get_params()
    
    skip_sunday_overnight = params.get('skip_sunday_overnight', False)
    carryover_mode = params.get('carryover_mode', 'auto')
    sunday_carryover_das = params.get('sunday_carryover_das', 0)
    carryover_excel_data = params.get('carryover_excel_data', [])  # List of {DA_ID, Store, Sat_Shift_End}
    
    records = []
    
    for store in shifts_df['Store'].unique():
        store_shifts = shifts_df[shifts_df['Store'] == store]
        store_demand = demand_df[demand_df['Store'] == store]
        all_das = sorted(store_shifts['DA_ID'].unique())
        
        # Get store-specific carryover DAs from Excel data
        store_carryover_das = [c for c in carryover_excel_data if c.get('Store') == store]
        
        for day_idx, day in enumerate(DAYS):
            day_shifts = store_shifts[store_shifts['Day'] == day]
            prev_day = DAYS[(day_idx - 1) % 7]
            prev_shifts = store_shifts[store_shifts['Day'] == prev_day]
            
            # Determine carryover handling for Sunday
            is_sunday = (day == 'Sun')
            use_manual_carryover = is_sunday and carryover_mode == 'manual'
            use_excel_carryover = is_sunday and carryover_mode == 'excel'
            skip_prev_overnight = skip_sunday_overnight and is_sunday
            
            for slot in range(24):
                demand_row = store_demand[
                    (store_demand['Day'].str[:3] == day) &
                    (store_demand['Slot'] == slot)
                ]
                required = int(demand_row['DA Required'].values[0]) if len(demand_row) > 0 and pd.notna(demand_row['DA Required'].values[0]) else 0
                
                orders = 0
                if len(demand_row) > 0:
                    # Try multiple column names for orders, handle duplicate columns
                    for col_name in ['Final Orders', 'Hourly Orders', 'Orders']:
                        if col_name in demand_row.columns:
                            orders_val = demand_row[col_name].values
                            # Handle duplicate columns - take first value
                            if hasattr(orders_val, '__len__') and len(orders_val) > 0:
                                first_val = orders_val.flat[0] if hasattr(orders_val, 'flat') else orders_val[0]
                                orders = int(first_val) if pd.notna(first_val) else 0
                            break
                
                da_status = {da: '-' for da in all_das}
                rostered = 0
                
                # Handle manual carryover for Sunday early morning (00:00-05:00)
                if use_manual_carryover and slot < 5 and sunday_carryover_das > 0:
                    # Add manual carryover DAs directly to rostered count
                    rostered += sunday_carryover_das
                # Handle Excel carryover for Sunday early morning - individual DAs
                elif use_excel_carryover and is_sunday:
                    for carryover_da in store_carryover_das:
                        sat_end = carryover_da.get('Sat_Shift_End', 5)
                        if slot < sat_end:
                            # This DA is still working from Saturday night
                            rostered += 1
                # Only count previous day's overnight carryover if not skipping and not using manual/excel
                if not skip_prev_overnight and not use_manual_carryover and not use_excel_carryover:
                    for _, shift in prev_shifts.iterrows():
                        if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
                            continue
                        
                        da = shift['DA_ID']
                        start = int(shift['Shift_Start'])
                        end = int(shift['Shift_End'])
                        brk = int(shift['Break_Hour']) if pd.notna(shift['Break_Hour']) else -1
                        brk2 = int(shift['Break_Hour_2']) if 'Break_Hour_2' in shift.index and pd.notna(shift.get('Break_Hour_2')) else -1
                        
                        if not _is_overnight(start, params):
                            continue
                        
                        if slot < end:
                            if (brk >= 0 and slot == brk) or (brk2 >= 0 and slot == brk2):
                                da_status[da] = 'B'
                            else:
                                da_status[da] = '1'
                                rostered += 1
                
                for _, shift in day_shifts.iterrows():
                    da = shift['DA_ID']
                    
                    if da_status[da] in ['1', 'B']:
                        continue
                    
                    if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
                        da_status[da] = 'OFF'
                        continue
                    
                    start = int(shift['Shift_Start'])
                    end = int(shift['Shift_End'])
                    brk = int(shift['Break_Hour']) if pd.notna(shift['Break_Hour']) else -1
                    brk2 = int(shift['Break_Hour_2']) if 'Break_Hour_2' in shift.index and pd.notna(shift.get('Break_Hour_2')) else -1
                    overnight = _is_overnight(start, params)
                    
                    if overnight:
                        working = slot >= start
                    else:
                        if end == 0:
                            working = slot >= start
                        else:
                            working = start <= slot < end
                    
                    if working:
                        if (brk >= 0 and slot == brk) or (brk2 >= 0 and slot == brk2):
                            da_status[da] = 'B'
                        else:
                            da_status[da] = '1'
                            rostered += 1
                
                record = {
                    'Store': store, 'Day': day, 'Slot': slot,
                    'Orders': orders,
                    'Required': required, 'Rostered': rostered,
                    'Diff': rostered - required
                }
                record.update(da_status)
                records.append(record)
    
    return pd.DataFrame(records)

def generate_da_summary(shifts_df, params=None):
    if params is None:
        params = get_params()
    
    summaries = []
    
    for da_id in shifts_df['DA_ID'].unique():
        da_shifts = shifts_df[shifts_df['DA_ID'] == da_id].sort_values('Day_Index')
        first = da_shifts.iloc[0]
        
        working = len(da_shifts[~da_shifts['Is_Day_Off']])
        off_days = da_shifts[da_shifts['Is_Day_Off']]['Day'].tolist()
        
        summary = {
            'DA_ID': da_id,
            'Store': first['Store'],
            'DSP': first['DSP'],
            'Working_Days': working,
            'Days_Off': ', '.join(off_days) if off_days else 'None'
        }
        
        issues = []
        prev_end, prev_day_idx, prev_overnight = None, None, False
        
        for _, shift in da_shifts.iterrows():
            day = shift['Day']
            day_idx = shift['Day_Index']
            
            if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
                summary[f'{day}_Shift'] = 'OFF'
                continue
            
            start = int(shift['Shift_Start'])
            end = int(shift['Shift_End'])
            summary[f'{day}_Shift'] = f"{start:02d}:00-{end:02d}:00"
            
            if prev_end is not None:
                rest = _calc_rest(prev_end, prev_day_idx, prev_overnight, day_idx, start)
                if rest < params['min_rest']:
                    issues.append(f"{day}:{rest}h rest")
            
            prev_end = end
            prev_day_idx = day_idx
            prev_overnight = _is_overnight(start, params, day_idx)
        
        summary['Weekly_Hours'] = params['effective_hours'] * working
        summary['Status'] = '✓ Valid' if not issues else '⚠️ ' + '; '.join(issues)
        summaries.append(summary)
    
    return pd.DataFrame(summaries)

def generate_carryover(shifts_df, params=None):
    if params is None:
        params = get_params()
    
    saturday = shifts_df[shifts_df['Day'] == 'Sat']
    records = []
    
    for _, shift in saturday.iterrows():
        if shift['Is_Day_Off'] or pd.isna(shift['Shift_Start']):
            records.append({
                'DA_ID': shift['DA_ID'], 'Store': shift['Store'],
                'DSP': shift['DSP'], 'Sat_Shift_End': None, 'Is_Overnight': False
            })
        else:
            start = int(shift['Shift_Start'])
            end = int(shift['Shift_End'])
            records.append({
                'DA_ID': shift['DA_ID'], 'Store': shift['Store'],
                'DSP': shift['DSP'], 'Sat_Shift_End': end,
                'Is_Overnight': _is_overnight(start, params)
            })
    
    return pd.DataFrame(records)

# =============================================================================
# MAIN ENTRY POINT
# =============================================================================
def run_roster(input_file, output_file=None, store=None, custom_params=None):
    print("=" * 60)
    print("DA ROSTERING ENGINE v12.4")
    print("=" * 60)
    
    params = get_params(custom_params)
    flexible = params.get('flexible_day_off', False)
    
    print(f"Parameters: {params['shift_hours']}h shift, {params['break_hours']}h break")
    print(f"Night shift: {'Enabled' if params['night_shift_enabled'] else 'Disabled'}")
    print(f"Day-off mode: {'Flexible (all days)' if flexible else 'Restricted (Sun-Thu only)'}")
    print()
    
    demand_df = load_demand(input_file, store)
    das_df = load_available_das(input_file, store)
    carryover_df = load_carryover(input_file)
    
    da_list = build_da_list(das_df)
    
    shifts_df = assign_shifts(da_list, demand_df, carryover_df, params)
    roster_df = generate_hourly_roster(shifts_df, demand_df, params)
    summary_df = generate_da_summary(shifts_df, params)
    carryover_out = generate_carryover(shifts_df, params)
    
    if output_file:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out_file = output_file.replace('.xlsx', f'_{timestamp}.xlsx')
        
        with pd.ExcelWriter(out_file, engine='openpyxl') as writer:
            roster_df.to_excel(writer, sheet_name='Hourly_Roster', index=False)
            summary_df.to_excel(writer, sheet_name='DA_Summary', index=False)
            carryover_out.to_excel(writer, sheet_name='Next_Week_Carryover', index=False)
        
        print(f"\nOutput saved to: {out_file}")
    
    return shifts_df, roster_df, summary_df

if __name__ == '__main__':
    print("="*60)
    print("ROSTER ENGINE V12.4 TEST")
    print("="*60)
    
    FILE = 'KSA_OTR_Capacity_planning_WK06_v2.xlsx'
    
    # Test both modes
    for flexible in [False, True]:
        mode = "FLEXIBLE" if flexible else "RESTRICTED"
        print(f"\n{'='*60}")
        print(f"MODE: {mode} DAY-OFF")
        print("="*60)
        
        demand_df = load_demand(FILE, store='QRA4')
        das_df = load_available_das(FILE, store='QRA4')
        da_list = build_da_list(das_df)
        
        params = get_params({
            'night_shift_enabled': True,
            'flexible_day_off': flexible
        })
        
        shifts = assign_shifts(da_list, demand_df, None, params)
        roster = generate_hourly_roster(shifts, demand_df, params)
        summary = generate_da_summary(shifts, params)
        
        # Results
        fri_sat_off = summary[(summary['Days_Off'].str.contains('Fri')) | (summary['Days_Off'].str.contains('Sat'))]
        total_gap = abs(roster[roster['Diff'] < 0]['Diff'].sum())
        
        print(f"\nResults:")
        print(f"  Total Gap: {total_gap}")
        print(f"  DAs with Fri/Sat off: {len(fri_sat_off)}")
