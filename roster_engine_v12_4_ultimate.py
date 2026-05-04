"""
DA ROSTERING ENGINE v12.4_ultimate
====================================
Demand-Driven Proportional Scoring Engine

KEY CHANGES FROM v12.2:
- Proportional scoring: gap/demand instead of gap*demand
- Demand-curve-derived hour classification (no hardcoded dawn/peak/late sets)
- Proportional late-hour bonus relative to unmet demand ratio
- Relative break penalty scaled to max hourly demand
- Proportional distribution penalty from demand curve
- All valid break positions (not just 2)
- Removed _generate_demand_based_configs entirely
- Removed day_off_config parameter; quota-only off-day system
- Demand-ordered day processing (descending daily demand)
- Deterministic optimization (no random.shuffle)

SACRED RULES (Non-negotiable):
1. 12h minimum rest between shifts
2. 5h max continuous before break
3. 6 working days per week (1 day off)

OUTPUT: Same format as v12.2 for webapp compatibility
"""

import pandas as pd
import numpy as np
from datetime import datetime

DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

DEFAULT_PARAMS = {
    'shift_hours': 10,
    'break_hours': 1,
    'max_continuous': 5,
    'min_rest': 12,
    'working_days': 6,
    'night_shift_enabled': True,
    'flexible_day_off': False
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
    """Return ALL valid break positions where both pre-break and post-break
    segments satisfy max_continuous constraint.
    
    v12.4_ultimate fix: iterates pos from 1 to shift_hours-1, includes if
    pos <= max_continuous AND (shift_hours - pos - 1) <= max_continuous.
    """
    mc = params['max_continuous']
    sh = _get_shift_hours(params, day_idx)
    positions = []
    for pos in range(1, sh):
        if pos <= mc and (sh - pos - 1) <= mc:
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
# DEMAND ANALYSIS (NEW in v12.4_ultimate)
# =============================================================================
def _classify_hours(demand, day):
    """Classify hours into peak/off-peak based on demand percentiles.
    
    Computes 75th percentile of nonzero hourly demand values for the day.
    Hours at or above threshold are 'peak', below are 'off_peak'.
    All-zero demand day returns all hours as off_peak.
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
def _calculate_optimal_start_distribution(demand, n, params):
    """Calculate optimal shift start distribution based on demand curve.
    
    v12.4_ultimate: derives targets proportionally from demand curve.
    """
    shift_hours = params['shift_hours']
    
    start_demand = {}
    for start in range(24):
        hours_covered = [(start + h) % 24 for h in range(shift_hours)]
        total = sum(demand[DAYS[di]][h] for di in range(7) for h in hours_covered)
        start_demand[start] = total
    
    total_demand = sum(start_demand.values())
    total_shifts = n * params.get('working_days', 6)
    
    target_distribution = {}
    for start, dem in start_demand.items():
        proportion = dem / total_demand if total_demand > 0 else 1/24
        target_distribution[start] = int(proportion * total_shifts)
    
    allocated = sum(target_distribution.values())
    if allocated < total_shifts:
        sorted_starts = sorted(start_demand.keys(), key=lambda x: -start_demand[x])
        for i in range(total_shifts - allocated):
            target_distribution[sorted_starts[i % len(sorted_starts)]] += 1
    
    return target_distribution

# =============================================================================
# V12.4_ultimate INITIAL ASSIGNMENT
# =============================================================================
def _initial_assign_v12(demand, n, params):
    """
    v12.4_ultimate Initial Assignment — fully demand-driven, deterministic.
    
    Changes from v12.2:
    - No day_off_config parameter (quota-only off-day system)
    - Proportional scoring: gap/demand instead of gap*demand
    - Demand-derived hour classification (no hardcoded sets)
    - Proportional late-hour bonus
    - Relative break penalty
    - Proportional distribution penalty
    - Demand-ordered day processing
    - Deterministic DA ordering (no random)
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
    daily_demand = [sum(demand[day]) for day in DAYS]
    total_weekly_demand = sum(daily_demand)
    working_days_param = params.get('working_days', 6)
    total_da_days = n * working_days_param
    total_off_days = n * (7 - working_days_param)
    
    # Determine which days can have off-days
    forced_working_days = set()
    if not flexible:
        forced_working_days = {5, 6}  # Fri, Sat must work
    
    eligible_days = [di for di in range(7) if di not in forced_working_days]
    
    # Working DAs proportional to demand (smoothed to avoid extreme off-day spikes)
    target_working = [0] * 7
    target_off = [0] * 7
    
    if total_weekly_demand > 0:
        n_eligible = len(eligible_days)
        
        if forced_working_days:
            for di in forced_working_days:
                target_working[di] = n
            remaining_da_days = total_da_days - len(forced_working_days) * n
            eligible_demand = sum(daily_demand[di] for di in eligible_days)
            if eligible_demand > 0 and remaining_da_days > 0:
                # Smoothed: blend proportional with uniform to prevent off-day spikes
                uniform_share = 1.0 / n_eligible
                for di in eligible_days:
                    prop_share = daily_demand[di] / eligible_demand
                    blended_share = 0.5 * prop_share + 0.5 * uniform_share
                    target_working[di] = max(0, min(n, round(remaining_da_days * blended_share)))
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
            # Smoothed: blend proportional with uniform to prevent off-day spikes
            uniform_share = 1.0 / 7
            for di in range(7):
                prop_share = daily_demand[di] / total_weekly_demand
                blended_share = 0.5 * prop_share + 0.5 * uniform_share
                target_working[di] = max(0, min(n, round(total_da_days * blended_share)))
            current_total = sum(target_working)
            diff = total_da_days - current_total
            sorted_all = sorted(range(7), key=lambda d: (-daily_demand[d], d))
            for i in range(abs(diff)):
                d = sorted_all[i % 7]
                if diff > 0 and target_working[d] < n:
                    target_working[d] += 1
                elif diff < 0 and target_working[d] > 0:
                    target_working[d] -= 1
        
        # Cap: no day should have more than 1.5x the average off-days
        avg_off = total_off_days / max(1, len(eligible_days))
        max_off_cap = max(1, round(avg_off * 1.5))
        for di in eligible_days:
            raw_off = max(0, n - target_working[di])
            if raw_off > max_off_cap:
                target_working[di] = n - max_off_cap
        # Rebalance after capping
        current_total = sum(target_working)
        diff = total_da_days - current_total
        sorted_by_demand = sorted(eligible_days, key=lambda d: (-daily_demand[d], d))
        for i in range(abs(diff)):
            d = sorted_by_demand[i % len(sorted_by_demand)]
            if diff > 0 and target_working[d] < n:
                target_working[d] += 1
            elif diff < 0 and target_working[d] > 0:
                target_working[d] -= 1
    else:
        per_day = total_da_days // 7
        for di in range(7):
            target_working[di] = per_day
        remainder = total_da_days - per_day * 7
        for i in range(remainder):
            target_working[i] += 1
    
    # Derive off-day quotas from working targets
    for di in range(7):
        target_off[di] = max(0, n - target_working[di])
    
    # Assign off-days using quotas (quota-only, no day_off_config)
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
    
    # v12.4_ultimate: Demand-ordered day processing
    daily_totals = [(di, sum(demand[DAYS[di]])) for di in range(7)]
    day_order = [di for di, _ in sorted(daily_totals, key=lambda x: (-x[1], x[0]))]
    
    for di in day_order:
        # v12.4_ultimate: Deterministic DA ordering (no random)
        order = sorted(range(n), key=lambda i: (0 if day_off[i] == (di - 1) % 7 else 1, i))
        
        # Precompute hour classification for this day
        hour_classes = _classify_hours(demand, DAYS[di])
        max_demand_today = max(demand[DAYS[di]]) if max(demand[DAYS[di]]) > 0 else 1
        
        for i in order:
            if day_off[i] == di:
                continue
            
            # v12.4_ultimate: With demand-ordered processing, check rest against
            # ALL already-assigned adjacent calendar days, not just prev_end tracking
            valid = list(range(24))
            if not params.get('night_shift_enabled', True):
                sh_check = _get_shift_hours(params, di)
                valid = [s for s in valid if s <= 24 - sh_check]
            
            # Check rest from previous calendar day's shift
            prev_di = (di - 1) % 7
            if shifts[i][prev_di] is not None:
                ps, pb = shifts[i][prev_di]
                p_end = _shift_end(ps, params, prev_di)
                p_on = _is_overnight(ps, params, prev_di)
                valid = [s for s in valid if _calc_rest(p_end, prev_di, p_on, di, s) >= params['min_rest']]
            
            # Check rest to next calendar day's shift
            next_di = (di + 1) % 7
            if shifts[i][next_di] is not None:
                ns, nb = shifts[i][next_di]
                valid = [s for s in valid if _calc_rest(
                    _shift_end(s, params, di), di, _is_overnight(s, params, di),
                    next_di, ns
                ) >= params['min_rest']]
            
            if not valid:
                continue
            
            best_start, best_break, best_score = None, None, -1e9
            
            sh_for_day = _get_shift_hours(params, di)
            
            for st in valid:
                valid_breaks = _valid_breaks(st, params, di)
                
                for br in valid_breaks:
                    covered = _covers(st, br, di, params)
                    
                    # v12.4_ultimate: Proportional gap scoring
                    gap_score = sum(
                        max(0, demand[DAYS[td]][hr] - coverage[td][hr]) / max(1, demand[DAYS[td]][hr])
                        for td, hr in covered
                    )
                    
                    # v12.4_ultimate: Proportional late-hour bonus
                    total_unmet = sum(max(0, demand[DAYS[di]][h] - coverage[di][h]) for h in range(24))
                    late_bonus = 0.0
                    for td, hr in covered:
                        if hr in hour_classes['off_peak'] and demand[DAYS[td]][hr] > coverage[td][hr]:
                            late_bonus += max(0, demand[DAYS[td]][hr] - coverage[td][hr]) / max(1, total_unmet)
                    
                    # v12.4_ultimate: Relative break penalty
                    overnight = _is_overnight(st, params, di)
                    br_day = (di + 1) % 7 if (overnight and br < st) else di
                    break_penalty = -(demand[DAYS[br_day]][br] / max_demand_today)
                    
                    # v12.4_ultimate: Proportional distribution scoring
                    target = target_dist[st]
                    current = current_dist[st]
                    if target > 0:
                        dist_score = (target - current) / target
                    else:
                        dist_score = -1.0 if current > 0 else 0.0
                    
                    score = gap_score + late_bonus + break_penalty + dist_score
                    
                    if score > best_score:
                        best_start, best_break, best_score = st, br, score
            
            if best_start is not None:
                shifts[i][di] = (best_start, best_break)
                current_dist[best_start] += 1
                for td, hr in _covers(best_start, best_break, di, params):
                    coverage[td][hr] += 1
    
    # ── Wrap-around rest check: Saturday overnight → Sunday ──
    min_rest = params.get('min_rest', 12)
    for i in range(n):
        if shifts[i][6] is not None and shifts[i][0] is not None:
            sat_start, sat_break = shifts[i][6]
            sun_start, sun_break = shifts[i][0]
            sat_end = _shift_end(sat_start, params, 6)
            sat_overnight = _is_overnight(sat_start, params, 6)
            rest = _calc_rest(sat_end, 6, sat_overnight, 0, sun_start)
            if rest < min_rest:
                for td, hr in _covers(sun_start, sun_break, 0, params):
                    coverage[td][hr] = max(0, coverage[td][hr] - 1)
                shifts[i][0] = None

    # ── OFF-DAY REDISTRIBUTION: For DAs with unassigned days (rest-blocked),
    # swap their off-day to the blocked day ONLY if it respects quotas.
    for i in range(n):
        for di in range(7):
            if shifts[i][di] is not None or day_off[i] == di:
                continue
            
            # Only allow swapping to an allowed off-day
            if di not in allowed_off_days:
                continue
            
            # Only swap if the new off-day hasn't exceeded its quota
            new_off_count = sum(1 for x in day_off if x == di)
            if new_off_count >= target_off[di]:
                continue
            
            original_off = day_off[i]
            
            # Check if we can actually work the original off-day
            orig_valid = list(range(24))
            if not params.get('night_shift_enabled', True):
                sh = _get_shift_hours(params, original_off)
                orig_valid = [s for s in orig_valid if s <= 24 - sh]
            
            orig_prev = (original_off - 1) % 7
            orig_next = (original_off + 1) % 7
            
            if shifts[i][orig_prev] is not None:
                ps, pb = shifts[i][orig_prev]
                p_end = _shift_end(ps, params, orig_prev)
                p_on = _is_overnight(ps, params, orig_prev)
                orig_valid = [s for s in orig_valid if _calc_rest(p_end, orig_prev, p_on, original_off, s) >= min_rest]
            
            if shifts[i][orig_next] is not None:
                ns, nb = shifts[i][orig_next]
                orig_valid = [s for s in orig_valid if _calc_rest(
                    _shift_end(s, params, original_off), original_off, _is_overnight(s, params, original_off),
                    orig_next, ns
                ) >= min_rest]
            
            if not orig_valid:
                continue
            
            best_st, best_br, best_score = None, None, -1e9
            max_dem = max(demand[DAYS[original_off]]) if max(demand[DAYS[original_off]]) > 0 else 1
            for st in orig_valid:
                for br in _valid_breaks(st, params, original_off):
                    covered = _covers(st, br, original_off, params)
                    score = sum(max(0, demand[DAYS[td]][hr] - coverage[td][hr]) for td, hr in covered) / max_dem
                    if score > best_score:
                        best_st, best_br, best_score = st, br, score
            
            if best_st is not None:
                day_off[i] = di
                shifts[i][original_off] = (best_st, best_br)
                for td, hr in _covers(best_st, best_br, original_off, params):
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
                # v12.4_ultimate: Deterministic DA ordering by coverage contribution
                da_order = sorted(range(n), key=lambda i: (
                    sum(1 for td, hr in _covers(shifts[i][gap_day][0], shifts[i][gap_day][1], gap_day, params)
                        if coverage[td][hr] > demand[DAYS[td]][hr]) if shifts[i][gap_day] else 0,
                    i  # stable tiebreaker
                ))
                
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
            # v12.4_ultimate: Deterministic DA ordering by coverage contribution
            da_order = sorted(range(n), key=lambda i: (
                sum(1 for td, hr in _covers(shifts[i][gap_day][0], shifts[i][gap_day][1], gap_day, params)
                    if coverage[td][hr] > demand[DAYS[td]][hr]) if shifts[i][gap_day] else 0,
                i  # stable tiebreaker
            ))
            
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
    """Single-path optimization for both flexible and non-flexible modes.
    
    v12.4_ultimate: Removed _generate_demand_based_configs entirely.
    No config iteration loop. Single initial assignment + optimization passes.
    """
    shifts, day_off, coverage = _initial_assign_v12(demand, n, params)
    
    zeros = _count_zeros(demand, coverage)
    for p in range(passes):
        shifts, gap, coverage = _optimize(shifts, demand, n, coverage, params, max_iter=100)
        zeros = _count_zeros(demand, coverage)
        if gap == 0 and zeros == 0:
            break
    
    if zeros > 0:
        for _ in range(3):
            shifts, gap, coverage = _optimize(shifts, demand, n, coverage, params, max_iter=100)
            zeros = _count_zeros(demand, coverage)
            if zeros == 0:
                break
    
    return gap, shifts, day_off, zeros


def find_optimal_shifts(demand, n, params, max_search=24, gap_threshold_pct=5):
    """Find the minimum number of shifts that achieves near-optimal gap."""
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
    mode = "flexible day-off" if flexible else "Fri/Sat enforced"
    print(f"Assigning shifts (v12.4_ultimate demand-driven, {mode})...")
    
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
    carryover_excel_data = params.get('carryover_excel_data', [])
    
    records = []
    
    for store in shifts_df['Store'].unique():
        store_shifts = shifts_df[shifts_df['Store'] == store]
        store_demand = demand_df[demand_df['Store'] == store]
        all_das = sorted(store_shifts['DA_ID'].unique())
        
        store_carryover_das = [c for c in carryover_excel_data if c.get('Store') == store]
        
        for day_idx, day in enumerate(DAYS):
            day_shifts = store_shifts[store_shifts['Day'] == day]
            prev_day = DAYS[(day_idx - 1) % 7]
            prev_shifts = store_shifts[store_shifts['Day'] == prev_day]
            
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
                    for col_name in ['Final Orders', 'Hourly Orders', 'Orders']:
                        if col_name in demand_row.columns:
                            orders_val = demand_row[col_name].values
                            if hasattr(orders_val, '__len__') and len(orders_val) > 0:
                                first_val = orders_val.flat[0] if hasattr(orders_val, 'flat') else orders_val[0]
                                orders = int(first_val) if pd.notna(first_val) else 0
                            break
                
                da_status = {da: '-' for da in all_das}
                rostered = 0
                
                if use_manual_carryover and slot < 5 and sunday_carryover_das > 0:
                    rostered += sunday_carryover_das
                elif use_excel_carryover and is_sunday:
                    for carryover_da in store_carryover_das:
                        sat_end = carryover_da.get('Sat_Shift_End', 5)
                        if slot < sat_end:
                            rostered += 1
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
    print("DA ROSTERING ENGINE v12.4_ultimate")
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
