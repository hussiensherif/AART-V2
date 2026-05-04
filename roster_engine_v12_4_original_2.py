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
1. Min rest between shifts (configurable via min_rest parameter)
2. Max continuous work before break (configurable via max_continuous parameter)
3. Working days per week (configurable via working_days parameter)

OUTPUT: Same format as v8 for webapp compatibility
"""

import pandas as pd
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
    except Exception:
        return pd.DataFrame()

def build_da_list(available_das_df):
    das = []
    # Track counter per (Store, DSP_Code) to avoid duplicate DA_IDs
    # when the same DSP appears in multiple rows
    dsp_counter = {}
    for _, row in available_das_df.iterrows():
        key = (row['Store'], row['DSP_Code'])
        if key not in dsp_counter:
            dsp_counter[key] = 0
        for i in range(int(row['DA_Count'])):
            dsp_counter[key] += 1
            das.append({
                'DA_ID': f"{row['Store']}-{row['DSP_Code']}-{str(dsp_counter[key]).zfill(3)}",
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
    """Return ALL valid break positions. For 2-break shifts, only returns first-break
    positions that have at least one valid second-break partner."""
    mc = params['max_continuous']
    sh = _get_shift_hours(params, day_idx)
    break_hours_param = params.get('break_hours', 1)
    
    if break_hours_param < 2:
        # Single break: original logic
        positions = []
        for pos in range(1, sh):
            if pos <= mc and (sh - pos - 1) <= mc:
                positions.append((start + pos) % 24)
        return positions if positions else [(start + mc) % 24]
    
    # 2 breaks: return first-break positions that have a valid second-break partner
    positions = []
    for pos1 in range(1, sh):
        hr1 = (start + pos1) % 24
        # Check if there's any valid pos2 that makes all 3 segments <= mc
        has_partner = False
        for pos2 in range(1, sh):
            if pos2 == pos1:
                continue
            offsets = sorted([pos1, pos2])
            seg1 = offsets[0]
            seg2 = offsets[1] - offsets[0] - 1
            seg3 = sh - offsets[1] - 1
            if seg1 <= mc and seg2 <= mc and seg3 <= mc:
                has_partner = True
                break
        if has_partner:
            positions.append(hr1)
    return positions if positions else [(start + mc) % 24]

def _place_breaks(start, params, day_idx=None, demand_row=None):
    """Wrapper around break_utils.place_breaks that returns break hour list.
    Returns list of break hours (length 0, 1, or 2 depending on break_hours).
    """
    sh = _get_shift_hours(params, day_idx)
    break_hours = params.get('break_hours', 1)
    max_continuous = params['max_continuous']
    return place_breaks(start, sh, break_hours, max_continuous, demand_row)

def _calc_rest(prev_end, prev_day_idx, prev_overnight, curr_day_idx, curr_start):
    if prev_end is None:
        return float('inf')
    
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
    
    # Operating window constraint
    allowed = params.get('valid_start_times')
    if allowed is not None:
        valid = [s for s in valid if s in allowed]
    
    return valid if valid else None

def _covers(start, break_hr, day_idx, params, break_hr_2=None):
    coverage = []
    overnight = _is_overnight(start, params, day_idx)
    sh = _get_shift_hours(params, day_idx)
    
    # Auto-compute second break if break_hours >= 2 and not provided
    if break_hr_2 is None and params.get('break_hours', 1) >= 2 and break_hr is not None:
        break_hr_2 = _get_break2(start, break_hr, params, day_idx)
    
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

def _get_break2(start, break_hr, params, day_idx=None):
    """Get the second break hour for 2-break shifts that forms a valid pair with break_hr."""
    if params.get('break_hours', 1) < 2 or break_hr is None:
        return None
    mc = params['max_continuous']
    sh = _get_shift_hours(params, day_idx)
    br1_offset = (break_hr - start) % 24
    best_br2 = None
    best_min_seg = -1
    for pos in range(1, sh):
        if pos == br1_offset:
            continue
        hr2 = (start + pos) % 24
        offsets = sorted([br1_offset, pos])
        seg1 = offsets[0]
        seg2 = offsets[1] - offsets[0] - 1
        seg3 = sh - offsets[1] - 1
        if seg1 <= mc and seg2 <= mc and seg3 <= mc:
            min_seg = min(seg1, seg2, seg3)
            if min_seg > best_min_seg:
                best_min_seg = min_seg
                best_br2 = hr2
    return best_br2

def _calc_gap(demand, coverage):
    return sum(max(0, demand[DAYS[di]][h] - coverage[di][h]) for di in range(7) for h in range(24))

def _count_zeros(demand, coverage):
    return sum(1 for di in range(7) for h in range(24) 
               if demand[DAYS[di]][h] > 0 and coverage[di][h] == 0)

# =============================================================================
# DEMAND ANALYSIS
# =============================================================================
def _classify_hours(demand, day):
    """Classify hours into peak/off-peak based on demand mean.
    
    Computes mean of nonzero hourly demand values for the day.
    Hours at or above mean = peak, below = off-peak.
    Handles all-zero demand day (returns all hours as off_peak).
    """
    hourly = demand[day]  # list of 24 values
    nonzero = [h for h in hourly if h > 0]
    if not nonzero:
        return {'peak': set(), 'off_peak': set(range(24))}
    threshold = sum(nonzero) / len(nonzero)  # mean of nonzero demand
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
        # Use n as upper bound — targets are relative proportions
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
    Fixed Start Time Assignment — each DA works the same start time every working day.
    
    Used when max_rest == min_rest (shift + rest = 24h scheduling constraint).
    No rest-blocking possible since start time is constant.
    
    Algorithm:
    1. Compute off-day quotas (equal surplus rate)
    2. Compute optimal start-time distribution from weekly demand
    3. Assign each DA a (start_time, off_day) pair greedily
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
    
    # Compute optimal start distribution from demand
    # For each start time, compute total weekly demand covered
    sh = params['shift_hours']
    night_enabled = params.get('night_shift_enabled', True)
    max_shifts = params.get('max_shifts', 0)
    
    valid_starts = list(range(24))
    if not night_enabled:
        max_start_hr = 24 - sh
        valid_starts = [s for s in valid_starts if s <= max_start_hr]
    
    # Operating window constraint
    allowed = params.get('valid_start_times')
    if allowed is not None:
        valid_starts = [s for s in valid_starts if s in allowed]
    
    # Compute per-start weekly demand coverage (used for ranking and distribution)
    start_weekly_demand = {}
    for st in valid_starts:
        br = _valid_breaks(st, params)[0]
        total = 0
        for di in range(7):
            for td, hr in _covers(st, br, di, params):
                total += demand[DAYS[td]][hr]
        start_weekly_demand[st] = total
    
    # If max_shifts is set, select N start times using greedy marginal demand
    if max_shifts > 0 and len(valid_starts) > max_shifts:
        selected = []
        covered_hours = set()
        remaining = list(valid_starts)

        for _ in range(max_shifts):
            best_st = None
            best_marginal = -1
            for st in remaining:
                br = _valid_breaks(st, params)[0]
                marginal = 0
                for di in range(7):
                    for td, hr in _covers(st, br, di, params):
                        if (td, hr) not in covered_hours:
                            marginal += demand[DAYS[td]][hr]
                if marginal > best_marginal:
                    best_marginal = marginal
                    best_st = st
            if best_st is None:
                break
            selected.append(best_st)
            remaining.remove(best_st)
            br = _valid_breaks(best_st, params)[0]
            for di in range(7):
                for td, hr in _covers(best_st, br, di, params):
                    covered_hours.add((td, hr))

        valid_starts = sorted(selected)
        start_weekly_demand = {s: start_weekly_demand[s] for s in valid_starts}

        # Shape-variance swap refinement: try replacing each selected start
        # with an unselected one if it produces a lower coverage/demand ratio
        # variance when DAs are allocated proportionally. This picks starts
        # whose combined coverage best matches the demand curve shape.
        # All values derived from data — no hardcoded constants.
        unselected = [s for s in list(range(24)) if s not in set(valid_starts)]
        if not night_enabled:
            max_start_hr = 24 - sh
            unselected = [s for s in unselected if s <= max_start_hr]

        def _eval_start_set(starts):
            """Evaluate a set of starts by simulating proportional DA allocation
            and computing the variance of coverage/demand ratios."""
            swd_eval = {}
            for s in starts:
                b = _valid_breaks(s, params)[0]
                swd_eval[s] = sum(demand[DAYS[td]][hr] for di in range(7) for td, hr in _covers(s, b, di, params))
            ts = sum(swd_eval.values())
            if ts == 0:
                return float('inf')
            da_alloc = {s: max(1, round(n * swd_eval[s] / ts)) for s in starts}
            sim_cov = [[0] * 24 for _ in range(7)]
            for s in starts:
                b = _valid_breaks(s, params)[0]
                cnt = da_alloc[s]
                for di in range(7):
                    for td, hr in _covers(s, b, di, params):
                        sim_cov[td][hr] += cnt
            ratios = []
            for di in range(7):
                for h in range(24):
                    d = demand[DAYS[di]][h]
                    if d > 0:
                        ratios.append(sim_cov[di][h] / d)
            if not ratios:
                return float('inf')
            mr = sum(ratios) / len(ratios)
            return sum((r - mr) ** 2 for r in ratios) / len(ratios) if mr > 0 else float('inf')

        swap_improved = True
        while swap_improved:
            swap_improved = False
            best_var = _eval_start_set(valid_starts)
            for idx, old_st in enumerate(valid_starts):
                for new_st in unselected:
                    trial = valid_starts[:idx] + [new_st] + valid_starts[idx+1:]
                    v = _eval_start_set(trial)
                    if v < best_var:
                        best_var = v
                        unselected.remove(new_st)
                        unselected.append(old_st)
                        valid_starts[idx] = new_st
                        swap_improved = True
                        break
                if swap_improved:
                    break
            valid_starts = sorted(valid_starts)

        # Recompute start_weekly_demand for the refined set
        start_weekly_demand = {}
        for st in valid_starts:
            br = _valid_breaks(st, params)[0]
            start_weekly_demand[st] = sum(
                demand[DAYS[td]][hr]
                for di in range(7) for td, hr in _covers(st, br, di, params)
            )
    
    # Score each (start_time, off_day) combo by total gap reduction
    coverage = [[0] * 24 for _ in range(7)]
    shifts = [[None] * 7 for _ in range(n)]
    day_off = []
    day_off_count = [0] * 7
    start_count = {s: 0 for s in valid_starts}
    
    total_start_demand = sum(start_weekly_demand.values())
    # Fix 2: Compute target_start from per-hour demand share.
    # For each hour h, the demand needs to be split among all starts that
    # cover hour h. Each start's share of hour h = demand[h] / (number of
    # selected starts covering h). Sum shares across all hours = target weight.
    # This prevents overlapping starts from double-counting shared hours.
    start_hour_share = {st: 0.0 for st in valid_starts}
    for di in range(7):
        for h in range(24):
            dem_h = demand[DAYS[di]][h]
            if dem_h <= 0:
                continue
            # Which selected starts cover (di, h)?
            covering_starts = []
            for st in valid_starts:
                br = _valid_breaks(st, params)[0]
                if any(td == di and hr == h for td, hr in _covers(st, br, di, params)):
                    covering_starts.append(st)
            if covering_starts:
                share = dem_h / len(covering_starts)
                for st in covering_starts:
                    start_hour_share[st] += share
    total_share = sum(start_hour_share.values())
    target_start = {}
    for st in valid_starts:
        if total_share > 0:
            target_start[st] = max(1, round(n * start_hour_share[st] / total_share))
        else:
            target_start[st] = max(1, n // len(valid_starts))
    
    # Precompute for ratio-based scoring
    dd_totals = [sum(demand[DAYS[di]][h] for h in range(24)) for di in range(7)]
    active_days_dd = [di for di in range(7) if dd_totals[di] > 0]
    total_weekly_demand = sum(dd_totals)
    eff_hours = sh - params.get('break_hours', 1)
    total_da_hours = n * working_days_param * eff_hours
    ideal_ratio = total_da_hours / total_weekly_demand if total_weekly_demand > 0 else 1.0
    # Active hours for variance computation
    active_hours = [(di, h) for di in range(7) for h in range(24) if demand[DAYS[di]][h] > 0]

    # When max_shifts > 0, seed each selected start with one DA first.
    # This guarantees all user-requested starts are used. The seeding
    # picks the best (break, off_day) using net demand scoring.
    # Phase 2 (greedy loop below) handles the remaining DAs using
    # net scoring with over-coverage penalty to avoid piling.
    next_da = 0
    if max_shifts > 0 and n >= len(valid_starts):
        seed_order = sorted(valid_starts, key=lambda s: -start_weekly_demand.get(s, 0))
        for st in seed_order:
            if next_da >= n:
                break
            best_off, best_br, best_score = None, None, float('-inf')
            for br in _valid_breaks(st, params):
                for off_day in allowed_off_days:
                    if day_off_count[off_day] >= target_off[off_day] and any(
                        day_off_count[d] < target_off[d] for d in allowed_off_days
                    ):
                        continue
                    score = 0
                    for di in range(7):
                        if di == off_day:
                            continue
                        for td, hr in _covers(st, br, di, params):
                            if demand[DAYS[td]][hr] > 0:
                                score += demand[DAYS[td]][hr] - coverage[td][hr]
                    if score > best_score:
                        best_off, best_br, best_score = off_day, br, score
            if best_off is None:
                best_off = allowed_off_days[0]
                best_br = _valid_breaks(st, params)[0]
            day_off.append(best_off)
            day_off_count[best_off] += 1
            start_count[st] = start_count.get(st, 0) + 1
            for di in range(7):
                if di == best_off:
                    shifts[next_da][di] = None
                else:
                    shifts[next_da][di] = (st, best_br)
                    for td, hr in _covers(st, best_br, di, params):
                        coverage[td][hr] += 1
            next_da += 1

    # Greedy assignment: for remaining DAs, pick the (start, off_day) that
    # best matches the demand shape using ratio + variance scoring.
    # No hardcoded constants — all derived from demand data and params.
    #
    # Optimization: precompute coverage sets per (start, break) to avoid
    # recomputing _covers in the inner loop. Variance is computed
    # incrementally using sum-of-ratios and sum-of-squared-ratios.
    n_active = len(active_hours)
    # Precompute which active_hours indices each (start, break, day) covers
    ah_index = {(di, h): idx for idx, (di, h) in enumerate(active_hours)}
    start_break_covers = {}
    for st in valid_starts:
        for br in _valid_breaks(st, params):
            # For each off_day, which active_hours indices are covered?
            for off_day in allowed_off_days:
                indices = []
                for di in range(7):
                    if di == off_day:
                        continue
                    for td, hr in _covers(st, br, di, params):
                        if (td, hr) in ah_index:
                            indices.append(ah_index[(td, hr)])
                start_break_covers[(st, br, off_day)] = indices

    for i in range(next_da, n):
        # Current ratios
        ratios_cur = [coverage[di][h] / demand[DAYS[di]][h] for di, h in active_hours]
        sum_r = sum(ratios_cur)
        sum_r2 = sum(r * r for r in ratios_cur)

        best_start, best_break, best_off, best_score = None, None, None, float('-inf')
        
        for st in valid_starts:
            for br in _valid_breaks(st, params):
                for off_day in allowed_off_days:
                    if day_off_count[off_day] >= target_off[off_day] and any(
                        day_off_count[d] < target_off[d] for d in allowed_off_days
                    ):
                        continue
                    
                    indices = start_break_covers.get((st, br, off_day), [])
                    
                    # Ratio score: sum of (ideal_ratio - current_ratio) * demand
                    # for each covered hour
                    ratio_score = 0.0
                    # Incremental variance: track delta in sum_r and sum_r2
                    delta_sum_r = 0.0
                    delta_sum_r2 = 0.0
                    for idx2 in indices:
                        di, h = active_hours[idx2]
                        d = demand[DAYS[di]][h]
                        old_r = ratios_cur[idx2]
                        new_r = old_r + 1.0 / d
                        ratio_score += (ideal_ratio - old_r) * d
                        delta_sum_r += (new_r - old_r)
                        delta_sum_r2 += (new_r * new_r - old_r * old_r)
                    
                    # Variance after = (sum_r2 + delta) / n - ((sum_r + delta) / n)^2
                    new_sum_r = sum_r + delta_sum_r
                    new_sum_r2 = sum_r2 + delta_sum_r2
                    var_after = new_sum_r2 / n_active - (new_sum_r / n_active) ** 2 if n_active > 0 else 0
                    var_before = sum_r2 / n_active - (sum_r / n_active) ** 2 if n_active > 0 else 0
                    shape_bonus = (var_before - var_after) * total_weekly_demand
                    
                    # Break penalty (both breaks for 2-break shifts)
                    max_dem = max(max(demand[DAYS[di2]]) for di2 in range(7)) or 1
                    br_penalty = 0
                    br2_score = _get_break2(st, br, params)
                    for di2 in range(7):
                        if di2 == off_day:
                            continue
                        overnight = _is_overnight(st, params, di2)
                        br_day = (di2 + 1) % 7 if (overnight and br < st) else di2
                        br_penalty -= demand[DAYS[br_day]][br] / max_dem
                        if br2_score is not None:
                            br2_day = (di2 + 1) % 7 if (overnight and br2_score < st) else di2
                            br_penalty -= demand[DAYS[br2_day]][br2_score] / max_dem
                    
                    score = ratio_score + shape_bonus + br_penalty / max(1, n)
                    
                    if score > best_score:
                        best_start, best_break, best_off, best_score = st, br, off_day, score
        
        if best_start is not None:
            day_off.append(best_off)
            day_off_count[best_off] += 1
            start_count[best_start] = start_count.get(best_start, 0) + 1
            
            for di in range(7):
                if di == best_off:
                    shifts[i][di] = None
                else:
                    shifts[i][di] = (best_start, best_break)
                    for td, hr in _covers(best_start, best_break, di, params):
                        coverage[td][hr] += 1
    
    # Post-assignment rebalancing pass: try moving each DA to a different
    # start time if it reduces total gap. This is a safety net that fixes
    # any DAs placed on over-covered starts during the greedy/seeding phases.
    # Only moves within the valid_starts set. No hardcoded constants.
    current_gap = _calc_gap(demand, coverage)
    for _rebal_round in range(3):  # iteration guard
        rebal_improved = False
        for i in range(len(day_off)):
            off_di = day_off[i]
            # Get current start and break for this DA
            cur_st, cur_br = None, None
            for di in range(7):
                if shifts[i][di] is not None:
                    cur_st, cur_br = shifts[i][di]
                    break
            if cur_st is None or cur_br is None:
                continue

            # Remove this DA's coverage
            for di in range(7):
                if di == off_di:
                    continue
                for td, hr in _covers(cur_st, cur_br, di, params):
                    coverage[td][hr] -= 1

            # Try each valid start + break combo
            best_new_st, best_new_br, best_new_gap = cur_st, cur_br, current_gap
            for st in valid_starts:
                for br in _valid_breaks(st, params):
                    # Apply candidate coverage
                    for di in range(7):
                        if di == off_di:
                            continue
                        for td, hr in _covers(st, br, di, params):
                            coverage[td][hr] += 1
                    g = _calc_gap(demand, coverage)
                    if g < best_new_gap:
                        best_new_gap = g
                        best_new_st, best_new_br = st, br
                    # Remove candidate coverage
                    for di in range(7):
                        if di == off_di:
                            continue
                        for td, hr in _covers(st, br, di, params):
                            coverage[td][hr] -= 1

            # Apply the best option (may be the original if nothing improved)
            for di in range(7):
                if di == off_di:
                    shifts[i][di] = None
                else:
                    shifts[i][di] = (best_new_st, best_new_br)
                    for td, hr in _covers(best_new_st, best_new_br, di, params):
                        coverage[td][hr] += 1
            if best_new_gap < current_gap:
                current_gap = best_new_gap
                rebal_improved = True
        if not rebal_improved:
            break
    
    # ── OFF-DAY REBALANCING: Equalize daily gap ratios ─────────────────────
    working_days_param = params.get('working_days', 6)
    eff_hours_rebal = params['shift_hours'] - params.get('break_hours', 1)
    for _offbal_round in range(n * 3):
        daily_dem = [sum(demand[DAYS[di]][h] for h in range(24)) for di in range(7)]
        daily_gap = [sum(max(0, demand[DAYS[di]][h] - coverage[di][h]) for h in range(24)) for di in range(7)]
        daily_ratio = [daily_gap[di] / daily_dem[di] if daily_dem[di] > 0 else 0 for di in range(7)]
        active_days = [di for di in range(7) if daily_dem[di] > 0]
        if not active_days:
            break
        worst_day = max(active_days, key=lambda di: daily_ratio[di])
        max_ratio = daily_ratio[worst_day]
        mean_ratio = sum(daily_ratio[di] for di in active_days) / len(active_days)
        # Stop when one DA's contribution can't meaningfully change the ratio
        convergence_threshold = eff_hours_rebal / max(1, max(daily_dem))
        if max_ratio - mean_ratio < convergence_threshold:
            break
        best_swap_i, best_swap_target, best_new_max = None, None, max_ratio
        for i in range(n):
            if day_off[i] != worst_day:
                continue
            da_st, da_br = None, None
            for di in range(7):
                if shifts[i][di] is not None:
                    da_st, da_br = shifts[i][di]
                    break
            if da_st is None:
                continue
            worst_gap_hours = set(h for h in range(24) if demand[DAYS[worst_day]][h] > coverage[worst_day][h])
            covers_worst = set(hr for td, hr in _covers(da_st, da_br, worst_day, params) if td == worst_day)
            if not (covers_worst & worst_gap_hours):
                continue
            for target_day in active_days:
                if target_day == worst_day or daily_ratio[target_day] >= max_ratio - convergence_threshold:
                    continue
                if shifts[i][target_day] is None:
                    continue
                for td, hr in _covers(da_st, da_br, target_day, params):
                    coverage[td][hr] -= 1
                for td, hr in _covers(da_st, da_br, worst_day, params):
                    coverage[td][hr] += 1
                new_daily_gap = [sum(max(0, demand[DAYS[di]][h] - coverage[di][h]) for h in range(24)) for di in range(7)]
                new_ratio = [new_daily_gap[di] / daily_dem[di] if daily_dem[di] > 0 else 0 for di in range(7)]
                new_max = max(new_ratio[di] for di in active_days)
                if new_max < best_new_max:
                    best_new_max = new_max
                    best_swap_i = i
                    best_swap_target = target_day
                for td, hr in _covers(da_st, da_br, worst_day, params):
                    coverage[td][hr] -= 1
                for td, hr in _covers(da_st, da_br, target_day, params):
                    coverage[td][hr] += 1
        if best_swap_i is not None and best_new_max < max_ratio:
            i = best_swap_i
            da_st, da_br = None, None
            for di in range(7):
                if shifts[i][di] is not None:
                    da_st, da_br = shifts[i][di]
                    break
            for td, hr in _covers(da_st, da_br, best_swap_target, params):
                coverage[td][hr] -= 1
            for td, hr in _covers(da_st, da_br, worst_day, params):
                coverage[td][hr] += 1
            shifts[i][worst_day] = (da_st, da_br)
            shifts[i][best_swap_target] = None
            day_off[i] = best_swap_target
        else:
            break
    
    # ── MULTI-OFF-DAY EXTENSION ────────────────────────────────────────────
    num_off_per_da = 7 - working_days_param
    if num_off_per_da > 1:
        extra_off_needed = num_off_per_da - 1
        ext_off_count = [0] * 7
        for i in range(n):
            ext_off_count[day_off[i]] += 1
        daily_dem = [sum(demand[DAYS[di]][h] for h in range(24)) for di in range(7)]
        total_dem = sum(daily_dem)
        total_all_off = n * (7 - working_days_param)
        total_da_days = n * working_days_param
        target_total_off = [0] * 7
        if total_dem > 0:
            for di in range(7):
                share = daily_dem[di] / total_dem
                tw = min(n, round(total_da_days * share))
                target_total_off[di] = max(0, n - tw)
            diff = total_all_off - sum(target_total_off)
            sbd = sorted(range(7), key=lambda d: daily_dem[d])
            for j in range(abs(diff)):
                d = sbd[j % 7]
                if diff > 0 and target_total_off[d] < n:
                    target_total_off[d] += 1
                elif diff < 0 and target_total_off[d] > 0:
                    target_total_off[d] -= 1
        target_extra = [max(0, target_total_off[di] - ext_off_count[di]) for di in range(7)]
        extra_off_assigned = [0] * 7
        for i in range(n):
            primary_off = day_off[i]
            wdl = []
            for di in range(7):
                if di == primary_off or shifts[i][di] is None:
                    continue
                st, br = shifts[i][di]
                gi = sum(1 for td, hr in _covers(st, br, di, params) if coverage[td][hr] <= demand[DAYS[td]][hr])
                todi = ext_off_count[di] + extra_off_assigned[di]
                bp = max(0, todi - target_extra[di]) * (daily_dem[di] / max(1, sum(daily_dem)) * n)
                wdl.append((di, gi + bp))
            wdl.sort(key=lambda x: (x[1], x[0]))
            for idx in range(min(extra_off_needed, len(wdl))):
                rdi = wdl[idx][0]
                st, br = shifts[i][rdi]
                for td, hr in _covers(st, br, rdi, params):
                    coverage[td][hr] -= 1
                shifts[i][rdi] = None
                extra_off_assigned[rdi] += 1
    
    # Convert day_off to sets
    day_off = [{d} if isinstance(d, int) else d for d in day_off]
    if num_off_per_da > 1:
        for i in range(n):
            for di in range(7):
                if shifts[i][di] is None:
                    day_off[i].add(di)
    
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
            allowed = params.get('valid_start_times')
            if allowed is not None:
                base_valid = [s for s in base_valid if s in allowed]
            
            # Check rest with previous day (di-1)
            if shifts[i][prev_di] is not None:
                prev_st, prev_br = shifts[i][prev_di]
                prev_end_hr = _shift_end(prev_st, params, prev_di)
                prev_on = _is_overnight(prev_st, params, prev_di)
                base_valid = [s for s in base_valid if _calc_rest(prev_end_hr, prev_di, prev_on, di, s) >= params['min_rest']]
            
            # Check rest with next day (di+1)
            if shifts[i][next_di] is not None:
                next_st, next_br = shifts[i][next_di]
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
            
            best_start, best_break, best_score = None, None, float('-inf')
            
            for st in valid:
                valid_breaks = _valid_breaks(st, params, di)
                overnight = _is_overnight(st, params, di)
                
                for br in valid_breaks:
                    covered_slots = _covers(st, br, di, params)
                    
                    # v12.4: ABSOLUTE gap scoring
                    gap_score = sum(
                        max(0, demand[DAYS[td]][hr] - coverage[td][hr])
                        for td, hr in covered_slots
                    )
                    gap_score = gap_score / max_demand_today
                    
                    # Off-peak bonus: derived from the ratio of off-peak gap
                    # to total gap on this day. When off-peak is under-served
                    # relative to peak, the bonus is larger. All data-derived.
                    late_bonus = 0.0
                    total_gap_today = sum(max(0, demand[DAYS[di]][h] - coverage[di][h]) for h in range(24))
                    offpeak_gap = sum(max(0, demand[DAYS[di]][h] - coverage[di][h]) for h in hour_classes['off_peak'])
                    offpeak_ratio = offpeak_gap / total_gap_today if total_gap_today > 0 else 0
                    for td, hr in covered_slots:
                        if hr in hour_classes['off_peak'] and demand[DAYS[td]][hr] > coverage[td][hr]:
                            late_bonus += max(0, demand[DAYS[td]][hr] - coverage[td][hr]) / max_demand_today * offpeak_ratio
                    
                    # v12.4: Relative break penalty (both breaks for 2-break shifts)
                    br_day = (di + 1) % 7 if (overnight and br < st) else di
                    break_penalty = -(demand[DAYS[br_day]][br] / max_demand_today)
                    br2 = _get_break2(st, br, params, di)
                    if br2 is not None:
                        br2_day = (di + 1) % 7 if (overnight and br2 < st) else di
                        break_penalty -= demand[DAYS[br2_day]][br2] / max_demand_today
                    
                    # Per-day distribution scoring: symmetric formula derived
                    # from target vs current ratio. Positive when under target,
                    # negative when over target. No arbitrary constants.
                    target = target_dist.get(st, 0)
                    current = current_day_dist.get(st, 0)
                    if target > 0:
                        dist_score = (target - current) / target
                    else:
                        dist_score = -current / max(1, n)
                    
                    # FLEXIBILITY PENALTY — derived from the fraction of valid
                    # starts lost on adjacent unprocessed days. Penalty magnitude
                    # is proportional to gap_score so it scales with the data.
                    flex_penalty = 0.0
                    s_end = _shift_end(st, params, di)
                    s_on = _is_overnight(st, params, di)
                    total_possible = len(base_valid)
                    
                    if unprocessed_next and day_off[i] != next_di:
                        next_valid_count = 0
                        for ns in range(24):
                            if not params.get('night_shift_enabled', True):
                                nsh = _get_shift_hours(params, next_di)
                                if ns > 24 - nsh:
                                    continue
                            if _calc_rest(s_end, di, s_on, next_di, ns) >= params['min_rest']:
                                next_valid_count += 1
                        flex_lost = total_possible - next_valid_count
                        flex_penalty -= (flex_lost / max(1, total_possible)) * max(gap_score, 1 / max(1, n))
                    
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
                        flex_lost = total_possible - prev_valid_count
                        flex_penalty -= (flex_lost / max(1, total_possible)) * max(gap_score, 1 / max(1, n))
                    
                    score = gap_score + late_bonus + break_penalty + dist_score + flex_penalty
                    
                    if score > best_score:
                        best_start, best_break, best_score = st, br, score
            
            if best_start is not None:
                shifts[i][di] = (best_start, best_break)
                current_day_dist[best_start] = current_day_dist.get(best_start, 0) + 1
                current_dist[best_start] += 1
                for td, hr in _covers(best_start, best_break, di, params):
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
                    old_adj_start, old_adj_break = shifts[i][adj_di]
                    
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
                        ps, pb = shifts[i][adj_prev_di]
                        p_end = _shift_end(ps, params, adj_prev_di)
                        p_on = _is_overnight(ps, params, adj_prev_di)
                        adj_valid = [s for s in adj_valid if _calc_rest(p_end, adj_prev_di, p_on, adj_di, s) >= params['min_rest']]
                    
                    # Rest from adj_di → adj_next_di (if adj_next_di != di and has shift)
                    if adj_next_di != di and shifts[i][adj_next_di] is not None:
                        ns, nb = shifts[i][adj_next_di]
                        adj_valid = [s for s in adj_valid if _calc_rest(
                            _shift_end(s, params, adj_di), adj_di, _is_overnight(s, params, adj_di),
                            adj_next_di, ns
                        ) >= params['min_rest']]
                    
                    if not adj_valid:
                        continue
                    
                    # Now for each candidate adj start, check if it opens up a valid start on day di
                    best_combo = None
                    best_combo_score = float('-inf')
                    
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
                            os, ob = shifts[i][other_di]
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
                            for di_br in _valid_breaks(di_st, params, di):
                                di_covered = _covers(di_st, di_br, di, params)
                                di_gap_score = sum(
                                    max(0, demand[DAYS[td]][hr] - coverage[td][hr]) / max(1, demand[DAYS[td]][hr])
                                    for td, hr in di_covered
                                )
                                # Also account for adj shift change cost
                                for adj_br in _valid_breaks(adj_st, params, adj_di):
                                    adj_new_covered = _covers(adj_st, adj_br, adj_di, params)
                                    adj_old_covered = _covers(old_adj_start, old_adj_break, adj_di, params)
                                    
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
                                        best_combo = (adj_st, adj_br, di_st, di_br)
                                    break  # take first valid adj break for this adj_st
                            if best_combo is not None and best_combo[2] == di_st:
                                break  # found a good combo with this di_st
                    
                    if best_combo is not None:
                        new_adj_st, new_adj_br, new_di_st, new_di_br = best_combo
                        
                        # Remove old adj coverage
                        for td, hr in _covers(old_adj_start, old_adj_break, adj_di, params):
                            coverage[td][hr] -= 1
                        
                        # Apply new adj shift
                        shifts[i][adj_di] = (new_adj_st, new_adj_br)
                        for td, hr in _covers(new_adj_st, new_adj_br, adj_di, params):
                            coverage[td][hr] += 1
                        
                        # Apply new shift on blocked day
                        shifts[i][di] = (new_di_st, new_di_br)
                        for td, hr in _covers(new_di_st, new_di_br, di, params):
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
            
            old_prev_st, old_prev_br = shifts[i][prev_di]
            old_next_st, old_next_br = shifts[i][next_di]
            
            # Get valid starts for prev_di (respecting prev_di's own prev neighbor)
            pp_di = (prev_di - 1) % 7
            prev_valid = list(range(24))
            if not params.get('night_shift_enabled', True):
                sh = _get_shift_hours(params, prev_di)
                prev_valid = [s for s in prev_valid if s <= 24 - sh]
            if pp_di != di and shifts[i][pp_di] is not None:
                pps, ppb = shifts[i][pp_di]
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
                nns, nnb = shifts[i][nn_di]
                next_valid = [s for s in next_valid if _calc_rest(
                    _shift_end(s, params, next_di), next_di, _is_overnight(s, params, next_di),
                    nn_di, nns
                ) >= params['min_rest']]
            
            if not prev_valid or not next_valid:
                continue
            
            # Try all (prev_start, next_start) combos to find one that opens a Sat window
            best_dual = None
            best_dual_score = float('-inf')
            
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
                    di_br = _valid_breaks(di_st, params, di)[0]
                    ps_br = _valid_breaks(ps, params, prev_di)[0]
                    ns_br = _valid_breaks(ns, params, next_di)[0]
                    
                    # Score: gap reduction from adding di shift - cost of changing prev/next
                    di_covered = _covers(di_st, di_br, di, params)
                    di_gain = sum(max(0, demand[DAYS[td]][hr] - coverage[td][hr]) for td, hr in di_covered)
                    
                    # Cost of changing prev
                    old_prev_cov = set(_covers(old_prev_st, old_prev_br, prev_di, params))
                    new_prev_cov = set(_covers(ps, ps_br, prev_di, params))
                    prev_loss = sum(max(0, demand[DAYS[td]][hr] - coverage[td][hr] + 1) for td, hr in old_prev_cov - new_prev_cov)
                    
                    # Cost of changing next
                    old_next_cov = set(_covers(old_next_st, old_next_br, next_di, params))
                    new_next_cov = set(_covers(ns, ns_br, next_di, params))
                    next_loss = sum(max(0, demand[DAYS[td]][hr] - coverage[td][hr] + 1) for td, hr in old_next_cov - new_next_cov)
                    
                    score = di_gain - prev_loss - next_loss
                    if score > best_dual_score:
                        best_dual_score = score
                        best_dual = (ps, ps_br, di_st, di_br, ns, ns_br)
            
            if best_dual is not None:
                new_ps, new_pb, new_ds, new_db, new_ns, new_nb = best_dual
                
                # Remove old coverage
                for td, hr in _covers(old_prev_st, old_prev_br, prev_di, params):
                    coverage[td][hr] -= 1
                for td, hr in _covers(old_next_st, old_next_br, next_di, params):
                    coverage[td][hr] -= 1
                
                # Apply new shifts
                shifts[i][prev_di] = (new_ps, new_pb)
                for td, hr in _covers(new_ps, new_pb, prev_di, params):
                    coverage[td][hr] += 1
                
                shifts[i][di] = (new_ds, new_db)
                for td, hr in _covers(new_ds, new_db, di, params):
                    coverage[td][hr] += 1
                
                shifts[i][next_di] = (new_ns, new_nb)
                for td, hr in _covers(new_ns, new_nb, next_di, params):
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
                ps, pb = shifts[i][orig_prev]
                p_end = _shift_end(ps, params, orig_prev)
                p_on = _is_overnight(ps, params, orig_prev)
                orig_valid = [s for s in orig_valid if _calc_rest(p_end, orig_prev, p_on, original_off, s) >= params['min_rest']]
            
            if shifts[i][orig_next] is not None:
                ns, nb = shifts[i][orig_next]
                orig_valid = [s for s in orig_valid if _calc_rest(
                    _shift_end(s, params, original_off), original_off, _is_overnight(s, params, original_off),
                    orig_next, ns
                ) >= params['min_rest']]
            
            if not orig_valid:
                continue  # can't work original off-day either
            
            # Find best start on original off-day
            best_st, best_br, best_score = None, None, float('-inf')
            max_dem = max(demand[DAYS[original_off]]) if max(demand[DAYS[original_off]]) > 0 else 1
            for st in orig_valid:
                for br in _valid_breaks(st, params, original_off):
                    covered = _covers(st, br, original_off, params)
                    score = sum(max(0, demand[DAYS[td]][hr] - coverage[td][hr]) for td, hr in covered) / max_dem
                    if score > best_score:
                        best_st, best_br, best_score = st, br, score
            
            if best_st is not None:
                # Swap: make di the off-day, work on original_off
                day_off[i] = di
                shifts[i][original_off] = (best_st, best_br)
                for td, hr in _covers(best_st, best_br, original_off, params):
                    coverage[td][hr] += 1
    
    # ── MULTI-OFF-DAY EXTENSION ────────────────────────────────────────────
    num_off_per_da = 7 - working_days_param
    if num_off_per_da > 1:
        extra_off_needed = num_off_per_da - 1
        ext_off_count = [0] * 7
        for i in range(n):
            ext_off_count[day_off[i]] += 1
        daily_dem = [sum(demand[DAYS[di]][h] for h in range(24)) for di in range(7)]
        total_dem = sum(daily_dem)
        total_all_off = n * (7 - working_days_param)
        total_da_days_ext = n * working_days_param
        target_total_off = [0] * 7
        if total_dem > 0:
            for di in range(7):
                share = daily_dem[di] / total_dem
                tw = min(n, round(total_da_days_ext * share))
                target_total_off[di] = max(0, n - tw)
            diff = total_all_off - sum(target_total_off)
            sbd = sorted(range(7), key=lambda d: daily_dem[d])
            for j in range(abs(diff)):
                d = sbd[j % 7]
                if diff > 0 and target_total_off[d] < n:
                    target_total_off[d] += 1
                elif diff < 0 and target_total_off[d] > 0:
                    target_total_off[d] -= 1
        target_extra = [max(0, target_total_off[di] - ext_off_count[di]) for di in range(7)]
        extra_off_assigned = [0] * 7
        for i in range(n):
            primary_off = day_off[i]
            wdl = []
            for di in range(7):
                if di == primary_off or shifts[i][di] is None:
                    continue
                st, br = shifts[i][di]
                gi = sum(1 for td, hr in _covers(st, br, di, params) if coverage[td][hr] <= demand[DAYS[td]][hr])
                todi = ext_off_count[di] + extra_off_assigned[di]
                bp = max(0, todi - target_extra[di]) * (daily_dem[di] / max(1, sum(daily_dem)) * n)
                wdl.append((di, gi + bp))
            wdl.sort(key=lambda x: (x[1], x[0]))
            for idx in range(min(extra_off_needed, len(wdl))):
                rdi = wdl[idx][0]
                st, br = shifts[i][rdi]
                for td, hr in _covers(st, br, rdi, params):
                    coverage[td][hr] -= 1
                shifts[i][rdi] = None
                extra_off_assigned[rdi] += 1
    
    # Convert day_off to sets
    day_off = [{d} if isinstance(d, int) else d for d in day_off]
    if num_off_per_da > 1:
        for i in range(n):
            for di in range(7):
                if shifts[i][di] is None:
                    day_off[i].add(di)
    
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
                    sum(1 for td, hr in _covers(shifts[i][gap_day][0], shifts[i][gap_day][1], gap_day, params)
                        if coverage[td][hr] > demand[DAYS[td]][hr]) if shifts[i][gap_day] else 0,
                    i
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
        
        # Process gap slots above the mean weighted gap — data-derived cutoff
        # that naturally focuses on the worst gaps without an arbitrary limit.
        mean_gap_weight = sum(g[2] for g in gaps) / len(gaps) if gaps else 0
        significant_gaps = [g for g in gaps if g[2] >= mean_gap_weight]
        if not significant_gaps:
            significant_gaps = gaps[:1]  # at least try the worst one
        
        for gap_day, gap_hour, _ in significant_gaps:
            # v12.4: Deterministic DA ordering by coverage contribution (ascending)
            da_order = sorted(range(n), key=lambda i: (
                sum(1 for td, hr in _covers(shifts[i][gap_day][0], shifts[i][gap_day][1], gap_day, params)
                    if coverage[td][hr] > demand[DAYS[td]][hr]) if shifts[i][gap_day] else 0,
                i
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
    """Run optimization — single path for both flexible and non-flexible modes.
    
    v12.4: Removed _generate_demand_based_configs and config iteration.
    Uses single initial assignment with quota-based off-days + optimization passes.
    
    In fixed start mode (max_rest == min_rest), skips the per-day optimizer
    since it would break the same-start-every-day constraint.
    """
    shifts, day_off, coverage = _initial_assign_v12(demand, n, params)
    
    # In fixed start mode, choose optimizer behavior based on fixed_start_optimizer param:
    # 'strict'   — no optimizer, keep same start every day
    # 'post_off' — only change start on the day after weekly off
    # 'flexible' — full optimizer, can change any day (original behavior)
    max_rest = params.get('max_rest')
    min_rest = params.get('min_rest', 12)
    if max_rest is not None and max_rest == min_rest:
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
                # Greedy true-marginal-demand selection (same algorithm as _initial_assign_fixed_start)
                sh = params['shift_hours']
                all_starts = list(range(24))
                if not params.get('night_shift_enabled', True):
                    all_starts = [s for s in all_starts if s <= 24 - sh]
                if len(all_starts) > max_shifts_param:
                    selected = []
                    covered_hours = set()
                    remaining = list(all_starts)
                    for _ in range(max_shifts_param):
                        best_st = None
                        best_marginal = -1
                        for st in remaining:
                            br = _valid_breaks(st, params)[0]
                            marginal = 0
                            for di in range(7):
                                for td, hr in _covers(st, br, di, params):
                                    if (td, hr) not in covered_hours:
                                        marginal += demand[DAYS[di]][hr]
                            if marginal > best_marginal:
                                best_marginal = marginal
                                best_st = st
                        if best_st is None:
                            break
                        selected.append(best_st)
                        remaining.remove(best_st)
                        br = _valid_breaks(best_st, params)[0]
                        for di in range(7):
                            for td, hr in _covers(best_st, br, di, params):
                                covered_hours.add((td, hr))
                    allowed_starts = set(selected)
                else:
                    allowed_starts = set(all_starts)
            
            improved = True
            for _round in range(3):
                if not improved:
                    break
                improved = False
                for i in range(n):
                    for off_di in sorted(day_off[i]):
                        post_off_di = (off_di + 1) % 7
                        
                        if shifts[i][post_off_di] is None:
                            continue
                        
                        old_st, old_br = shifts[i][post_off_di]
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
                        
                        old_covered = _covers(old_st, old_br, post_off_di, params)
                        for td, hr in old_covered:
                            coverage[td][hr] -= 1
                        
                        day_gap_before = [0.0] * 7
                        daily_dem = [0.0] * 7
                        worst_ratio_day = 0
                        worst_ratio = -1.0
                        for di in range(7):
                            daily_dem[di] = sum(demand[DAYS[di]][h] for h in range(24))
                            if daily_dem[di] > 0:
                                day_gap_before[di] = sum(max(0, demand[DAYS[di]][h] - coverage[di][h]) for h in range(24))
                                r = day_gap_before[di] / daily_dem[di]
                                if r > worst_ratio:
                                    worst_ratio = r
                                    worst_ratio_day = di
                        
                        best_new_st, best_new_br, best_new_gap = None, None, gap
                        best_worst_day_improvement = float('-inf')
                        for st in candidates:
                            for br in _valid_breaks(st, params, post_off_di):
                                new_covered = _covers(st, br, post_off_di, params)
                                for td, hr in new_covered:
                                    coverage[td][hr] += 1
                                new_gap = _calc_gap(demand, coverage)
                                worst_day_gap_after = sum(
                                    max(0, demand[DAYS[worst_ratio_day]][h] - coverage[worst_ratio_day][h])
                                    for h in range(24)
                                )
                                worst_day_improvement = day_gap_before[worst_ratio_day] - worst_day_gap_after
                                if (new_gap < best_new_gap or
                                    (new_gap == best_new_gap and worst_day_improvement > best_worst_day_improvement)):
                                    best_new_gap = new_gap
                                    best_new_st, best_new_br = st, br
                                    best_worst_day_improvement = worst_day_improvement
                                for td, hr in new_covered:
                                    coverage[td][hr] -= 1
                        
                        if best_new_st is not None and best_new_gap < gap:
                            shifts[i][post_off_di] = (best_new_st, best_new_br)
                            for td, hr in _covers(best_new_st, best_new_br, post_off_di, params):
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

# =============================================================================
# MAIN ASSIGNMENT
# =============================================================================
def assign_shifts(da_list_df, demand_df, carryover_df=None, params=None):
    """Assign shifts to DAs. carryover_df is accepted for API compatibility but not used."""
    if params is None:
        params = get_params()
    
    flexible = params.get('flexible_day_off', False)
    mode = "flexible day-off" if flexible else "Fri/Sat enforced"
    print(f"Assigning shifts (v12.4 demand-driven proportional, {mode})...")
    
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
                        'Break_Hour': None, 'Break_Hour_2': None,
                        'Is_Day_Off': True
                    })
                else:
                    st, br = shift
                    br2 = _get_break2(st, br, params, di)
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
                        brk2 = int(shift['Break_Hour_2']) if pd.notna(shift.get('Break_Hour_2')) else -1
                        
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
                    brk2 = int(shift['Break_Hour_2']) if pd.notna(shift.get('Break_Hour_2')) else -1
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


# =============================================================================
# CARRYOVER DA RESHUFFLE
# =============================================================================
def reshuffle_das_for_carryover(shifts_df, carryover_df, params=None):
    """Reshuffle DA identities between W-1 Saturday and W+1 roster to minimize
    rest violations while keeping the optimal roster unchanged."""
    if params is None:
        params = get_params()
    
    if carryover_df is None or carryover_df.empty:
        return shifts_df, pd.DataFrame()
    
    min_rest = params['min_rest']
    shift_hours = params['shift_hours']
    
    result_shifts = shifts_df.copy()
    all_transitions = []
    
    for store in shifts_df['Store'].unique():
        store_shifts = shifts_df[shifts_df['Store'] == store]
        store_carry = carryover_df[carryover_df['Store'] == store] if 'Store' in carryover_df.columns else pd.DataFrame()
        
        if store_carry.empty:
            continue
        
        w1_das = {}
        for _, row in store_carry.iterrows():
            sat_end = row.get('Sat_Shift_End', row.get('Shift_End'))
            sat_start = row.get('Sat_Shift_Start', row.get('Shift_Start'))
            if pd.isna(sat_end):
                continue
            if pd.notna(sat_start):
                sat_start = int(sat_start)
            else:
                sat_start = (int(sat_end) - shift_hours) % 24
            w1_das[row['DA_ID']] = {'start': sat_start, 'end': int(sat_end)}
        
        w1_da_ids = sorted(w1_das.keys())
        sun_shifts = store_shifts[store_shifts['Day'] == 'Sun']
        w2_das = {}
        for _, row in sun_shifts.iterrows():
            if row['Is_Day_Off'] or pd.isna(row.get('Shift_Start')):
                w2_das[row['DA_ID']] = None
            else:
                w2_das[row['DA_ID']] = int(row['Shift_Start'])
        w2_da_ids = sorted(w2_das.keys())
        
        if len(w1_da_ids) != len(w2_da_ids):
            continue
        
        def _compute_rest(sat_end, sat_start, sun_start):
            if sun_start is None:
                return float('inf')
            is_on = sat_end > 0 and sat_end < sat_start
            if is_on:
                return sun_start - sat_end if sun_start >= sat_end else sun_start + 24 - sat_end
            elif sat_end == 0:
                return sun_start
            else:
                return (24 - sat_end) + sun_start
        
        def _sort_key(da_id):
            info = w1_das[da_id]
            e = info['end']
            s = info['start']
            is_on = e > 0 and e < s
            if is_on:
                return -100 - e
            elif e == 0:
                return -50
            else:
                return -(24 - e)
        
        w1_sorted = sorted(w1_da_ids, key=_sort_key)
        w2_available = set(w2_da_ids)
        mapping = {}
        
        for w1_da in w1_sorted:
            sat_info = w1_das[w1_da]
            best_w2 = None
            best_rest = float('inf')
            best_is_valid = False
            
            for w2_da in w2_available:
                sun_start = w2_das[w2_da]
                rest = _compute_rest(sat_info['end'], sat_info['start'], sun_start)
                is_valid = rest >= min_rest
                
                if is_valid and not best_is_valid:
                    best_w2 = w2_da
                    best_rest = rest
                    best_is_valid = True
                elif is_valid and best_is_valid and rest < best_rest:
                    best_w2 = w2_da
                    best_rest = rest
                elif not is_valid and not best_is_valid and rest > best_rest:
                    best_w2 = w2_da
                    best_rest = rest
            
            if best_w2 is not None:
                mapping[w1_da] = best_w2
                w2_available.remove(best_w2)
                sun_str = f"{w2_das[best_w2]:02d}:00" if w2_das[best_w2] is not None else "OFF"
                all_transitions.append({
                    'Store': store,
                    'W-1 DA': w1_da,
                    'W+1 DA': best_w2,
                    'Sat End': f"{sat_info['end']:02d}:00",
                    'Sun Start': sun_str,
                    'Rest': f"{best_rest}h" if best_rest < float('inf') else "OFF",
                    'Status': '✅' if best_is_valid else '❌',
                })
        
        reverse_map = {v: k for k, v in mapping.items()}
        mask = result_shifts['Store'] == store
        result_shifts.loc[mask, 'DA_ID'] = result_shifts.loc[mask, 'DA_ID'].map(
            lambda x: reverse_map.get(x, x)
        )
    
    transition_df = pd.DataFrame(all_transitions) if all_transitions else pd.DataFrame()
    violations = sum(1 for t in all_transitions if t['Status'] == '❌')
    total = len(all_transitions)
    print(f"      Reshuffle: {total} DAs matched, {total - violations} valid, {violations} violations")
    
    return result_shifts, transition_df
