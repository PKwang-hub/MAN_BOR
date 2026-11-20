#!/usr/bin/env python3
"""
AeroCast-TH Verification Engine
Automated ground-truth verification of convective nowcasts against airport METAR/SPECI reports.
Calculates ICAO/WMO standard scores: POD (Hit Rate), FAR (False Alarm Ratio), CSI (Threat Score),
and Mean Absolute Timing Error.
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
import json
import re
from typing import List, Optional, Dict

try:
    import taf_decoder
except ImportError:
    import aerocast_web.taf_decoder as taf_decoder

class Verdict(Enum):
    HIT = "HIT"                       # Predicted rain, rain observed in METAR
    MISS = "MISS"                     # Predicted no rain, but rain observed in METAR
    FALSE_ALARM = "FALSE_ALARM"       # Predicted rain, no rain observed in METAR
    CORRECT_NEGATIVE = "CORRECT_NEG"  # Predicted no rain, no rain observed

@dataclass
class ForecastRecord:
    forecast_id: str
    icao: str
    predicted_at_utc: str
    target_eta_utc: str
    window_start_utc: str
    window_end_utc: str
    predicted_intensity: str
    predicted_pop_percent: int
    taf_deficit: bool                 # True if official TAF had no rain / no TEMPO

@dataclass
class MetarRecord:
    icao: str
    timestamp_utc: str
    raw_text: str
    has_rain: bool
    rain_phenomena: List[str]         # e.g. ["+TSRA", "SHRA", "-RA"]

@dataclass
class VerificationResult:
    forecast: ForecastRecord
    verdict: Verdict
    actual_metars: List[MetarRecord]
    first_rain_observed_utc: Optional[str]
    timing_error_minutes: Optional[int]
    taf_rescued: bool                 # True if AeroCast alerted a TAF deficit and it hit
    notes: str

# Regex to detect precipitation phenomena in ICAO METAR tokens
RAIN_REGEX = re.compile(r'\b(-|\+|VC)?(TS|SH|FZ|BL)?(RA|DZ|GR|GS)\b|\bTS\b|\bVCTS\b')

def parse_metar_rain(raw_metar: str) -> tuple[bool, List[str]]:
    """Extracts rain and convective phenomena from a raw METAR string."""
    tokens = raw_metar.strip().split()
    rain_phenomena = []
    has_rain = False

    for token in tokens:
        # Ignore cloud group tokens like FEW020CB or SCT020TCU
        if re.search(r'\d{3}(CB|TCU)', token):
            continue
        match = RAIN_REGEX.search(token)
        if match:
            has_rain = True
            rain_phenomena.append(token)

    return has_rain, rain_phenomena

def check_taf_rain_forecast(raw_taf: str, target_utc: datetime) -> bool:
    """Checks whether official TAF forecast rain at target_utc per NOAA TAF Card standards."""
    try:
        decoded = taf_decoder.decode_taf(raw_taf)
        resolved = taf_decoder.get_forecast_for_time(decoded, target_utc)
        return resolved.has_forecast_rain
    except Exception:
        return False

def verify_forecast_event(forecast: ForecastRecord, metars: List[MetarRecord]) -> VerificationResult:
    """Verifies a single nowcast prediction against aerodrome METARs in the target window."""
    w_start = datetime.fromisoformat(forecast.window_start_utc)
    w_end = datetime.fromisoformat(forecast.window_end_utc)
    t_eta = datetime.fromisoformat(forecast.target_eta_utc)

    # Filter METARs inside the validation window
    relevant_metars = []
    rain_observed = False
    first_rain_time = None
    first_rain_phenomena = []

    for m in metars:
        m_time = datetime.fromisoformat(m.timestamp_utc)
        if w_start <= m_time <= w_end:
            relevant_metars.append(m)
            if m.has_rain and not rain_observed:
                rain_observed = True
                first_rain_time = m_time
                first_rain_phenomena = m.rain_phenomena

    timing_error = None
    if rain_observed and first_rain_time:
        timing_error = int((first_rain_time - t_eta).total_seconds() / 60)

    predicted_rain = forecast.predicted_pop_percent >= 50

    if predicted_rain and rain_observed:
        verdict = Verdict.HIT
        taf_rescued = forecast.taf_deficit
        notes = f"Precipitation observed ({', '.join(first_rain_phenomena)}). Timing delta: {timing_error:+d} min."
    elif predicted_rain and not rain_observed:
        verdict = Verdict.FALSE_ALARM
        taf_rescued = False
        notes = "No precipitation recorded in METARs during the verification window."
    elif not predicted_rain and rain_observed:
        verdict = Verdict.MISS
        taf_rescued = False
        notes = f"Unpredicted rain observed ({', '.join(first_rain_phenomena)})."
    else:
        verdict = Verdict.CORRECT_NEGATIVE
        taf_rescued = False
        notes = "No rain predicted and none observed. Clear conditions verified."

    return VerificationResult(
        forecast=forecast,
        verdict=verdict,
        actual_metars=relevant_metars,
        first_rain_observed_utc=first_rain_time.isoformat() if first_rain_time else None,
        timing_error_minutes=timing_error,
        taf_rescued=taf_rescued,
        notes=notes
    )

def compute_aggregate_metrics(results: List[VerificationResult]) -> dict:
    """Computes ICAO/WMO Contingency Table & Accuracy KPIs."""
    hits = sum(1 for r in results if r.verdict == Verdict.HIT)
    misses = sum(1 for r in results if r.verdict == Verdict.MISS)
    false_alarms = sum(1 for r in results if r.verdict == Verdict.FALSE_ALARM)
    correct_negs = sum(1 for r in results if r.verdict == Verdict.CORRECT_NEGATIVE)
    taf_rescued = sum(1 for r in results if r.taf_rescued)
    total_events = len(results)

    # Probability of Detection (POD / Hit Rate)
    pod = (hits / (hits + misses) * 100) if (hits + misses) > 0 else 100.0

    # False Alarm Ratio (FAR)
    far = (false_alarms / (hits + false_alarms) * 100) if (hits + false_alarms) > 0 else 0.0

    # Critical Success Index (CSI / Threat Score)
    csi = (hits / (hits + misses + false_alarms) * 100) if (hits + misses + false_alarms) > 0 else 100.0

    # Timing error mean absolute value
    hit_errors = [abs(r.timing_error_minutes) for r in results if r.timing_error_minutes is not None]
    mean_timing_error = (sum(hit_errors) / len(hit_errors)) if hit_errors else 0.0

    # Total unforecast TAF rain events
    taf_deficits_total = sum(1 for r in results if r.forecast.taf_deficit and r.verdict == Verdict.HIT)

    return {
        "sample_size": total_events,
        "hits": hits,
        "misses": misses,
        "false_alarms": false_alarms,
        "correct_negatives": correct_negs,
        "pod_hit_rate_percent": round(pod, 1),
        "far_false_alarm_percent": round(far, 1),
        "csi_threat_score_percent": round(csi, 1),
        "mean_timing_error_minutes": round(mean_timing_error, 1),
        "taf_deficit_rescues": taf_rescued
    }
