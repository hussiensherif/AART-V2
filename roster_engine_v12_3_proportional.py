"""
DA ROSTERING ENGINE v12.3 — Proportional Scoring
==================================================
Based on v12.2 with proportional gap scoring to distribute DAs
following the demand curve shape instead of concentrating at peaks.

KEY CHANGE from v12.2:
- Scoring uses gap/demand (proportional) instead of gap*demand (absolute)
- Stronger distribution enforcement to follow the slot curve
- Result: DAs distributed proportionally even with DA shortage
"""

import pandas as pd
import numpy as np
import random
from datetime import datetime

DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

DEFAULT_PARAMS = {
    'shift_hours': 10,
    'break_hours': 1,
    'max_continuous': 5,
    'min_rest': 12,
    'working_days': 6,
    'night_shift_enabled': True,
    'flexible_day_off': False  # NEW: Toggle for V10.1 vs V10.2 behavior
}

def get_params(custom_params=None):
    params = DEFAULT_PARAMS.copy()
    if custom_params:
        params.update(custom_params)
    params['effective_hours'] = params['shift_hours'] - params['break_hours']
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
    """Return 2 best break positions: at max_continuous and max_continuous-1."""
    mc = params['max_continuous']
    sh = _get_shift_hours(params, day_idx)
    # Only return positions at mc and mc-1 (fast, like original)
    positions = []
    for pos in [mc, mc - 1]:
        if pos < 1 or pos >= sh:
            continue
        hours_after = sh - pos - 1
        if hours_after >= 0 and pos <= mc and hours_after <= mc:
            positions.append((start + pos) % 24)
    return positions if positions else [(start + mc) % 24]

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
    
    return valid if valid else None

def _covers(start, break_hr, day_idx, params):
    coverage = []
    overnight = _is_overnight(start, params, day_idx)
    sh = _get_shift_hours(params, day_idx)
    
    for h in range(sh):
        hour = (start + h) % 24
        if break_hr is not None and break_hr >= 0 and hour == break_hr:
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
                st, br = shifts[i][di]
                for td, hr in _covers(st, br, di, params):
                    cov[td][hr] += 1
    return cov

def _calc_gap(demand, coverage):
    return sum(max(0, demand[DAYS[di]][h] - coverage[di][h]) for di in range(7) for h in range(24))

def _count_zeros(demand, coverage):
    return sum(1 for di in range(7) for h in range(24) 
               if demand[DAYS[di]][h] > 0 and coverage[di][h] == 0)

# =============================================================================
# DEMAND-BASED DAY-OFF CONFIG (for flexible_day_off=True mode)
# =============================================================================
def _generate_demand_based_configs(demand):
    """Generate day-off configs based on demand (low demand days get more offs)."""
    daily_demand = [sum(demand[day]) for day in DAYS]
    max_demand = max(daily_demand) if max(daily_demand) > 0 else 1
    min_demand = min(daily_demand)
    
    day_off_weights = []
    for d in daily_demand:
        if max_demand == min_demand:
            weight = 12
        else:
            normalized = (max_demand - d) / (max_demand - min_demand)
            weight = 5 + normalized * 15
        day_off_weights.append(max(1, int(weight)))
    
    configs = [
        day_off_weights,
        [max(1, int(w * 1.2)) for w in day_off_weights],
        [max(1, int(w * 0.8)) for w in day_off_weights],
        [12] * 7,
        [max(1, (day_off_weights[i] + day_off_weights[(i-1)%7] + day_off_weights[(i+1)%7]) // 3) for i in range(7)],
    ]
    return configs

# =============================================================================
# DEMAND-PROPORTIONAL DISTRIBUTION
# =============================================================================
def _calculate_optimal_start_distribution(demand, n, params):
    """Calculate optimal shift start distribution based on demand curve."""
    shift_hours = params['shift_hours']
    
    start_demand = {}
    for start in range(24):
        hours_covered = [(start + h) % 24 for h in range(shift_hours)]
        total = sum(demand[DAYS[di]][h] for di in range(7) for h in hours_covered)
        start_demand[start] = total
    
    total_demand = sum(start_demand.values())
    total_shifts = n * 6
    
    target_distribution = {}
    for start, dem in start_demand.items():
        proportion = dem / total_demand if total_demand > 0 else 1/24
        target_distribution[start] = int(proportion * total_shifts)
    
    allocated = sum(target_distribution.values())
    if allocated < total_shifts:
        # V12.3: Round-robin remainder to maintain proportionality
        sorted_starts = sorted(start_demand.keys(), key=lambda x: (-start_demand[x], x))
        for i in range(total_shifts - allocated):
            target_distribution[sorted_starts[i % len(sorted_starts)]] += 1
    
    return target_distribution

# =============================================================================
# V12 INITIAL ASSIGNMENT
# =============================================================================
def _initial_assign_v12(demand, n, params, day_off_config=None):
    """
    V12 Initial Assignment with toggle support:
    - flexible_day_off=False: Sun-Thu only for day-off, but DEMAND-BASED distribution
    - flexible_day_off=True: All days allowed with demand-based distribution
    """
    flexible = params.get('flexible_day_off', False)
    
    # Determine allowed off days
    if flexible:
        allowed_off_days = [0, 1, 2, 3, 4, 5, 6]  # All days
    else:
        allowed_off_days = [0, 1, 2, 3, 4]  # Sun-Thu only (Fri=5, Sat=6 must work)
    
    # Calculate target start distribution
    target_dist = _calculate_optimal_start_distribution(demand, n, params)
    current_dist = {h: 0 for h in range(24)}
    
    # Calculate demand-proportional off-day QUOTAS
    # Step 1: How many DA-days total? n × working_days_per_week
    # Step 2: Each day gets DA-days proportional to its demand share
    # Step 3: Off-days per day = n - working_DAs_for_that_day
    daily_demand = [sum(demand[day]) for day in DAYS]
    total_weekly_demand = sum(daily_demand)
    working_days_param = params.get('working_days', 6)
    total_da_days = n * working_days_param  # total working DA-days available
    
    # Calculate target working DAs per day (proportional to demand)
    target_working = [0] * 7
    if total_weekly_demand > 0:
        for di in range(7):
            if di in allowed_off_days or (flexible and di in allowed_off_days):
                target_working[di] = round(total_da_days * daily_demand[di] / total_weekly_demand)
            else:
                # Fri/Sat forced working when not flexible — all DAs work
                target_working[di] = n
    else:
        # No demand data — equal distribution
        for di in range(7):
            target_working[di] = total_da_days // 7
    
    # Clamp: no day can have more working DAs than total DAs
    for di in range(7):
        target_working[di] = min(target_working[di], n)
        target_working[di] = max(target_working[di], 0)
    
    # Adjust so total working DA-days = n * working_days_param
    current_total = sum(target_working)
    diff = total_da_days - current_total
    if diff > 0:
        # Need more working DA-days — add to highest demand days
        sorted_days = sorted(range(7), key=lambda d: -daily_demand[d])
        for i in range(diff):
            d = sorted_days[i % len(sorted_days)]
            if target_working[d] < n:
                target_working[d] += 1
    elif diff < 0:
        # Need fewer working DA-days — remove from lowest demand days
        sorted_days = sorted(range(7), key=lambda d: daily_demand[d])
        for i in range(abs(diff)):
            d = sorted_days[i % len(sorted_days)]
            if target_working[d] > 0:
                target_working[d] -= 1
    
    # Off-day quotas: how many DAs should be OFF each day
    target_off = [n - target_working[di] for di in range(7)]
    # Non-allowed off days get 0 quota
    for di in range(7):
        if di not in allowed_off_days:
            target_off[di] = 0
    
    # Assign off-days using quotas (round-robin filling)
    day_off = []
    day_off_count = [0] * 7
    
    if day_off_config:
        # Use provided config (for flexible mode optimization)
        for i in range(n):
            best_day = min(allowed_off_days, key=lambda d: day_off_count[d] / max(1, day_off_config[d]))
            day_off.append(best_day)
            day_off_count[best_day] += 1
    else:
        # Fill off-day quotas proportionally
        for i in range(n):
            # Pick the allowed day furthest below its off-day quota
            best_day = min(
                allowed_off_days,
                key=lambda d: (day_off_count[d] / max(1, target_off[d]), d)
            )
            day_off.append(best_day)
            day_off_count[best_day] += 1
    
    coverage = [[0] * 24 for _ in range(7)]
    prev_end = [None] * n
    prev_day = [None] * n
    prev_overnight = [False] * n
    shifts = [[None] * 7 for _ in range(n)]
    
    for di in range(7):
        order = sorted(range(n), key=lambda i: (0 if day_off[i] == (di - 1) % 7 else 1, random.random()))
        
        for i in order:
            if day_off[i] == di:
                continue
            
            valid = _valid_starts(di, prev_end[i], prev_day[i], prev_overnight[i], params)
            if not valid:
                continue
            
            best_start, best_break, best_score = None, None, -1e9
            
            # Check if night shift is disabled
            night_shift_off = not params.get('night_shift_enabled', True)
            sh_for_day = _get_shift_hours(params, di)
            max_start = 24 - sh_for_day if night_shift_off else 23
            
            for st in valid:
                # V12.2: Get valid break options
                valid_breaks = _valid_breaks(st, params, di)
                
                # V12.2: SMART BREAK SELECTION
                # Check if this shift covers BOTH dawn (2-8) AND late peak (10-22)
                shift_hours = set()
                overnight = _is_overnight(st, params, di)
                for h in range(sh_for_day):
                    hour = (st + h) % 24
                    if overnight and hour < st:
                        shift_hours.add(hour)
                    else:
                        shift_hours.add(hour)
                
                dawn_hours = set([2, 3, 4, 5, 6, 7, 8])
                late_peak_hours = set([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22])
                critical_late_hours = set([20, 21, 22, 23])
                
                covers_dawn = bool(shift_hours & dawn_hours)
                covers_late_peak = bool(shift_hours & late_peak_hours)
                
                # If shift covers BOTH, prioritize dawn breaks
                if covers_dawn and covers_late_peak:
                    dawn_breaks = [br for br in valid_breaks if br in dawn_hours]
                    if dawn_breaks:
                        valid_breaks = dawn_breaks
                
                for br in valid_breaks:
                    # V12.3: PURE DEMAND-DRIVEN scoring
                    # Score = sum of raw demand values at covered hours
                    # No gap weighting, no bonuses, no penalties
                    # This makes DAs follow the demand curve exactly
                    
                    score = 0
                    for td, hr in _covers(st, br, di, params):
                        score += demand[DAYS[td]][hr]
                    
                    # Distribution enforcement: hard cap
                    # If this start time already has enough DAs, heavily penalize
                    if current_dist[st] >= target_dist[st]:
                        score -= 100000  # effectively blocks over-allocation
                    
                    if score > best_score:
                        best_start, best_break, best_score = st, br, score
            
            if best_start is not None:
                shifts[i][di] = (best_start, best_break)
                current_dist[best_start] += 1
                for td, hr in _covers(best_start, best_break, di, params):
                    coverage[td][hr] += 1
                
                prev_end[i] = _shift_end(best_start, params, di)
                prev_day[i] = di
                prev_overnight[i] = _is_overnight(best_start, params, di)
    
    # ── Wrap-around rest check: Saturday overnight → Sunday ──
    # The main loop processes Sun(0)→Sat(6), so Saturday overnight shifts
    # may violate rest with already-assigned Sunday shifts.
    min_rest = params.get('min_rest', 12)
    for i in range(n):
        if shifts[i][6] is not None and shifts[i][0] is not None:
            sat_start, sat_break = shifts[i][6]
            sun_start, sun_break = shifts[i][0]
            sat_end = _shift_end(sat_start, params, 6)
            sat_overnight = _is_overnight(sat_start, params, 6)
            rest = _calc_rest(sat_end, 6, sat_overnight, 0, sun_start)
            if rest < min_rest:
                # Remove Sunday shift — Saturday was assigned later and takes priority
                for td, hr in _covers(sun_start, sun_break, 0, params):
                    coverage[td][hr] = max(0, coverage[td][hr] - 1)
                shifts[i][0] = None

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
                da_order = list(range(n))
                random.shuffle(da_order)
                
                for i in da_order:
                    if not shifts[i][gap_day]:
                        continue
                    
                    old_start, old_break = shifts[i][gap_day]
                    
                    if any((td, hr) == (gap_day, gap_hour) for td, hr in _covers(old_start, old_break, gap_day, params)):
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
                        for br in _valid_breaks(st, params, gap_day):
                            if not any((td, hr) == (gap_day, gap_hour) for td, hr in _covers(st, br, gap_day, params)):
                                continue
                            
                            for td, hr in _covers(old_start, old_break, gap_day, params):
                                coverage[td][hr] -= 1
                            for td, hr in _covers(st, br, gap_day, params):
                                coverage[td][hr] += 1
                            
                            new_zeros = _count_zeros(demand, coverage)
                            
                            if new_zeros < best_zeros:
                                shifts[i][gap_day] = (st, br)
                                best_zeros = new_zeros
                                best_gap = _calc_gap(demand, coverage)
                                improved = True
                                break
                            else:
                                for td, hr in _covers(st, br, gap_day, params):
                                    coverage[td][hr] -= 1
                                for td, hr in _covers(old_start, old_break, gap_day, params):
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
            da_order = list(range(n))
            random.shuffle(da_order)
            
            for i in da_order:
                if not shifts[i][gap_day]:
                    continue
                
                old_start, old_break = shifts[i][gap_day]
                
                if any((td, hr) == (gap_day, gap_hour) for td, hr in _covers(old_start, old_break, gap_day, params)):
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
                    for br in _valid_breaks(st, params, gap_day):
                        if not any((td, hr) == (gap_day, gap_hour) for td, hr in _covers(st, br, gap_day, params)):
                            continue
                        
                        for td, hr in _covers(old_start, old_break, gap_day, params):
                            coverage[td][hr] -= 1
                        for td, hr in _covers(st, br, gap_day, params):
                            coverage[td][hr] += 1
                        
                        new_gap = _calc_gap(demand, coverage)
                        new_zeros = _count_zeros(demand, coverage)
                        
                        if new_gap < best_gap and new_zeros <= best_zeros:
                            best_gap = new_gap
                            shifts[i][gap_day] = (st, br)
                            improved = True
                            break
                        else:
                            for td, hr in _covers(st, br, gap_day, params):
                                coverage[td][hr] -= 1
                            for td, hr in _covers(old_start, old_break, gap_day, params):
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
    """Run optimization with appropriate config based on flexible_day_off setting.
    
    V12.2.1: Increased passes from 2 to 3 and max_iter from 30 to 100
    for better gap reduction, especially in night-shift-OFF mode.
    """
    flexible = params.get('flexible_day_off', False)
    
    if flexible:
        # V10.2 mode: Try multiple demand-based configs (reduced for speed)
        configs = _generate_demand_based_configs(demand)[:3]  # Only try first 3 configs
        
        best_gap = float('inf')
        best_shifts, best_day_off = None, None
        best_zeros = float('inf')
        
        for cfg in configs:
            shifts, day_off, coverage = _initial_assign_v12(demand, n, params, cfg)
            
            for _ in range(passes):
                shifts, gap, coverage = _optimize(shifts, demand, n, coverage, params, max_iter=100)
                zeros = _count_zeros(demand, coverage)
                if gap == 0 and zeros == 0:
                    break
            
            zeros = _count_zeros(demand, coverage)
            
            if zeros < best_zeros or (zeros == best_zeros and gap < best_gap):
                best_gap = gap
                best_shifts = [row[:] for row in shifts]
                best_day_off = day_off[:]
                best_zeros = zeros
            
            if gap == 0 and zeros == 0:
                break
        
        # Extra optimization only if zeros exist
        if best_shifts and best_zeros > 0:
            coverage = _calc_coverage(best_shifts, n, params)
            for _ in range(2):
                best_shifts, new_gap, coverage = _optimize(best_shifts, demand, n, coverage, params, max_iter=100)
                new_zeros = _count_zeros(demand, coverage)
                if new_zeros < best_zeros:
                    best_zeros = new_zeros
                    best_gap = new_gap
                if best_zeros == 0:
                    break
        
        return best_gap, best_shifts, best_day_off, best_zeros
    
    else:
        # V10.1 mode: Single config with Sun-Thu only
        shifts, day_off, coverage = _initial_assign_v12(demand, n, params, None)
        
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

# =============================================================================
# MAIN ASSIGNMENT
# =============================================================================
def assign_shifts(da_list_df, demand_df, carryover_df=None, params=None):
    if params is None:
        params = get_params()
    
    flexible = params.get('flexible_day_off', False)
    mode = "flexible day-off" if flexible else "Fri/Sat enforced"
    print(f"Assigning shifts (v12.2 smart break placement, {mode})...")
    
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
        
        for i, da_row in store_das.iterrows():
            da_id = da_row['DA_ID']
            
            for di in range(7):
                day = DAYS[di]
                shift = best_shifts[i][di]
                
                if shift is None:
                    all_schedules.append({
                        'DA_ID': da_id, 'Store': store, 'DSP': da_row['DSP'],
                        'Day': day, 'Day_Index': di,
                        'Shift_Start': None, 'Shift_End': None,
                        'Break_Hour': None, 'Is_Day_Off': True
                    })
                else:
                    st, br = shift
                    all_schedules.append({
                        'DA_ID': da_id, 'Store': store, 'DSP': da_row['DSP'],
                        'Day': day, 'Day_Index': di,
                        'Shift_Start': st, 'Shift_End': _shift_end(st, params, di),
                        'Break_Hour': br, 'Is_Day_Off': False
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
                        
                        if not _is_overnight(start, params):
                            continue
                        
                        if slot < end:
                            if brk >= 0 and slot == brk:
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
                    overnight = _is_overnight(start, params)
                    
                    if overnight:
                        working = slot >= start
                    else:
                        if end == 0:
                            working = slot >= start
                        else:
                            working = start <= slot < end
                    
                    if working:
                        if brk >= 0 and slot == brk:
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
    print("DA ROSTERING ENGINE v12.0")
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
    print("ROSTER ENGINE V12 TEST")
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
