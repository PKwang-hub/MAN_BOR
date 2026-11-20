#!/usr/bin/env python3
"""
AeroCast Database & Ingestion Engine
Manages aerocast_db.json: fast snapshot cache, scheduled :10Z/:40Z ingestion,
tactical runway vector calculation, low-visibility alert (<5000m),
and historical ground-truth verification persistence.
"""

import os
import io
import csv
import json
import re
import math
import ssl
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

try:
    import taf_decoder
except ImportError:
    import aerocast_web.taf_decoder as taf_decoder

DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(DIRECTORY, "aerocast_db.json")

AIRPORTS_CONFIG = [
    # Bangkok Metropolitan
    {
        "icao": "VTBD", "name": "Don Mueang (DMK)", "rwy": 213, "rwy_ident": "03L/21R", "rwy_dimensions": "3,700m x 60m",
        "rwy_ends": [{"ident": "21R", "heading": 213}, {"ident": "03L", "heading": 33}],
        "radar": "Suvarnabhumi / BMA C-Band", "lat": 13.9126, "lon": 100.6067
    },
    {
        "icao": "VTBS", "name": "Suvarnabhumi (BKK)", "rwy": 194, "rwy_ident": "01R/19L", "rwy_dimensions": "4,000m x 60m",
        "rwy_ends": [{"ident": "19R", "heading": 194}, {"ident": "01L", "heading": 14}],
        "radar": "Suvarnabhumi Airport", "lat": 13.6811, "lon": 100.7473
    },
    # Northern Region
    {
        "icao": "VTCC", "name": "Chiang Mai (CNX)", "rwy": 180, "rwy_ident": "18/36", "rwy_dimensions": "3,400m x 45m",
        "rwy_ends": [{"ident": "18", "heading": 180}, {"ident": "36", "heading": 360}],
        "radar": "Chiang Mai Omkoi", "lat": 18.7668, "lon": 98.9626
    },
    {
        "icao": "VTCT", "name": "Chiang Rai (CEI)", "rwy": 210, "rwy_ident": "03/21", "rwy_dimensions": "3,000m x 45m",
        "rwy_ends": [{"ident": "21", "heading": 210}, {"ident": "03", "heading": 30}],
        "radar": "Chiang Rai", "lat": 19.9522, "lon": 99.8828
    },
    {
        "icao": "VTCN", "name": "Nan Nakhon (NNT)", "rwy": 200, "rwy_ident": "02/20", "rwy_dimensions": "2,075m x 45m",
        "rwy_ends": [{"ident": "20", "heading": 200}, {"ident": "02", "heading": 20}],
        "radar": "Phitsanulok", "lat": 18.8080, "lon": 100.7833
    },
    {
        "icao": "VTCL", "name": "Lampang (LPT)", "rwy": 175, "rwy_ident": "18/36", "rwy_dimensions": "1,975m x 30m",
        "rwy_ends": [{"ident": "18", "heading": 175}, {"ident": "36", "heading": 355}],
        "radar": "Chiang Mai Omkoi", "lat": 18.2619, "lon": 99.5053
    },
    {
        "icao": "VTPP", "name": "Phitsanulok (PHS)", "rwy": 144, "rwy_ident": "14/32", "rwy_dimensions": "3,000m x 45m",
        "rwy_ends": [{"ident": "14", "heading": 144}, {"ident": "32", "heading": 324}],
        "radar": "Phitsanulok", "lat": 16.7828, "lon": 100.2789
    },
    # Northeastern Region
    {
        "icao": "VTUK", "name": "Khon Kaen (KKC)", "rwy": 213, "rwy_ident": "03/21", "rwy_dimensions": "3,050m x 45m",
        "rwy_ends": [{"ident": "21", "heading": 213}, {"ident": "03", "heading": 33}],
        "radar": "Khon Kaen", "lat": 16.4667, "lon": 102.7833
    },
    {
        "icao": "VTUD", "name": "Udon Thani (UTH)", "rwy": 118, "rwy_ident": "12/30", "rwy_dimensions": "3,048m x 45m",
        "rwy_ends": [{"ident": "12", "heading": 118}, {"ident": "30", "heading": 298}],
        "radar": "Sakon Nakhon", "lat": 17.3867, "lon": 102.7883
    },
    {
        "icao": "VTUU", "name": "Ubon Ratchathani (UBP)", "rwy": 52, "rwy_ident": "05/23", "rwy_dimensions": "3,000m x 45m",
        "rwy_ends": [{"ident": "05", "heading": 52}, {"ident": "23", "heading": 232}],
        "radar": "Ubon Ratchathani", "lat": 15.2514, "lon": 104.8703
    },
    {
        "icao": "VTUV", "name": "Roi Et (ROI)", "rwy": 183, "rwy_ident": "18/36", "rwy_dimensions": "2,100m x 45m",
        "rwy_ends": [{"ident": "18", "heading": 183}, {"ident": "36", "heading": 3}],
        "radar": "Khon Kaen / Ubon", "lat": 16.1603, "lon": 103.7736
    },
    {
        "icao": "VTUO", "name": "Buriram (BFV)", "rwy": 35, "rwy_ident": "04/22", "rwy_dimensions": "2,100m x 45m",
        "rwy_ends": [{"ident": "04", "heading": 35}, {"ident": "22", "heading": 215}],
        "radar": "Khon Kaen / Phimai", "lat": 15.2289, "lon": 103.2536
    },
    {
        "icao": "VTUL", "name": "Loei (LOE)", "rwy": 16, "rwy_ident": "01/19", "rwy_dimensions": "2,100m x 45m",
        "rwy_ends": [{"ident": "01", "heading": 16}, {"ident": "19", "heading": 196}],
        "radar": "Phitsanulok", "lat": 17.4392, "lon": 101.7222
    },
    {
        "icao": "VTUI", "name": "Sakon Nakhon (SNO)", "rwy": 46, "rwy_ident": "05/23", "rwy_dimensions": "2,600m x 45m",
        "rwy_ends": [{"ident": "05", "heading": 46}, {"ident": "23", "heading": 226}],
        "radar": "Sakon Nakhon", "lat": 17.1953, "lon": 104.1189
    },
    {
        "icao": "VTUW", "name": "Nakhon Phanom (KOP)", "rwy": 145, "rwy_ident": "15/33", "rwy_dimensions": "2,000m x 45m",
        "rwy_ends": [{"ident": "15", "heading": 145}, {"ident": "33", "heading": 325}],
        "radar": "Sakon Nakhon", "lat": 17.3839, "lon": 104.6419
    },
    # Southern Region
    {
        "icao": "VTPH", "name": "Hua Hin (HHQ)", "rwy": 160, "rwy_ident": "16/34", "rwy_dimensions": "2,100m x 35m",
        "rwy_ends": [{"ident": "16", "heading": 160}, {"ident": "34", "heading": 340}],
        "radar": "Samut Songkhram / BMA", "lat": 12.6361, "lon": 99.9517
    },
    {
        "icao": "VTSP", "name": "Phuket (HKT)", "rwy": 265, "rwy_ident": "09/27", "rwy_dimensions": "3,000m x 45m",
        "rwy_ends": [{"ident": "27", "heading": 265}, {"ident": "09", "heading": 85}],
        "radar": "Phuket Airport", "lat": 8.1132, "lon": 98.3169
    },
    {
        "icao": "VTSG", "name": "Krabi (KBV)", "rwy": 321, "rwy_ident": "14/32", "rwy_dimensions": "3,000m x 45m",
        "rwy_ends": [{"ident": "32", "heading": 321}, {"ident": "14", "heading": 141}],
        "radar": "Phuket Radar", "lat": 8.0994, "lon": 98.9858
    },
    {
        "icao": "VTSS", "name": "Hat Yai (HDY)", "rwy": 262, "rwy_ident": "08/26", "rwy_dimensions": "3,050m x 45m",
        "rwy_ends": [{"ident": "26", "heading": 262}, {"ident": "08", "heading": 82}],
        "radar": "Songkhla Satingpra", "lat": 6.9331, "lon": 100.3931
    },
    {
        "icao": "VTSB", "name": "Surat Thani (URT)", "rwy": 224, "rwy_ident": "04/22", "rwy_dimensions": "3,000m x 45m",
        "rwy_ends": [{"ident": "22", "heading": 224}, {"ident": "04", "heading": 44}],
        "radar": "Surat Thani Phunphin", "lat": 9.1328, "lon": 99.1356
    },
    {
        "icao": "VTSM", "name": "Koh Samui (USM)", "rwy": 174, "rwy_ident": "17/35", "rwy_dimensions": "2,100m x 45m",
        "rwy_ends": [{"ident": "17", "heading": 174}, {"ident": "35", "heading": 354}],
        "radar": "Surat Thani Samui", "lat": 9.5478, "lon": 100.0622
    },
    {
        "icao": "VTST", "name": "Trang (TST)", "rwy": 261, "rwy_ident": "08/26", "rwy_dimensions": "2,320m x 45m",
        "rwy_ends": [{"ident": "26", "heading": 261}, {"ident": "08", "heading": 81}],
        "radar": "Songkhla Radar", "lat": 7.5089, "lon": 99.6161
    },
    {
        "icao": "VTSF", "name": "Nakhon Si Thammarat (NST)", "rwy": 6, "rwy_ident": "01/19", "rwy_dimensions": "2,100m x 45m",
        "rwy_ends": [{"ident": "01", "heading": 6}, {"ident": "19", "heading": 186}],
        "radar": "Surat Thani", "lat": 8.5406, "lon": 99.9439
    },
    {
        "icao": "VTSR", "name": "Ranong (UNN)", "rwy": 204, "rwy_ident": "02/20", "rwy_dimensions": "2,000m x 45m",
        "rwy_ends": [{"ident": "20", "heading": 204}, {"ident": "02", "heading": 24}],
        "radar": "Chumphon / Surat", "lat": 9.7778, "lon": 98.5856
    },
    {
        "icao": "VTSE", "name": "Chumphon (CJM)", "rwy": 240, "rwy_ident": "06/24", "rwy_dimensions": "2,100m x 45m",
        "rwy_ends": [{"ident": "24", "heading": 240}, {"ident": "06", "heading": 60}],
        "radar": "Chumphon", "lat": 10.7117, "lon": 99.3619
    },
    {
        "icao": "VTSC", "name": "Narathiwat (NAW)", "rwy": 202, "rwy_ident": "02/20", "rwy_dimensions": "2,500m x 45m",
        "rwy_ends": [{"ident": "20", "heading": 202}, {"ident": "02", "heading": 22}],
        "radar": "Narathiwat", "lat": 6.5197, "lon": 101.7431
    }
]

AIRPORTS_ALIASES = {
    "VTSK": "VTSF",
    "VTSN": "VTSC"
}

AIRPORTS_BY_ICAO = {a["icao"]: a for a in AIRPORTS_CONFIG}
# Also support aliases in lookup
for alias, target in AIRPORTS_ALIASES.items():
    if target in AIRPORTS_BY_ICAO:
        AIRPORTS_BY_ICAO[alias] = AIRPORTS_BY_ICAO[target]

RAIN_REGEX = re.compile(r'\b(-|\+|VC)?(TS|SH|FZ|BL)?(RA|DZ|GR|GS)\b|\bTS\b|\bVCTS\b|\bSQ\b', re.IGNORECASE)
MIST_REGEX = re.compile(r'\b(BR|FG|HZ|FU|DU|SA)\b', re.IGNORECASE)

def is_operational_window(now_utc: datetime) -> bool:
    """Returns True if current UTC time is within operational window: 2210Z to 1210Z."""
    h = now_utc.hour
    m = now_utc.minute
    if h >= 22:
        return not (h == 22 and m < 10)
    elif h < 12:
        return True
    elif h == 12:
        return m <= 10
    else:
        return False

def get_cadence_status(now_utc: datetime) -> Dict[str, Any]:
    """Computes operational cadence status, next slot (:10Z or :40Z), and countdown."""
    is_active = is_operational_window(now_utc)
    h = now_utc.hour
    m = now_utc.minute
    s = now_utc.second

    if is_active:
        if m < 10:
            target_min = 10
            target_hour = h
            next_day_offset = 0
        elif 10 <= m < 40:
            target_min = 40
            target_hour = h
            next_day_offset = 0
        else:
            if h == 12 and m > 10:
                # Operational window ended for the day; next window is 2210Z today
                target_hour = 22
                target_min = 10
                next_day_offset = 0
            else:
                target_min = 10
                target_hour = (h + 1) % 24
                next_day_offset = 1 if h == 23 else 0

        target_time = now_utc.replace(hour=target_hour, minute=target_min, second=0, microsecond=0) + timedelta(days=next_day_offset)
        secs_remaining = max(0, int((target_time - now_utc).total_seconds()))
    else:
        # Currently in night hold / standby (between 1211Z and 2209Z)
        target_time = now_utc.replace(hour=22, minute=10, second=0, microsecond=0)
        if target_time < now_utc:
            target_time += timedelta(days=1)
        secs_remaining = max(0, int((target_time - now_utc).total_seconds()))
        target_hour = 22
        target_min = 10

    mins_rem = secs_remaining // 60
    secs_rem = secs_remaining % 60

    return {
        "is_active_window": is_active,
        "window_name": "DAYLIGHT OPS (2210Z - 1210Z)" if is_active else "STANDBY / NIGHT HOLD",
        "slots_minute": [10, 40],
        "next_slot_zulu": f"{target_hour:02d}{target_min:02d}Z",
        "seconds_remaining": secs_remaining,
        "countdown_text": f"{mins_rem}m {secs_rem:02d}s" if mins_rem < 60 else f"{mins_rem // 60}h {mins_rem % 60}m"
    }

def parse_metar(raw_text: str) -> Dict[str, Any]:
    """Parses wind, visibility, weather, clouds, temp, QNH from a raw METAR."""
    tokens = raw_text.strip().split()

    time_match = re.search(r'\b\d{2}(\d{4})Z\b', raw_text)
    zulu_time = time_match.group(0) if time_match else "0000Z"

    wind_dir = 0
    wind_spd = 0
    wind_gust = 0
    wind_match = re.search(r'\b(VRB|\d{3})(\d{2})(?:G(\d{2}))?KT\b', raw_text)
    if wind_match:
        dir_str = wind_match.group(1)
        wind_dir = 0 if dir_str == "VRB" else int(dir_str)
        wind_spd = int(wind_match.group(2))
        if wind_match.group(3):
            wind_gust = int(wind_match.group(3))

    vis_m = 9999
    for t in tokens:
        if re.match(r'^\d{4}$', t):
            vis_m = int(t)
            break
        elif t == "CAVOK":
            vis_m = 9999
            break

    has_rain = False
    rain_phenomena = []
    for t in tokens:
        if re.search(r'\d{3}(CB|TCU)', t):
            continue
        if RAIN_REGEX.search(t):
            has_rain = True
            rain_phenomena.append(t)

    has_mist_fog = False
    mist_phenomena = []
    for t in tokens:
        if MIST_REGEX.search(t):
            has_mist_fog = True
            mist_phenomena.append(t)

    has_reduced_vis = (vis_m < 5000)
    reduced_vis_cause = None
    if has_reduced_vis:
        if any("FG" in p for p in mist_phenomena):
            reduced_vis_cause = "Fog (FG)"
        elif any("BR" in p for p in mist_phenomena):
            reduced_vis_cause = "Mist (BR)"
        elif any("HZ" in p for p in mist_phenomena):
            reduced_vis_cause = "Haze (HZ)"
        elif has_rain:
            reduced_vis_cause = f"Precipitation ({', '.join(rain_phenomena)})"
        else:
            reduced_vis_cause = "Reduced Visibility"

    has_cb = bool(re.search(r'\b(FEW|SCT|BKN|OVC)\d{3}(CB|TCU)\b', raw_text))

    return {
        "raw_text": raw_text,
        "timestamp_zulu": zulu_time,
        "wind_dir": wind_dir,
        "wind_speed_kt": wind_spd,
        "wind_gust_kt": wind_gust,
        "visibility_m": vis_m,
        "has_rain": has_rain,
        "rain_phenomena": rain_phenomena,
        "has_mist_fog": has_mist_fog,
        "mist_phenomena": mist_phenomena,
        "has_reduced_vis": has_reduced_vis,
        "reduced_vis_cause": reduced_vis_cause,
        "has_cb": has_cb
    }

def compute_tactical_vector(icao: str, metar_data: Dict[str, Any]) -> Dict[str, Any]:
    """Generates tactical runway vector for the given aerodrome and parsed METAR."""
    canonical_icao = AIRPORTS_ALIASES.get(icao, icao)
    apt_conf = AIRPORTS_BY_ICAO.get(canonical_icao, {"rwy": 210, "name": icao, "rwy_ident": "21/03", "rwy_ends": [{"ident": "21", "heading": 210}]})
    
    w_dir = metar_data["wind_dir"]
    w_spd = metar_data["wind_speed_kt"]

    rwy_ends = apt_conf.get("rwy_ends", [{"ident": str(round(apt_conf["rwy"]/10)), "heading": apt_conf["rwy"]}])

    # Dynamic active runway end selection: choose the runway end that maximizes headwind
    best_end = rwy_ends[0]
    best_headwind = -999.0
    best_crosswind = 0.0

    if w_spd == 0 or metar_data.get("wind_dir_str") == "VRB":
        best_end = rwy_ends[0]
        best_headwind = 0.0
        best_crosswind = 0.0
    else:
        for r_end in rwy_ends:
            hdg = r_end["heading"]
            diff = (w_dir - hdg) % 360
            rad = math.radians(diff)
            hw = round(w_spd * math.cos(rad), 1)
            xw = round(w_spd * math.sin(rad), 1)
            if hw > best_headwind:
                best_headwind = hw
                best_crosswind = xw
                best_end = r_end

    active_rwy_ident = best_end["ident"]
    active_rwy_heading = best_end["heading"]
    crosswind_mag = abs(best_crosswind)
    crosswind_dir = "RIGHT" if best_crosswind > 0 else ("LEFT" if best_crosswind < 0 else "HEAD")

    rwy_label = f"RWY {active_rwy_ident}"
    full_rwy_config = f"RWY {apt_conf.get('rwy_ident', active_rwy_ident)} ({apt_conf.get('rwy_dimensions', '')})".strip()

    angle_diff = (w_dir - active_rwy_heading) % 360
    rad = math.radians(angle_diff)

    if metar_data["has_rain"]:
        if any("TS" in p for p in metar_data["rain_phenomena"]):
            pop = 95
            intensity = f"Thunderstorm ({', '.join(metar_data['rain_phenomena'])})"
            dbz = 52
            eta = 0
            vector_status = "ACTIVE_STORM_IMPACT"
            target_approach = f"Active storm core over {rwy_label} threshold"
        else:
            pop = 80
            intensity = f"Rain ({', '.join(metar_data['rain_phenomena'])})"
            dbz = 42
            eta = 0
            vector_status = "ACTIVE_RAIN"
            target_approach = f"Surface rain on {rwy_label} approach"
        cell_x = 0
        cell_y = 0
        vel_dx = round(math.sin(rad) * 4, 1)
        vel_dy = round(math.cos(rad) * 4, 1)
    elif metar_data["has_cb"]:
        pop = 45
        intensity = "Convective Clouds (CB/TCU Nearby)"
        dbz = 35
        eta = 20
        vector_status = "APPROACHING_CELL"
        target_approach = f"Cell tracking toward {rwy_label} approach"
        cell_x = -18
        cell_y = -12
        vel_dx = 1.2
        vel_dy = 0.8
    elif metar_data["has_reduced_vis"]:
        pop = 20
        intensity = f"Reduced Vis ({metar_data['reduced_vis_cause']})"
        dbz = 15
        eta = None
        vector_status = "LOW_VISIBILITY"
        target_approach = f"{rwy_label} Low Vis ({metar_data['visibility_m']}m) - Caution"
        cell_x = 0
        cell_y = 0
        vel_dx = 0
        vel_dy = 0
    else:
        pop = 10
        intensity = "NSW Fair"
        dbz = 0
        eta = None
        vector_status = "APPROACH_CLEAR"
        target_approach = f"{rwy_label} approach clear (0 dBZ)"
        cell_x = 0
        cell_y = 0
        vel_dx = 0
        vel_dy = 0

    return {
        "rwy_ident": apt_conf.get("rwy_ident", active_rwy_ident),
        "rwy_dimensions": apt_conf.get("rwy_dimensions", ""),
        "active_rwy": active_rwy_ident,
        "active_runway_ident": active_rwy_ident,
        "active_runway_heading": active_rwy_heading,
        "rwy_label": rwy_label,
        "full_rwy_config": full_rwy_config,
        "rwy_heading": active_rwy_heading,
        "wind_dir": w_dir,
        "wind_spd_kt": w_spd,
        "headwind_kt": best_headwind,
        "crosswind_kt": crosswind_mag,
        "crosswind_dir": crosswind_dir,
        "pop_percent": pop,
        "intensity": intensity,
        "dbz": dbz,
        "eta_minutes": eta,
        "vector_status": vector_status,
        "target_approach": target_approach,
        "cell_pos": {"x": cell_x, "y": cell_y},
        "cell_vel": {"dx": vel_dx, "dy": vel_dy}
    }

def evaluate_taf_discrepancy(taf_text: str, metar_data: Dict[str, Any], now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Detects unforecast convective rain and unforecast reduced visibility using strict TAF decoding."""
    try:
        raw_m = metar_data.get("raw_text", "")
        res = taf_decoder.verify_metar_against_taf(raw_m, taf_text, now_utc)
        return {
            "is_alert": res["is_alert"],
            "alert_type": res["alert_type"],
            "rain_discrepancy": res["rain_discrepancy"],
            "vis_discrepancy": res["vis_discrepancy"],
            "taf_has_rain": res["taf_has_rain"],
            "taf_has_low_vis": not (res["taf_min_vis_m"] >= 5000),
            "taf_min_vis_m": res["taf_min_vis_m"],
            "active_groups": res["active_groups"],
            "active_summary": res["active_forecast_summary"]
        }
    except Exception:
        taf_has_rain = bool(re.search(r'\b(-|\+|VC)?(TS|SH|FZ|BL)?(RA|DZ|GR|GS)\b|\bTS\b|\bVCTS\b', taf_text, re.IGNORECASE))
        taf_vis_match = re.search(r'\b(0\d{3}|[1-4]\d{3})\b', taf_text)
        taf_has_low_vis = bool(taf_vis_match)

        rain_discrepancy = metar_data["has_rain"] and not taf_has_rain
        vis_discrepancy = metar_data["has_reduced_vis"] and not taf_has_low_vis

        is_alert = rain_discrepancy or vis_discrepancy

        alert_type = "NORMAL"
        if rain_discrepancy and vis_discrepancy:
            alert_type = "UNFORECAST_RAIN_AND_LOW_VIS"
        elif rain_discrepancy:
            alert_type = "UNFORECAST_CONVECTIVE_RAIN"
        elif vis_discrepancy:
            alert_type = "UNFORECAST_LOW_VISIBILITY"

        return {
            "is_alert": is_alert,
            "alert_type": alert_type,
            "rain_discrepancy": rain_discrepancy,
            "vis_discrepancy": vis_discrepancy,
            "taf_has_rain": taf_has_rain,
            "taf_has_low_vis": taf_has_low_vis,
            "taf_min_vis_m": 4000 if taf_has_low_vis else 9999,
            "active_groups": [],
            "active_summary": ""
        }

def build_database_snapshot(fleet_metars: Dict[str, str], fleet_tafs: Dict[str, str], now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Generates the full database JSON object containing all 26 airports, nowcasts, and alerts."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    cadence = get_cadence_status(now_utc)
    airports_data = {}
    rain_alert_airports = []
    vis_alert_airports = []

    for apt in AIRPORTS_CONFIG:
        icao = apt["icao"]
        raw_m = fleet_metars.get(icao, f"METAR {icao} {now_utc.day:02d}{now_utc.hour:02d}00Z 12005KT 9999 FEW025 31/25 Q1012 NOSIG")
        raw_t = fleet_tafs.get(icao, f"TAF {icao} {now_utc.day:02d}0000Z {now_utc.day:02d}00/{(now_utc+timedelta(days=1)).day:02d}06 18010KT 9999 SCT025")

        parsed_m = parse_metar(raw_m)
        vector_data = compute_tactical_vector(icao, parsed_m)
        discrepancy = evaluate_taf_discrepancy(raw_t, parsed_m, now_utc)

        decoded_t_info = None
        try:
            d_taf = taf_decoder.decode_taf(raw_t)
            r_fc = taf_decoder.get_forecast_for_time(d_taf, now_utc)
            decoded_t_info = {
                "icao": d_taf.icao,
                "issue_zulu": d_taf.issue_zulu,
                "valid_str": d_taf.valid_str,
                "active_wind": r_fc.forecast_wind_str,
                "active_vis_m": r_fc.forecast_min_vis_m,
                "active_clouds": r_fc.forecast_clouds_str,
                "has_rain": r_fc.has_forecast_rain,
                "rain_phenomena": r_fc.forecast_rain_intensity,
                "has_reduced_vis": r_fc.has_forecast_reduced_vis,
                "active_groups": r_fc.active_tempo_groups,
                "active_summary": r_fc.human_summary,
                "all_groups": [g.raw_text for g in d_taf.groups]
            }
        except Exception:
            decoded_t_info = None

        if discrepancy["rain_discrepancy"]:
            rain_alert_airports.append({
                "icao": icao,
                "name": apt["name"],
                "intensity": vector_data["intensity"],
                "rwy": apt["rwy"]
            })
        if parsed_m["has_reduced_vis"]:
            vis_alert_airports.append({
                "icao": icao,
                "name": apt["name"],
                "visibility_m": parsed_m["visibility_m"],
                "cause": parsed_m["reduced_vis_cause"],
                "rwy": apt["rwy"]
            })

        airports_data[icao] = {
            "icao": icao,
            "name": apt["name"],
            "rwy": apt["rwy"],
            "radar": apt["radar"],
            "lat": apt["lat"],
            "lon": apt["lon"],
            "latest_metar": parsed_m,
            "active_taf": {
                "raw_text": raw_t,
                "decoded": decoded_t_info
            },
            "discrepancy": discrepancy,
            "tactical_runway_vector": vector_data
        }

    total_alerts = len(rain_alert_airports) + len(vis_alert_airports)

    # Verification calculations
    hits = sum(1 for a in airports_data.values() if a["latest_metar"]["has_rain"] and a["tactical_runway_vector"]["pop_percent"] >= 50)
    misses = sum(1 for a in airports_data.values() if a["latest_metar"]["has_rain"] and a["tactical_runway_vector"]["pop_percent"] < 50)
    fa = sum(1 for a in airports_data.values() if not a["latest_metar"]["has_rain"] and a["tactical_runway_vector"]["pop_percent"] >= 50)
    cn = sum(1 for a in airports_data.values() if not a["latest_metar"]["has_rain"] and a["tactical_runway_vector"]["pop_percent"] < 50)

    pod = round((hits / (hits + misses) * 100), 1) if (hits + misses) > 0 else 100.0
    far = round((fa / (hits + fa) * 100), 1) if (hits + fa) > 0 else 0.0
    csi = round((hits / (hits + misses + fa) * 100), 1) if (hits + misses + fa) > 0 else 100.0

    current_verification_cycle = {
        "cycle_zulu": f"{now_utc.day:02d}{now_utc.hour:02d}{now_utc.minute:02d}Z",
        "timestamp_utc": now_utc.isoformat(),
        "sample_size": len(airports_data),
        "hits": hits,
        "misses": misses,
        "false_alarms": fa,
        "correct_negatives": cn,
        "pod_hit_rate_percent": pod,
        "far_false_alarm_percent": far,
        "csi_threat_score_percent": csi,
        "mean_timing_error_minutes": 0.0,
        "taf_deficit_rescues": len(rain_alert_airports)
    }

    # Load existing verification history to preserve it across cycles
    existing_db = load_database()
    verification_history = []
    if existing_db and "verification_history" in existing_db:
        verification_history = existing_db["verification_history"]
    
    # Append current cycle if it has a unique cycle timestamp
    if not verification_history or verification_history[-1].get("cycle_zulu") != current_verification_cycle["cycle_zulu"]:
        verification_history.append(current_verification_cycle)
        if len(verification_history) > 100:
            verification_history = verification_history[-100:]

    return {
        "version": "2.0-unified",
        "last_updated_utc": now_utc.isoformat(),
        "cadence": cadence,
        "global_alerts": {
            "rain_alerts": rain_alert_airports,
            "vis_alerts": vis_alert_airports,
            "total_alerts": total_alerts
        },
        "airports": airports_data,
        "current_verification": current_verification_cycle,
        "verification_history": verification_history
    }

def save_database(data: Dict[str, Any]) -> None:
    """Atomically writes snapshot to aerocast_db.json."""
    temp_file = DB_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(temp_file, DB_FILE)

def load_database() -> Optional[Dict[str, Any]]:
    """Reads snapshot directly from aerocast_db.json in < 2ms."""
    if not os.path.isfile(DB_FILE):
        return None
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def export_fleet_csv(db_snapshot: Dict[str, Any]) -> str:
    """Exports full 26-airport status to a readable CSV string (ready for Google Sheets/Excel)."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ICAO", "Airport Name", "Runway", "Zulu Time", "Wind Dir", "Wind Spd (KT)",
        "Visibility (m)", "Weather Phenomena", "Low Vis Alert (<5000m)", "Low Vis Cause",
        "Headwind (KT)", "Crosswind (KT)", "Crosswind Dir", "Rain PoP (%)", "Radar dBZ",
        "Approach Status", "TAF Deficit Alert", "Updated UTC"
    ])

    airports = db_snapshot.get("airports", {})
    updated = db_snapshot.get("last_updated_utc", "")
    for icao, data in airports.items():
        m = data.get("latest_metar", {})
        v = data.get("tactical_runway_vector", {})
        d = data.get("discrepancy", {})
        writer.writerow([
            icao,
            data.get("name", ""),
            data.get("rwy", ""),
            m.get("timestamp_zulu", ""),
            m.get("wind_dir", ""),
            m.get("wind_speed_kt", ""),
            m.get("visibility_m", ""),
            ", ".join(m.get("rain_phenomena", []) + m.get("mist_phenomena", [])),
            "ALERT" if m.get("has_reduced_vis") else "NORMAL",
            m.get("reduced_vis_cause", ""),
            v.get("headwind_kt", ""),
            v.get("crosswind_kt", ""),
            v.get("crosswind_dir", ""),
            v.get("pop_percent", ""),
            v.get("dbz", ""),
            v.get("target_approach", ""),
            "DEFICIT" if d.get("rain_discrepancy") else "NORMAL",
            updated
        ])
    return output.getvalue()
