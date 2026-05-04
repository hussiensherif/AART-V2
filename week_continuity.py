"""
AART — Week-over-Week Roster Continuity
========================================

Parses an *exported* AART roster Excel file from the previous week and
converts Saturday overnight shifts into engine-compatible continuity
constraints for the current week.

The exported files produced by ``app_roster_weekly.py`` typically
contain one or more of the following sheets:

* ``Summary``
* ``{Store}_Roster`` — hourly roster per store
* ``{Store}_Shifts`` — shift details per store
* ``Shift_Details``  — combined shift details (single-store exports)
* ``Sunday_Carryover`` — pre-computed carryover (fastest path)
* ``Hourly_Roster``  — single-store hourly roster

Public API
----------

parse_previous_week_roster(uploaded_file, min_rest=12, shift_hours=10)
    Returns a ``dict`` describing previous-week Saturday overnight DAs
    and their Sunday spillover hours.

build_continuity_constraints(prev_week_data, current_params, strategy)
    Turns the parsed data into engine-compatible continuity constraints.

build_continuity_report_df(prev_week_data, constraints=None)
    Returns a tidy ``pandas.DataFrame`` for the week-continuity report.

build_week_continuity_sheet(prev_week_data, strategy)
    Builds the ``Week_Continuity`` sheet (``pandas.DataFrame``) for the
    downloaded Excel file.

Notes
-----
* The parser is lenient with column and sheet naming.
* All times are integer hours on a 24-hour clock.
* An overnight shift is detected when
  ``Shift_Start + shift_hours > 24`` (equivalently ``Shift_End < Shift_Start``).
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_STD_COLS = {
    "DA_ID": "DA_ID",
    "Store": "Store",
    "DSP": "DSP",
    "DSP_Code": "DSP_Code",
    "DSP Name": "DSP",
    "DSP Code": "DSP_Code",
    "Station": "Store",
    "Day": "Day",
    "Shift_Start": "Shift_Start",
    "Shift_End": "Shift_End",
    "Shift Start": "Shift_Start",
    "Shift End": "Shift_End",
    "Is_Day_Off": "Is_Day_Off",
    "Is Day Off": "Is_Day_Off",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of *df* with normalized column names."""
    new_cols = {}
    for c in df.columns:
        stripped = str(c).strip()
        new_cols[c] = _STD_COLS.get(stripped, stripped)
    return df.rename(columns=new_cols)


def _to_int_hour(val) -> Optional[int]:
    """Coerce a cell value to an integer hour in [0, 23] or ``None``."""
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    # "19:00" -> 19
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        m = re.match(r"^(\d{1,2})(?::\d{1,2})?$", s)
        if m:
            try:
                h = int(m.group(1))
                return h if 0 <= h < 24 else None
            except ValueError:
                return None
        try:
            h = int(float(s))
            return h if 0 <= h < 24 else None
        except ValueError:
            return None
    try:
        h = int(float(val))
    except (ValueError, TypeError):
        return None
    return h if 0 <= h < 24 else None


def _detect_week_from_filename(name: Optional[str]) -> Optional[str]:
    """Extract ``WKxx`` from a filename like ``DA_Roster_WK16_...``."""
    if not name:
        return None
    m = re.search(r"WK(\d{1,2})", str(name), re.IGNORECASE)
    if m:
        return f"WK{int(m.group(1))}"
    m = re.search(r"Week[_\s]*(\d{1,2})", str(name), re.IGNORECASE)
    if m:
        return f"WK{int(m.group(1))}"
    return None


def _spillover_hours(end_hour: int) -> List[int]:
    """Return the list of Sunday hours a shift ending at *end_hour* covers.

    An overnight shift that ends at 05:00 spills into Sunday hours 0..4.
    A shift ending at 00:00 returns ``[]`` (no spillover).
    """
    if end_hour <= 0 or end_hour > 23:
        return []
    return list(range(0, end_hour))


def _classify_overnight(start: int, end: int, shift_hours: int) -> bool:
    """Return ``True`` when this Saturday shift spills into Sunday."""
    if start is None or end is None:
        return False
    # Wrap-around end time (end < start) is the canonical signal.
    if end < start:
        return True
    # Also classify shifts that would overflow 24h based on nominal length.
    if start + shift_hours > 24:
        return True
    return False


def _extract_sat_shifts_from_sheet(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Extract Saturday working shifts from a Shift_Details-like sheet."""
    if df is None or df.empty:
        return []
    df = _normalize_columns(df)

    required = {"DA_ID", "Store", "Day"}
    if not required.issubset(set(df.columns)):
        return []

    rows: List[Dict[str, Any]] = []
    # Filter Sat rows that are not day-off
    mask_day = df["Day"].astype(str).str.strip().str.lower().isin(
        {"sat", "saturday"}
    )
    if "Is_Day_Off" in df.columns:
        day_off = df["Is_Day_Off"]
        if day_off.dtype == object:
            day_off = day_off.astype(str).str.strip().str.lower().isin(
                {"true", "1", "yes"}
            )
        mask_work = ~day_off.fillna(False).astype(bool)
    else:
        mask_work = pd.Series(True, index=df.index)

    sat_rows = df[mask_day & mask_work]

    for _, r in sat_rows.iterrows():
        start = _to_int_hour(r.get("Shift_Start"))
        end = _to_int_hour(r.get("Shift_End"))
        if start is None or end is None:
            continue
        rows.append(
            {
                "DA_ID": str(r.get("DA_ID", "")).strip(),
                "Store": str(r.get("Store", "")).strip(),
                "DSP": str(r.get("DSP", "")).strip() if "DSP" in df.columns else "",
                "DSP_Code": (
                    str(r.get("DSP_Code", "")).strip()
                    if "DSP_Code" in df.columns else ""
                ),
                "Sat_Shift_Start": start,
                "Sat_Shift_End": end,
            }
        )
    return rows


def _parse_sunday_carryover_sheet(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Parse a ``Sunday_Carryover`` sheet (if present) directly."""
    if df is None or df.empty:
        return []
    df = _normalize_columns(df)
    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        start_raw = r.get("Sat_Shift_Start")
        # The sheet stored it as a string like "19:00"; normalize
        start = _to_int_hour(start_raw)
        end = None
        # Sheet column is "Sunday_Spillover_Hours" like "00:00-05:00"
        spill = r.get("Sunday_Spillover_Hours")
        if isinstance(spill, str) and "-" in spill:
            tail = spill.split("-", 1)[1]
            end = _to_int_hour(tail)
        if start is None or end is None:
            continue
        rows.append(
            {
                "DA_ID": str(r.get("DA_ID", "")).strip(),
                "Store": str(r.get("Store", "")).strip(),
                "DSP": str(r.get("DSP", "")).strip() if "DSP" in df.columns else "",
                "DSP_Code": "",
                "Sat_Shift_Start": start,
                "Sat_Shift_End": end,
            }
        )
    return rows


def parse_previous_week_roster(
    uploaded_file,
    min_rest: int = 12,
    shift_hours: int = 10,
) -> Dict[str, Any]:
    """Parse a previously exported AART roster Excel file.

    Parameters
    ----------
    uploaded_file : file-like or bytes or str path
        The Excel file uploaded via ``st.file_uploader`` (or a raw path).
    min_rest : int, default 12
        Minimum rest hours enforced between shifts. Used to derive the
        minimum valid Sunday start hour for carryover DAs.
    shift_hours : int, default 10
        Nominal shift length, used to detect overnight shifts and
        compute fallback end times.

    Returns
    -------
    dict
        See module docstring for schema. Always returns a dict, even
        when the file is empty or unparseable — callers should check
        ``total_overnight_das``.
    """
    # Determine filename (if uploaded via Streamlit the object has .name)
    file_name = getattr(uploaded_file, "name", None)
    week_detected = _detect_week_from_filename(file_name)

    # Reset pointer if possible so we can read fully.
    if hasattr(uploaded_file, "seek"):
        try:
            uploaded_file.seek(0)
        except Exception:
            pass

    try:
        xls = pd.ExcelFile(uploaded_file)
    except Exception as exc:  # pragma: no cover — surface to caller
        return {
            "stores": {},
            "week_detected": week_detected,
            "export_timestamp": None,
            "total_stores": 0,
            "total_overnight_das": 0,
            "error": f"Unable to open Excel file: {exc}",
        }

    sheet_names = list(xls.sheet_names)

    # --- Strategy 1: fast path — Sunday_Carryover ---------------------
    sat_records: List[Dict[str, Any]] = []
    if "Sunday_Carryover" in sheet_names:
        try:
            df = pd.read_excel(xls, sheet_name="Sunday_Carryover")
            sat_records.extend(_parse_sunday_carryover_sheet(df))
        except Exception:
            pass

    # --- Strategy 2: Shift_Details (single-store export) --------------
    if not sat_records and "Shift_Details" in sheet_names:
        try:
            df = pd.read_excel(xls, sheet_name="Shift_Details")
            sat_records.extend(_extract_sat_shifts_from_sheet(df))
        except Exception:
            pass

    # --- Strategy 3: {Store}_Shifts per-store sheets ------------------
    if not sat_records:
        for sheet in sheet_names:
            if str(sheet).endswith("_Shifts"):
                try:
                    df = pd.read_excel(xls, sheet_name=sheet)
                except Exception:
                    continue
                sat_records.extend(_extract_sat_shifts_from_sheet(df))

    # De-duplicate by (DA_ID, Store)
    seen: Set[Tuple[str, str]] = set()
    deduped: List[Dict[str, Any]] = []
    for rec in sat_records:
        key = (rec.get("DA_ID", ""), rec.get("Store", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rec)

    # Build per-store aggregates, keeping only overnight DAs.
    stores: Dict[str, Dict[str, Any]] = {}
    total_overnight = 0
    total_sat = 0

    for rec in deduped:
        store = rec.get("Store", "")
        if not store:
            continue
        start = rec.get("Sat_Shift_Start")
        end = rec.get("Sat_Shift_End")
        total_sat += 1

        store_entry = stores.setdefault(
            store,
            {
                "overnight_das": [],
                "total_sat_das": 0,
                "overnight_count": 0,
                "coverage_hours": {},
            },
        )
        store_entry["total_sat_das"] += 1

        is_overnight = _classify_overnight(start, end, shift_hours)
        if not is_overnight:
            continue

        spillover = _spillover_hours(end)
        if not spillover:
            # Overnight detected but no Sunday spillover — skip.
            continue

        min_sun_start = (end + min_rest) % 24
        # A "tight" start beyond 20:00 is flagged as risky (very late start
        # would effectively remove them from Sunday operations entirely).
        rest_conflict_risk = min_sun_start > 20 or min_sun_start == 0

        store_entry["overnight_das"].append(
            {
                "DA_ID": rec.get("DA_ID", ""),
                "DSP": rec.get("DSP", ""),
                "DSP_Code": rec.get("DSP_Code", ""),
                "sat_shift_start": int(start),
                "sat_shift_end": int(end),
                "is_overnight": True,
                "spillover_hours": spillover,
                "min_sunday_start": int(min_sun_start),
                "rest_conflict_risk": bool(rest_conflict_risk),
            }
        )
        store_entry["overnight_count"] += 1
        total_overnight += 1

        for h in spillover:
            store_entry["coverage_hours"][h] = (
                store_entry["coverage_hours"].get(h, 0) + 1
            )

    return {
        "stores": stores,
        "week_detected": week_detected,
        "export_timestamp": datetime.now().isoformat(timespec="seconds"),
        "total_stores": len(stores),
        "total_overnight_das": total_overnight,
        "total_sat_das": total_sat,
    }


# ---------------------------------------------------------------------------
# Continuity constraints for the engine
# ---------------------------------------------------------------------------

STRATEGY_FULL = "full_continuity"
STRATEGY_COVERAGE = "coverage_only"
STRATEGY_FLEX = "flexible_handoff"


def build_continuity_constraints(
    prev_week_data: Mapping[str, Any],
    current_params: Mapping[str, Any],
    strategy: str = STRATEGY_FULL,
) -> Dict[str, Any]:
    """Convert parsed previous-week data into engine-compatible constraints.

    Parameters
    ----------
    prev_week_data : mapping
        Output of :func:`parse_previous_week_roster`.
    current_params : mapping
        Current week's working-rules params (``min_rest``, ``shift_hours``,
        etc.). Only used for derivation of ``min_sunday_start``.
    strategy : str
        One of ``'full_continuity'``, ``'coverage_only'``,
        ``'flexible_handoff'``.

    Returns
    -------
    dict
        ``{
            'carryover_excel_data': [...],
            'sunday_blocked_da_ids': set(),
            'sunday_late_starts': {DA_ID: hour},
            'coverage_boost': {store: {hour: count}},
            'strategy': <strategy>,
        }``
    """
    min_rest = int(current_params.get("min_rest", 12))

    carryover_excel_data: List[Dict[str, Any]] = []
    sunday_blocked: Set[str] = set()
    sunday_late_starts: Dict[str, int] = {}
    coverage_boost: Dict[str, Dict[int, int]] = {}

    stores = prev_week_data.get("stores", {}) or {}

    for store, data in stores.items():
        # Coverage boost works regardless of strategy — we also always
        # produce this so it can be inspected in the report.
        hours = data.get("coverage_hours", {}) or {}
        if hours:
            coverage_boost[store] = dict(hours)

        for da in data.get("overnight_das", []) or []:
            da_id = da.get("DA_ID", "")
            sat_start = int(da.get("sat_shift_start", 0))
            sat_end = int(da.get("sat_shift_end", 0))
            # Re-derive min_sunday_start against the *current* week's min_rest
            min_sun_start = (sat_end + min_rest) % 24
            # >22 (and not 0) means "start after 22:00 Sunday" which the
            # engine treats as effectively no Sunday shift.
            too_late = (min_sun_start > 22) and (min_sun_start != 0)

            if strategy in (STRATEGY_FULL, STRATEGY_FLEX):
                carryover_excel_data.append(
                    {
                        "DA_ID": da_id,
                        "Store": store,
                        "DSP": da.get("DSP", ""),
                        "DSP_Code": da.get("DSP_Code", ""),
                        "Sat_Shift_Start": sat_start,
                        "Sat_Shift_End": sat_end,
                        "Min_Sunday_Start": int(min_sun_start),
                    }
                )
                if da_id:
                    if too_late:
                        sunday_blocked.add(da_id)
                    else:
                        sunday_late_starts[da_id] = int(min_sun_start)
            elif strategy == STRATEGY_COVERAGE:
                # Anonymous coverage — we still record the end time so the
                # engine can credit the hourly count.
                carryover_excel_data.append(
                    {
                        "DA_ID": da_id or f"CARRY-{store}-{len(carryover_excel_data):03d}",
                        "Store": store,
                        "DSP": da.get("DSP", ""),
                        "DSP_Code": da.get("DSP_Code", ""),
                        "Sat_Shift_Start": sat_start,
                        "Sat_Shift_End": sat_end,
                    }
                )

    return {
        "carryover_excel_data": carryover_excel_data,
        "sunday_blocked_da_ids": sunday_blocked,
        "sunday_late_starts": sunday_late_starts,
        "coverage_boost": coverage_boost,
        "strategy": strategy,
    }


# ---------------------------------------------------------------------------
# Reports / display helpers
# ---------------------------------------------------------------------------

def build_continuity_report_df(
    prev_week_data: Mapping[str, Any],
    constraints: Optional[Mapping[str, Any]] = None,
) -> pd.DataFrame:
    """Build the per-store summary table for the continuity report UI."""
    rows: List[Dict[str, Any]] = []
    stores = prev_week_data.get("stores", {}) or {}
    blocked = (constraints or {}).get("sunday_blocked_da_ids", set()) or set()

    for store, data in sorted(stores.items()):
        overnight = data.get("overnight_das", []) or []
        risk_count = sum(1 for d in overnight if d.get("rest_conflict_risk"))
        connected = sum(
            1 for d in overnight if d.get("DA_ID") and d["DA_ID"] not in blocked
        )
        violations = sum(1 for d in overnight if d.get("DA_ID") in blocked)

        earliest = None
        for d in overnight:
            v = d.get("min_sunday_start")
            if v is None:
                continue
            earliest = v if earliest is None else min(earliest, v)

        rows.append(
            {
                "Store": store,
                "Overnight_DAs": len(overnight),
                "Connected": connected,
                "Violations": violations,
                "Risk_Flags": risk_count,
                "Earliest_Sun_Start": (
                    f"{earliest:02d}:00" if earliest is not None else "—"
                ),
                "Status": "✅" if violations == 0 else "⚠️",
            }
        )
    return pd.DataFrame(rows)


def build_store_detail_df(prev_week_data: Mapping[str, Any], store: str) -> pd.DataFrame:
    """Build a per-DA detail table for a single store."""
    data = (prev_week_data.get("stores", {}) or {}).get(store)
    if not data:
        return pd.DataFrame()

    rows = []
    for d in data.get("overnight_das", []) or []:
        if d.get("rest_conflict_risk"):
            status = "🔴 Violation risk"
        elif d.get("min_sunday_start", 0) >= 17:
            status = "🟡 Tight rest"
        else:
            status = "🟢 OK"
        rows.append(
            {
                "DA_ID": d.get("DA_ID", ""),
                "DSP": d.get("DSP", ""),
                "Sat_Start": f"{int(d.get('sat_shift_start', 0)):02d}:00",
                "Sat_End": f"{int(d.get('sat_shift_end', 0)):02d}:00",
                "Min_Sun_Start": f"{int(d.get('min_sunday_start', 0)):02d}:00",
                "Status": status,
            }
        )
    return pd.DataFrame(rows)


def build_week_continuity_sheet(
    prev_week_data: Mapping[str, Any],
    strategy: str,
    sunday_shifts_by_da: Optional[Mapping[str, int]] = None,
    min_rest: int = 12,
) -> pd.DataFrame:
    """Build the ``Week_Continuity`` sheet for the Excel download.

    Parameters
    ----------
    prev_week_data : mapping
        Parsed previous-week data.
    strategy : str
        The continuity strategy that was used.
    sunday_shifts_by_da : mapping, optional
        Map of ``DA_ID -> Sunday shift start hour`` from the generated
        current-week roster. When provided, rest-hour compliance is
        computed precisely; otherwise it is estimated.
    min_rest : int
        Minimum rest hours enforced by the current week's rules.
    """
    rows: List[Dict[str, Any]] = []
    stores = prev_week_data.get("stores", {}) or {}
    sunday_shifts_by_da = sunday_shifts_by_da or {}

    for store, data in sorted(stores.items()):
        for d in data.get("overnight_das", []) or []:
            da_id = d.get("DA_ID", "")
            sat_end = int(d.get("sat_shift_end", 0))
            sun_start = sunday_shifts_by_da.get(da_id)
            if sun_start is None:
                status = "Sunday Off"
                rest_hours = None
            else:
                # Rest between Sat shift end (Sunday 00:sat_end) and Sunday start
                rest_hours = (sun_start - sat_end) % 24
                if rest_hours >= min_rest:
                    status = "OK"
                else:
                    status = "⚠️ Short rest"
            rows.append(
                {
                    "Store": store,
                    "DA_ID_Previous": da_id,
                    "DA_ID_Current": da_id if sun_start is not None else "",
                    "Sat_Shift_End": f"{sat_end:02d}:00",
                    "Sun_Shift_Start": (
                        f"{int(sun_start):02d}:00" if sun_start is not None else ""
                    ),
                    "Rest_Hours": rest_hours if rest_hours is not None else "",
                    "Status": status,
                    "Strategy_Used": strategy,
                }
            )

    return pd.DataFrame(rows)
