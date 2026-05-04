"""
Shared break placement utilities using demand-backward algorithm.

This module provides demand-aware break placement for all retained roster engines.
Breaks are placed at the lowest-demand eligible positions that satisfy the
max_continuous work constraint.
"""


def compute_work_segments(break_positions, shift_hours, start):
    """
    Compute the lengths of work segments given break positions within a shift.

    Break positions are absolute hours (0-23). We convert them to offsets
    within the shift, then derive segment lengths between breaks.

    Args:
        break_positions: List of absolute break hours (0-23).
        shift_hours: Total shift duration in hours.
        start: Shift start hour (0-23).

    Returns:
        List of segment lengths (work hours between breaks / shift boundaries).
    """
    if not break_positions:
        return [shift_hours]

    # Convert absolute hours to offsets within the shift
    offsets = sorted(((h - start) % 24) for h in break_positions)

    segments = []
    prev = 0
    for off in offsets:
        segments.append(off - prev)
        prev = off + 1  # +1 because the break itself occupies 1 hour
    # Remaining segment after last break
    segments.append(shift_hours - prev)
    return segments


def place_breaks(start, shift_hours, break_hours, max_continuous, demand_row=None):
    """
    Return optimal break position(s) using demand-backward placement.

    Args:
        start: Shift start hour (0-23).
        shift_hours: Total shift duration in hours.
        break_hours: Number of break hours (0, 1, or 2).
        max_continuous: Maximum continuous work hours before break required.
        demand_row: Dict/Series of {hour: demand_count} for the day (optional).
                    If None, falls back to placing break at max_continuous offset.

    Returns:
        List of break hour(s). Length equals break_hours.
        Empty list if break_hours == 0.
    """
    if break_hours == 0:
        return []

    # Clamp break_hours if it exceeds half the shift
    break_hours = min(break_hours, shift_hours // 2)
    if break_hours == 0:
        return []

    # Build eligible positions: offsets 1 through shift_hours-1
    # Each position is (absolute_hour, offset_within_shift)
    eligible = []
    for pos in range(1, shift_hours):
        hour = (start + pos) % 24
        eligible.append((hour, pos))

    if break_hours == 1:
        return _place_single_break(eligible, start, shift_hours, max_continuous, demand_row)

    if break_hours == 2:
        return _place_double_break(eligible, start, shift_hours, max_continuous, demand_row)

    return []


def _place_single_break(eligible, start, shift_hours, max_continuous, demand_row):
    """Place a single break at the lowest-demand valid position."""
    valid = []
    for hour, pos in eligible:
        segments = compute_work_segments([hour], shift_hours, start)
        if all(seg <= max_continuous for seg in segments):
            valid.append(hour)

    if not valid:
        # Fallback: place at max_continuous offset
        return [(start + max_continuous) % 24]

    if demand_row is None:
        # No demand info — pick first valid (closest to max_continuous offset)
        return [valid[0]]

    # Sort by demand ascending, then return lowest-demand position
    valid.sort(key=lambda h: _get_demand(demand_row, h))
    return [valid[0]]


def _place_double_break(eligible, start, shift_hours, max_continuous, demand_row):
    """Place two breaks at the lowest total demand valid pair.
    
    When multiple pairs have the same demand score, prefer the pair whose
    shortest work segment is longest (most balanced placement).  This avoids
    degenerate placements like a 1-hour work segment right after shift start.
    """
    best_pair = None
    best_demand = float('inf')
    best_min_seg = -1  # tiebreaker: largest minimum segment wins

    for i, (h1, p1) in enumerate(eligible):
        for h2, p2 in eligible[i + 1:]:
            segments = compute_work_segments([h1, h2], shift_hours, start)
            if all(seg <= max_continuous for seg in segments):
                total_demand = _get_demand(demand_row, h1) + _get_demand(demand_row, h2)
                min_seg = min(segments)
                if (total_demand < best_demand
                        or (total_demand == best_demand and min_seg > best_min_seg)):
                    best_demand = total_demand
                    best_min_seg = min_seg
                    best_pair = [h1, h2]

    if best_pair is not None:
        return best_pair

    # Fallback: try to place two breaks using max_continuous spacing
    fb1 = (start + max_continuous) % 24
    # Second break: max_continuous after first break + 1
    fb2 = (start + max_continuous + 1 + max_continuous) % 24
    # Verify the second fallback is still within the shift
    fb2_offset = (fb2 - start) % 24
    if 1 <= fb2_offset < shift_hours:
        return [fb1, fb2]

    # Last resort: place at max_continuous and max_continuous+1
    fb2 = (fb1 + 1) % 24
    return [fb1, fb2]


def _get_demand(demand_row, hour):
    """Safely retrieve demand for an hour from a dict-like or list demand_row."""
    if demand_row is None:
        return 0
    # Support list/tuple indexing (engines pass demand as list of 24 values)
    if isinstance(demand_row, (list, tuple)):
        if 0 <= hour < len(demand_row):
            return demand_row[hour]
        return 0
    # Support both string and int keys (pandas columns can be either)
    val = demand_row.get(hour, None)
    if val is None:
        val = demand_row.get(str(hour), 0)
    return val if val is not None else 0
