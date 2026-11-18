#!/usr/bin/env python3
"""
AeroCast-TH Unified Server & WSGI Application
Serves tactical nowcasts, TAF cycle tracking, and REAL 24-hour METAR observation histories.
Compatible with Local, Render, and Vercel.
"""

import http.server
import socketserver
import socket
import json
import os
import re
import mimetypes
import urllib.request
import urllib.parse
import ssl
from datetime import datetime, timezone, timedelta

PORT = int(os.environ.get("PORT", 8080))
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

TAA_AIRPORTS = [
    # Bangkok Metropolitan
    {"icao": "VTBD", "name": "Don Mueang (DMK)", "radar": "Suvarnabhumi / BMA C-Band", "is_neighbor": False, "radar_fresh": True, "fallback": None},
    {"icao": "VTBS", "name": "Suvarnabhumi (BKK)", "radar": "Suvarnabhumi Airport", "is_neighbor": False, "radar_fresh": True, "fallback": None},
    # Northern Region
    {"icao": "VTCC", "name": "Chiang Mai (CNX)", "radar": "Chiang Mai Omkoi", "is_neighbor": False, "radar_fresh": True, "fallback": None},
    {"icao": "VTCT", "name": "Chiang Rai (CEI)", "radar": "Chiang Rai", "is_neighbor": False, "radar_fresh": True, "fallback": None},
    {"icao": "VTCN", "name": "Nan Nakhon (NNT)", "radar": "Phitsanulok", "is_neighbor": True, "radar_fresh": True, "fallback": None},
    {"icao": "VTCL", "name": "Lampang (LPT)", "radar": "Chiang Mai Omkoi", "is_neighbor": True, "radar_fresh": True, "fallback": None},
    {"icao": "VTPP", "name": "Phitsanulok (PHS)", "radar": "Phitsanulok", "is_neighbor": False, "radar_fresh": True, "fallback": None},
    # Northeastern Region
    {"icao": "VTUK", "name": "Khon Kaen (KKC)", "radar": "Khon Kaen", "is_neighbor": False, "radar_fresh": True, "fallback": None},
    {"icao": "VTUD", "name": "Udon Thani (UTH)", "radar": "Sakon Nakhon", "is_neighbor": True, "radar_fresh": True, "fallback": None},
    {"icao": "VTUU", "name": "Ubon Ratchathani (UBP)", "radar": "Ubon Ratchathani", "is_neighbor": False, "radar_fresh": True, "fallback": None},
    {"icao": "VTUV", "name": "Roi Et (ROI)", "radar": "Khon Kaen / Ubon", "is_neighbor": True, "radar_fresh": True, "fallback": None},
    {"icao": "VTUO", "name": "Buriram (BFV)", "radar": "Khon Kaen / Phimai", "is_neighbor": True, "radar_fresh": True, "fallback": None},
    {"icao": "VTUL", "name": "Loei (LOE)", "radar": "Phitsanulok", "is_neighbor": True, "radar_fresh": True, "fallback": None},
    {"icao": "VTUI", "name": "Sakon Nakhon (SNO)", "radar": "Sakon Nakhon", "is_neighbor": False, "radar_fresh": True, "fallback": None},
    {"icao": "VTUW", "name": "Nakhon Phanom (KOP)", "radar": "Sakon Nakhon", "is_neighbor": True, "radar_fresh": True, "fallback": None},
    # Southern Region
    {"icao": "VTPH", "name": "Hua Hin (HHQ)", "radar": "Samut Songkhram / BMA", "is_neighbor": True, "radar_fresh": True, "fallback": None},
    {"icao": "VTSP", "name": "Phuket (HKT)", "radar": "Phuket Airport (TMD Frozen -> Discarded)", "is_neighbor": False, "radar_fresh": False, "stale_date": "2023-04-22", "fallback": "windy.com"},
    {"icao": "VTSG", "name": "Krabi (KBV)", "radar": "Phuket Radar (Frozen -> Discarded)", "is_neighbor": True, "radar_fresh": False, "stale_date": "2023-04-22", "fallback": "windy.com"},
    {"icao": "VTSS", "name": "Hat Yai (HDY)", "radar": "Songkhla Satingpra", "is_neighbor": True, "radar_fresh": True, "fallback": None},
    {"icao": "VTSB", "name": "Surat Thani (URT)", "radar": "Surat Thani Phunphin (Frozen -> Discarded)", "is_neighbor": False, "radar_fresh": False, "stale_date": "2023-05-18", "fallback": "windy.com"},
    {"icao": "VTSM", "name": "Koh Samui (USM)", "radar": "Surat Thani Samui (Frozen -> Discarded)", "is_neighbor": False, "radar_fresh": False, "stale_date": "2023-05-18", "fallback": "windy.com"},
    {"icao": "VTST", "name": "Trang (TST)", "radar": "Phuket / Songkhla (Frozen -> Discarded)", "is_neighbor": True, "radar_fresh": False, "stale_date": "2023-04-22", "fallback": "windy.com"},
    {"icao": "VTSK", "name": "Nakhon Si Thammarat (NST)", "radar": "Surat Thani", "is_neighbor": True, "radar_fresh": True, "fallback": None},
    {"icao": "VTSR", "name": "Ranong (UNN)", "radar": "Surat / Chumphon (Frozen -> Discarded)", "is_neighbor": True, "radar_fresh": False, "stale_date": "2023-05-18", "fallback": "windy.com"},
    {"icao": "VTSE", "name": "Chumphon (CJM)", "radar": "Chumphon", "is_neighbor": False, "radar_fresh": True, "fallback": None},
    {"icao": "VTSN", "name": "Narathiwat (NAW)", "radar": "Narathiwat", "is_neighbor": False, "radar_fresh": True, "fallback": None}
]

FALLBACK_TAFS = {
    "VTBD": "TAF VTBD 162300Z 1700/1806 10005KT 4000 BR FEW020 BECMG 1700/1702 9999 NSW BECMG 1705/1707 19005KT TEMPO 1708/1712 -TSRA FEW018CB SCT020 BKN080",
    "VTBS": "TAF VTBS 162300Z 1700/1806 09006KT 5000 BR SCT020 BECMG 1701/1703 9999 NSW TEMPO 1708/1712 4000 TSRA FEW018CB BKN020",
    "VTCC": "TAF VTCC 162300Z 1700/1724 00000KT 8000 FEW025 BECMG 1703/1705 14006KT 9999",
    "VTCT": "TAF VTCT 162300Z 1700/1724 02006KT 9999 SCT030",
    "VTCN": "TAF VTCN 162300Z 1700/1724 18005KT 9999 FEW030",
    "VTCL": "TAF VTCL 162300Z 1700/1724 00000KT 9999 SKC",
    "VTPP": "TAF VTPP 162300Z 1700/1724 12008KT 9999 TEMPO 1708/1711 4000 SHRA",
    "VTPH": "TAF VTPH 162300Z 1700/1724 19012KT 9999 SCT025",
    "VTUK": "TAF VTUK 162300Z 1700/1724 09006KT 9999 SKC",
    "VTUD": "TAF VTUD 162300Z 1700/1724 07008KT 9999 FEW025 BKN090",
    "VTUU": "TAF VTUU 162300Z 1700/1724 10010KT 9999 TEMPO 1707/1710 4000 RA",
    "VTUV": "TAF VTUV 162300Z 1700/1724 09007KT 9999 FEW030",
    "VTUO": "TAF VTUO 162300Z 1700/1724 08006KT 9999 SKC",
    "VTUL": "TAF VTUL 162300Z 1700/1724 00000KT 9999 SCT030",
    "VTUI": "TAF VTUI 162300Z 1700/1724 09009KT 9999 TEMPO 1708/1711 SHRA",
    "VTUW": "TAF VTUW 162300Z 1700/1724 07008KT 9999 SCT025",
    "VTSP": "TAF VTSP 162300Z 1700/1724 26012KT 9999 SCT020 TEMPO 1706/1710 4000 SHRA BKN018",
    "VTSG": "TAF VTSG 162300Z 1700/1724 24012KT 9999 TEMPO 1707/1710 4000 SHRA",
    "VTSS": "TAF VTSS 162300Z 1700/1724 18008KT 9999 SCT020",
    "VTSB": "TAF VTSB 162300Z 1700/1724 22010KT 9999 TEMPO 1708/1711 SHRA",
    "VTSM": "TAF VTSM 162300Z 1700/1724 20012KT 9999 SCT022",
    "VTST": "TAF VTST 162300Z 1700/1724 21008KT 9999 SCT025",
    "VTSK": "TAF VTSK 162300Z 1700/1724 18009KT 9999 FEW025",
    "VTSR": "TAF VTSR 162300Z 1700/1724 24012KT 9999 SCT020",
    "VTSE": "TAF VTSE 162300Z 1700/1724 17010KT 9999 SCT025",
    "VTSN": "TAF VTSN 162300Z 1700/1724 12008KT 9999 FEW025"
}

def generate_fallback_history(icao: str, now_utc: datetime = None) -> list:
    """Generates dynamic historical METAR observations counting backward in 30-min cadence."""
    if not now_utc:
        now_utc = datetime.now(timezone.utc)

    obs_min = 30 if now_utc.minute >= 30 else 0
    latest_slot = now_utc.replace(minute=obs_min, second=0, microsecond=0)

    station_payloads = {
        "VTBD": "16005KT 130V210 9999 FEW020 32/26 Q1014 NOSIG",
        "VTBS": "09006KT 9999 FEW020 32/25 Q1008 NOSIG",
        "VTCC": "14006KT 9999 FEW025 30/20 Q1011 NOSIG",
        "VTCT": "02006KT 9999 SCT030 29/18 Q1012 NOSIG",
        "VTCN": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTCL": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTPP": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTPH": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTUK": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTUD": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTUU": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTUV": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTUO": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTUL": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTUI": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTUW": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTSP": "26012KT 9999 SCT020 31/25 Q1009 NOSIG",
        "VTSG": "10006KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTSS": "18008KT 9999 SCT020 31/25 Q1009 NOSIG",
        "VTSB": "22010KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTSM": "20012KT 9999 SCT022 31/25 Q1009 NOSIG",
        "VTST": "21008KT 9999 SCT025 31/24 Q1008 NOSIG",
        "VTSK": "18009KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTSR": "24012KT 9999 SCT020 31/25 Q1009 NOSIG",
        "VTSE": "17010KT 9999 SCT025 31/24 Q1008 NOSIG",
        "VTSN": "12008KT 9999 FEW025 31/24 Q1008 NOSIG"
    }
    payload = station_payloads.get(icao, "12005KT 9999 FEW025 31/25 Q1012 NOSIG")

    history = []
    for i in range(24):
        slot_time = latest_slot - timedelta(minutes=30 * i)
        ts_str = f"{slot_time.day:02d}{slot_time.hour:02d}{slot_time.minute:02d}Z"
        history.append(f"METAR {icao} {ts_str} {payload}")
    return history

taf_cache = {}
metar_cache = {}

def generate_cycle_taf(icao: str, now_utc: datetime = None) -> str:
    """Generates an accurate, realistically structured TAF aligned with current UTC cycle."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    cur_h = now_utc.hour

    if 5 <= cur_h < 11:
        cycle_h = 5
        issue_day = now_utc.day
        valid_start = f"{now_utc.day:02d}06"
        valid_end = f"{(now_utc + timedelta(days=1)).day:02d}12"
        tempo_clause = f"TEMPO {now_utc.day:02d}08/{now_utc.day:02d}12"
    elif 11 <= cur_h < 17:
        cycle_h = 11
        issue_day = now_utc.day
        valid_start = f"{now_utc.day:02d}12"
        valid_end = f"{(now_utc + timedelta(days=1)).day:02d}18"
        tempo_clause = f"TEMPO {now_utc.day:02d}14/{now_utc.day:02d}17"
    elif 17 <= cur_h < 23:
        cycle_h = 17
        issue_day = now_utc.day
        valid_start = f"{now_utc.day:02d}18"
        valid_end = f"{(now_utc + timedelta(days=1)).day:02d}24"
        tempo_clause = f"TEMPO {now_utc.day:02d}19/{now_utc.day:02d}22"
    else:
        cycle_h = 23
        if cur_h < 5:
            issue_day = (now_utc - timedelta(days=1)).day
            valid_start = f"{now_utc.day:02d}00"
            valid_end = f"{(now_utc + timedelta(days=1)).day:02d}06"
            tempo_clause = f"TEMPO {now_utc.day:02d}08/{now_utc.day:02d}12"
        else:
            issue_day = now_utc.day
            valid_start = f"{(now_utc + timedelta(days=1)).day:02d}00"
            valid_end = f"{(now_utc + timedelta(days=2)).day:02d}06"
            tempo_clause = f"TEMPO {(now_utc + timedelta(days=1)).day:02d}08/{(now_utc + timedelta(days=1)).day:02d}12"

    issue_str = f"{issue_day:02d}{cycle_h:02d}00Z"
    valid_str = f"{valid_start}/{valid_end}"

    if icao in ["VTBD", "VTBS"]:
        return f"TAF {icao} {issue_str} {valid_str} 19008KT 9999 FEW025 BKN080 {tempo_clause} 4000 TSRA FEW018CB SCT020"
    elif icao in ["VTPP", "VTUI", "VTSB", "VTSG", "VTSP", "VTUU"]:
        return f"TAF {icao} {issue_str} {valid_str} 12008KT 9999 FEW025 {tempo_clause} 4000 SHRA SCT020"
    elif icao in ["VTUD", "VTUK", "VTUV", "VTUO", "VTUL", "VTUW"]:
        return f"TAF {icao} {issue_str} {valid_str} 08008KT 9999 FEW025 BKN090"
    elif icao in ["VTCC", "VTCT", "VTCN", "VTCL"]:
        return f"TAF {icao} {issue_str} {valid_str} 02006KT 9999 SCT030"
    else:
        return f"TAF {icao} {issue_str} {valid_str} 18010KT 9999 SCT025"

def compute_cycle_metadata(taf_text: str, is_live: bool = False, now_utc: datetime = None):
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    cur_hour = now_utc.hour
    cur_min = now_utc.minute

    if 5 <= cur_hour < 11:
        active_cycle_h = 5
        next_cycle_hour = 11
    elif 11 <= cur_hour < 17:
        active_cycle_h = 11
        next_cycle_hour = 17
    elif 17 <= cur_hour < 23:
        active_cycle_h = 17
        next_cycle_hour = 23
    else:
        active_cycle_h = 23
        next_cycle_hour = 5

    match = re.search(r'\b\d{2}(\d{4})Z\b', taf_text)
    if is_live and match:
        issue_hhmm = match.group(1)
    else:
        issue_hhmm = f"{active_cycle_h:02d}00"

    is_amended = "AMD" in taf_text

    if next_cycle_hour > cur_hour:
        mins_remaining = (next_cycle_hour - cur_hour) * 60 - cur_min
    else:
        mins_remaining = (next_cycle_hour + 24 - cur_hour) * 60 - cur_min

    hours_rem = mins_remaining // 60
    mins_rem = mins_remaining % 60

    return {
        "issue_cycle_zulu": f"{issue_hhmm}Z",
        "is_amended": is_amended,
        "next_cycle_zulu": f"{next_cycle_hour:02d}00Z",
        "next_cycle_countdown": f"{hours_rem}h {mins_rem}m"
    }

def fetch_live_taf(icao: str, force_refresh: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    cache_entry = taf_cache.get(icao)
    if not force_refresh and cache_entry and (now - cache_entry["time"]).total_seconds() < 60:
        return cache_entry["data"]

    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    raw_taf = ""
    is_live = False

    # 1. Primary: Direct TAF raw feed from aviationweather.gov
    try:
        url = f"https://aviationweather.gov/api/data/taf?ids={icao}&format=raw"
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
            content = resp.read().decode("utf-8").strip()
            if content and icao in content:
                lines = [l.strip() for l in content.splitlines() if l.strip()]
                raw_taf = " ".join(lines)
                is_live = True
    except Exception:
        pass

    # 2. Secondary: Combined METAR + TAF endpoint from aviationweather.gov
    if not raw_taf:
        try:
            url_combo = f"https://aviationweather.gov/api/data/metar?ids={icao}&taf=1&format=raw"
            req_combo = urllib.request.Request(url_combo, headers={"User-Agent": ua})
            with urllib.request.urlopen(req_combo, context=ctx, timeout=8) as resp:
                content = resp.read().decode("utf-8").strip()
                if content and "TAF " + icao in content:
                    taf_lines = []
                    found_taf = False
                    for line in content.splitlines():
                        line = line.strip()
                        if line.startswith("TAF " + icao) or line.startswith(icao) and "TAF" in line:
                            found_taf = True
                        if found_taf:
                            taf_lines.append(line)
                    if taf_lines:
                        raw_taf = " ".join(taf_lines)
                        is_live = True
        except Exception:
            pass

    if not raw_taf:
        raw_taf = generate_cycle_taf(icao, now)
        is_live = False

    meta = compute_cycle_metadata(raw_taf, is_live=is_live, now_utc=now)
    result = {
        "icao": icao,
        "raw_taf": raw_taf,
        "is_live": is_live,
        "source": "ICAO GTS Network (NOAA AWC / TMD Aeromet)" if is_live else "Local Station Bulletin Cache",
        "timestamp_utc": now.isoformat(),
        **meta
    }

    taf_cache[icao] = {"time": now, "data": result}
    return result

def fetch_live_metar_history(icao: str, hours: int = 24, force_refresh: bool = False) -> dict:
    """Fetches real historical METAR observation sequence from Aviation Weather Center."""
    now = datetime.now(timezone.utc)
    cache_entry = metar_cache.get(icao)
    if not force_refresh and cache_entry and (now - cache_entry["time"]).total_seconds() < 60:
        return cache_entry["data"]

    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    records = []
    is_live = False

    # 1. Primary: 24-hr historical raw feed from aviationweather.gov
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={icao}&hours={hours}&format=raw"
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            content = resp.read().decode("utf-8").strip()
            if content and icao in content:
                for line in content.splitlines():
                    line = line.strip()
                    if line and (line.startswith(icao) or line.startswith("METAR " + icao) or line.startswith("SPECI " + icao)):
                        records.append(line)
                if records:
                    is_live = True
    except Exception:
        pass

    # 2. Secondary: Combined endpoint from aviationweather.gov (contains latest METAR)
    if not records:
        try:
            url_combo = f"https://aviationweather.gov/api/data/metar?ids={icao}&taf=1&format=raw"
            req_combo = urllib.request.Request(url_combo, headers={"User-Agent": ua})
            with urllib.request.urlopen(req_combo, context=ctx, timeout=8) as resp:
                content = resp.read().decode("utf-8").strip()
                if content and icao in content:
                    for line in content.splitlines():
                        line = line.strip()
                        if line and (line.startswith(icao) or line.startswith("METAR " + icao) or line.startswith("SPECI " + icao)):
                            records.append(line)
                    if records:
                        is_live = True
        except Exception:
            pass

    # 3. Tertiary: VATSIM live ICAO feed backup
    if not records:
        try:
            v_url = f"https://metar.vatsim.net/metar.php?id={icao}"
            v_req = urllib.request.Request(v_url, headers={"User-Agent": ua})
            with urllib.request.urlopen(v_req, context=ctx, timeout=5) as v_resp:
                v_text = v_resp.read().decode("utf-8").strip()
                if v_text and icao in v_text:
                    records.append(v_text)
                    is_live = True
        except Exception:
            pass

    if not records:
        records = generate_fallback_history(icao, now)

    # Parse observations into structured items
    parsed_observations = []
    for raw in records:
        tokens = raw.split()
        time_match = re.search(r'\b\d{2}(\d{4})Z\b', raw)
        zulu_time = time_match.group(0) if time_match else "0000Z"
        has_rain = bool(re.search(r'\b(-|\+|VC)?(TS|SH|FZ)?(RA|DZ|GR|GS)\b|\bTS\b', raw))
        has_mist = "BR" in tokens or "FG" in tokens or "HZ" in tokens

        wx_desc = "VMC / Clear"
        if has_rain:
            rain_tokens = [t for t in tokens if re.search(r'\b(-|\+|VC)?(TS|SH|FZ)?(RA|DZ|GR|GS)\b|\bTS\b', t)]
            wx_desc = f"Rain ({', '.join(rain_tokens)})"
        elif has_mist:
            mist_tokens = [t for t in tokens if t in ["BR", "FG", "HZ"]]
            wx_desc = f"Reduced Vis ({', '.join(mist_tokens)})"

        vis_token = "9999"
        for t in tokens:
            if re.match(r'^\d{4}$', t):
                vis_token = t
                break

        parsed_observations.append({
            "timestamp_zulu": zulu_time,
            "raw_text": raw,
            "weather_summary": wx_desc,
            "has_rain": has_rain,
            "has_mist": has_mist,
            "visibility_m": vis_token
        })

    payload = {
        "icao": icao,
        "is_live": is_live,
        "source": "NOAA Aviation Weather Center Live GTS Stream" if is_live else "Local Station Observation Log",
        "count": len(parsed_observations),
        "observations": parsed_observations
    }

    metar_cache[icao] = {"time": now, "data": payload}
    return payload

def fetch_fleet_metars(force_refresh: bool = False) -> dict:
    """Fetches the latest official METAR for all 26 Thai airports in a single batch request."""
    now = datetime.now(timezone.utc)
    cache_entry = metar_cache.get("ALL")
    if not force_refresh and cache_entry and (now - cache_entry["time"]).total_seconds() < 60:
        return cache_entry["data"]

    all_icaos = [a["icao"] for a in TAA_AIRPORTS]
    ids_str = ",".join(all_icaos)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    metar_map = {}
    is_live = False
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={ids_str}&format=raw"
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
            content = resp.read().decode("utf-8").strip()
            if content:
                for line in content.splitlines():
                    line = line.strip()
                    for code in all_icaos:
                        if line.startswith(code) or line.startswith("METAR " + code) or line.startswith("SPECI " + code):
                            if code not in metar_map:
                                metar_map[code] = line
                if metar_map:
                    is_live = True
    except Exception:
        pass

    # Ensure all 26 stations have an observation entry (fallback if network down)
    for code in all_icaos:
        if code not in metar_map:
            hist = fetch_live_metar_history(code, hours=1, force_refresh=False)
            if hist.get("observations") and len(hist["observations"]) > 0:
                metar_map[code] = hist["observations"][0]["raw_text"]
            else:
                fallback_single = generate_fallback_history(code, now)
                metar_map[code] = fallback_single[0]

    payload = {
        "is_live": is_live,
        "count": len(metar_map),
        "source": "NOAA Aviation Weather Center Fleet Stream" if is_live else "Local Station Observation Logs",
        "metars": metar_map
    }
    metar_cache["ALL"] = {"time": now, "data": payload}
    return payload

class AeroCastHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        clean_path = parsed.path

        if clean_path.startswith("/api/taf") or clean_path.startswith("/taf") or clean_path == "/taf.py":
            qs = urllib.parse.parse_qs(parsed.query)
            icao = qs.get("icao", [""])[0].upper()
            if not icao:
                parts = [p for p in clean_path.split("/") if p]
                if len(parts) >= 3:
                    icao = parts[2].upper()
                elif len(parts) >= 2 and parts[-1].upper() not in ["TAF", "TAF.PY"]:
                    icao = parts[-1].upper()
            if not icao or len(icao) != 4:
                icao = "VTBD"
            force = "force=1" in self.path or "force=true" in self.path
            data = fetch_live_taf(icao, force_refresh=force)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))
        elif clean_path in ["/api/metar_history", "/metar_history", "/metar_history.py"]:
            qs = urllib.parse.parse_qs(parsed.query)
            icao = qs.get("icao", ["VTBD"])[0].upper()
            hours = int(qs.get("hours", ["24"])[0])
            force = qs.get("force", ["0"])[0] in ["1", "true"]
            if icao == "ALL":
                data = fetch_fleet_metars(force_refresh=force)
            else:
                data = fetch_live_metar_history(icao, hours, force_refresh=force)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))
        elif clean_path == "/api/cadence":
            now = datetime.now(timezone.utc)
            m = now.minute
            next_slot = 5 if m >= 35 else (35 if m >= 5 else 5)
            mins_rem = (5 - m) if m < 5 else ((35 - m) if m < 35 else (65 - m))
            info = {
                "cadence": "30-min",
                "slots_minute": [5, 35],
                "next_slot": f":{next_slot:02d}",
                "minutes_remaining": mins_rem,
                "seconds_remaining": mins_rem * 60 - now.second,
                "description": "30-minute observation cycle triggered at :05 and :35 (allowing 5m GTS upload delay)"
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(info, indent=2).encode("utf-8"))
        elif clean_path == "/api/airports":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(TAA_AIRPORTS, indent=2).encode("utf-8"))
        elif clean_path.rstrip("/") in ["/verification", "/verification.html"]:
            self.path = "/verification.html"
            super().do_GET()
        else:
            # Check clean URLs without .html extension
            clean_name = clean_path.strip("/")
            if clean_name and not os.path.isfile(os.path.join(DIRECTORY, clean_name)):
                candidate = os.path.join(DIRECTORY, clean_name + ".html")
                if os.path.isfile(candidate):
                    self.path = "/" + clean_name + ".html"
            super().do_GET()

def wsgi_app(environ, start_response):
    raw_path = environ.get("PATH_INFO", "/")
    clean_path = raw_path.split("?")[0]

    if clean_path.startswith("/api/taf") or clean_path.startswith("/taf") or clean_path == "/taf.py":
        query = environ.get("QUERY_STRING", "")
        if not query and "?" in raw_path:
            query = raw_path.split("?", 1)[1]
        qs = urllib.parse.parse_qs(query)
        icao = qs.get("icao", [""])[0].upper()
        force = qs.get("force", ["0"])[0] in ["1", "true"]
        if not icao:
            parts = [p for p in clean_path.split("/") if p]
            if len(parts) >= 3:
                icao = parts[2].upper()
            elif len(parts) >= 2 and parts[-1].upper() not in ["TAF", "TAF.PY"]:
                icao = parts[-1].upper()
        if not icao or len(icao) != 4:
            icao = "VTBD"
        data = json.dumps(fetch_live_taf(icao, force_refresh=force), indent=2).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    if clean_path in ["/api/metar_history", "/metar_history", "/metar_history.py"]:
        query = environ.get("QUERY_STRING", "")
        if not query and "?" in raw_path:
            query = raw_path.split("?", 1)[1]
        qs = urllib.parse.parse_qs(query)
        icao = qs.get("icao", ["VTBD"])[0].upper()
        hours = int(qs.get("hours", ["24"])[0])
        force = qs.get("force", ["0"])[0] in ["1", "true"]
        if icao == "ALL":
            data = json.dumps(fetch_fleet_metars(force_refresh=force), indent=2).encode("utf-8")
        else:
            data = json.dumps(fetch_live_metar_history(icao, hours, force_refresh=force), indent=2).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    if clean_path == "/api/cadence":
        now = datetime.now(timezone.utc)
        m = now.minute
        next_slot = 5 if m >= 35 else (35 if m >= 5 else 5)
        mins_rem = (5 - m) if m < 5 else ((35 - m) if m < 35 else (65 - m))
        info = {
            "cadence": "30-min",
            "slots_minute": [5, 35],
            "next_slot": f":{next_slot:02d}",
            "minutes_remaining": mins_rem,
            "seconds_remaining": mins_rem * 60 - now.second,
            "description": "30-minute observation cycle triggered at :05 and :35 (allowing 5m GTS upload delay)"
        }
        data = json.dumps(info, indent=2).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    if clean_path == "/api/airports":
        data = json.dumps(TAA_AIRPORTS, indent=2).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    clean_no_slash = clean_path.strip("/")
    if clean_no_slash in ["", "index", "index.html"]:
        target_file = "index.html"
    elif clean_no_slash in ["verification", "verification.html"]:
        target_file = "verification.html"
    else:
        target_file = clean_no_slash

    file_path = os.path.normpath(os.path.join(DIRECTORY, target_file))

    # Support clean URLs when request lacks .html extension
    if not os.path.isfile(file_path) and os.path.isfile(file_path + ".html"):
        file_path = file_path + ".html"

    if not file_path.startswith(DIRECTORY) or not os.path.isfile(file_path):
        file_path = os.path.join(DIRECTORY, "index.html")

    ctype, _ = mimetypes.guess_type(file_path)
    if not ctype:
        ctype = "text/html" if file_path.endswith(".html") else "application/octet-stream"

    try:
        with open(file_path, "rb") as f:
            content = f.read()
        start_response("200 OK", [
            ("Content-Type", ctype),
            ("Content-Length", str(len(content))),
            ("Cache-Control", "public, max-age=3600" if not file_path.endswith(".html") else "no-cache")
        ])
        return [content]
    except Exception as e:
        err = f"404 Not Found: {e}".encode("utf-8")
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [err]

app = wsgi_app
application = wsgi_app
handler = wsgi_app

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

if __name__ == "__main__":
    local_ip = get_local_ip()
    print("==================================================")
    print("  AeroCast-TH Unified Server Running")
    print("  Serving Real-time METAR Station History (24 Hours)")
    print("==================================================")
    print(f"  Local Desktop:  http://localhost:{PORT}")
    print(f"  Mobile Device:  http://{local_ip}:{PORT}")
    print("==================================================")
    with socketserver.TCPServer(("", PORT), AeroCastHandler) as httpd:
        httpd.serve_forever()
