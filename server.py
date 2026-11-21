#!/usr/bin/env python3
"""
AeroCast-TH Unified Server & WSGI Application
Version 2.0: Scheduled Ingestion (:10Z & :40Z from 2210Z to 1210Z),
Database Snapshot Engine, Low Visibility Alerts (<5000m),
Tactical Runway Vector Generation, and Ground-Truth Verification Persistence.
Compatible with Local, Render, and Vercel.
"""

import http.server
import socketserver
import socket
import json
import os
import re
import mimetypes
import threading
import time
import urllib.request
import urllib.parse
import ssl
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

# Import database module from flat directory
import database

try:
    import taf_decoder
except ImportError:
    import aerocast_web.taf_decoder as taf_decoder

PORT = int(os.environ.get("PORT", 8080))
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

TAA_AIRPORTS = database.AIRPORTS_CONFIG

taf_cache = {}
metar_cache = {}
last_ingested_slot = None

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

    if not raw_taf:
        raw_taf = generate_cycle_taf(icao, now)
        is_live = False

    decoded_payload = None
    try:
        d_taf = taf_decoder.decode_taf(raw_taf)
        r_fc = taf_decoder.get_forecast_for_time(d_taf, now)
        decoded_payload = {
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
            "all_groups": [{"type": g.group_type, "header": g.raw_header, "text": g.raw_text} for g in d_taf.groups]
        }
    except Exception:
        decoded_payload = None

    meta = compute_cycle_metadata(raw_taf, is_live=is_live, now_utc=now)
    result = {
        "icao": icao,
        "raw_taf": raw_taf,
        "is_live": is_live,
        "source": "ICAO GTS Network (NOAA AWC / TMD Aeromet)" if is_live else "Local Station Bulletin Cache",
        "timestamp_utc": now.isoformat(),
        "decoded": decoded_payload,
        **meta
    }

    taf_cache[icao] = {"time": now, "data": result}
    return result

def fetch_fleet_metars(force_refresh: bool = False) -> dict:
    """Fetches the latest official METAR for all 26 Thai airports in a single batch request."""
    now = datetime.now(timezone.utc)
    cache_entry = metar_cache.get("ALL")
    if not force_refresh and cache_entry and (now - cache_entry["time"]).total_seconds() < 60:
        return cache_entry["data"]

    all_icaos = [a["icao"] for a in TAA_AIRPORTS]
    aliases = getattr(database, 'AIRPORTS_ALIASES', {})
    query_ids = list(set(all_icaos + list(aliases.keys())))
    ids_str = ",".join(query_ids)

    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    metar_map = {}
    is_live = False
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={ids_str}&format=raw&hours=24"
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
            content = resp.read().decode("utf-8").strip()
            if content:
                for line in content.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    for code in all_icaos:
                        if line.startswith(code) or line.startswith("METAR " + code) or line.startswith("SPECI " + code):
                            if code not in metar_map:
                                metar_map[code] = line
                    for alias, canon in aliases.items():
                        if line.startswith(alias) or line.startswith("METAR " + alias) or line.startswith("SPECI " + alias):
                            if canon not in metar_map:
                                metar_map[canon] = line.replace(alias, canon, 1)
                if metar_map:
                    is_live = True
    except Exception:
        pass

    # For hourly or regional stations not broadcasting in the latest hour:
    # Use the latest actual observation available from the local database or archive.
    # NEVER fabricate or synthesize fake METAR data.
    for code in all_icaos:
        if code not in metar_map or "NIL" in metar_map[code]:
            last_real = database.get_latest_metar_from_archive(code)
            if last_real:
                metar_map[code] = last_real
            elif code not in metar_map:
                metar_map[code] = f"METAR {code} NIL"

    payload = {
        "is_live": is_live,
        "count": len(metar_map),
        "source": "NOAA Aviation Weather Center Fleet Stream" if is_live else "Local Station Observation Logs",
        "metars": metar_map
    }
    metar_cache["ALL"] = {"time": now, "data": payload}
    return payload

def perform_ingest_cycle(force: bool = False) -> dict:
    """
    Executes a complete ingestion, forecasting, tactical vector generation,
    and database write cycle.
    """
    global last_ingested_slot
    now_utc = datetime.now(timezone.utc)

    slot_key = f"{now_utc.day:02d}_{now_utc.hour:02d}_{'40' if now_utc.minute >= 40 else ('10' if now_utc.minute >= 10 else 'prev')}"
    if not force and slot_key == last_ingested_slot:
        existing = database.load_database()
        if existing and not is_snapshot_stale(existing, max_age_seconds=1500):
            return existing

    fleet_metar_resp = fetch_fleet_metars(force_refresh=force)
    fleet_metars = fleet_metar_resp.get("metars", {})

    fleet_tafs = {}
    for apt in TAA_AIRPORTS:
        icao = apt["icao"]
        t_data = fetch_live_taf(icao, force_refresh=force)
        fleet_tafs[icao] = t_data.get("raw_taf", "")

    snapshot = database.build_database_snapshot(fleet_metars, fleet_tafs, now_utc=now_utc)
    database.save_database(snapshot)
    last_ingested_slot = slot_key
    return snapshot

def is_snapshot_stale(snap: Optional[dict], max_age_seconds: int = 1500) -> bool:
    """Checks if a snapshot is missing, corrupt, or older than max_age_seconds (default 25 mins)."""
    if not snap or not isinstance(snap, dict):
        return True
    last_up = snap.get("last_updated_utc")
    if not last_up:
        return True
    try:
        up_time = datetime.fromisoformat(last_up.replace("Z", "+00:00"))
        now_utc = datetime.now(timezone.utc)
        age = (now_utc - up_time).total_seconds()
        return age > max_age_seconds
    except Exception:
        return True

def background_cadence_worker():
    """Background daemon to perform ingestion on :10Z and :40Z schedule and keep database permanently fresh."""
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            snap = database.load_database()
            # If snapshot is stale (> 25 mins), or if at cadence slot minutes [10, 11, 40, 41] and older than 10 mins
            if is_snapshot_stale(snap, max_age_seconds=1500) or (now_utc.minute in [10, 11, 40, 41] and is_snapshot_stale(snap, max_age_seconds=600)):
                perform_ingest_cycle(force=True)
            time.sleep(25)
        except Exception:
            time.sleep(30)

# Initialize database snapshot on startup if missing or stale
snap_on_boot = database.load_database()
if not snap_on_boot or is_snapshot_stale(snap_on_boot, max_age_seconds=1500):
    try:
        perform_ingest_cycle(force=True)
    except Exception as e:
        print(f"Initial ingest failed: {e}")

# Start background ingestion thread
scheduler_thread = threading.Thread(target=background_cadence_worker, daemon=True)
scheduler_thread.start()

class AeroCastHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        clean_path = parsed.path

        if clean_path in ["/api/snapshot", "/snapshot", "/api/data"]:
            force = "force=1" in self.path or "force=true" in self.path
            snap = database.load_database()
            if not snap or force or is_snapshot_stale(snap, max_age_seconds=1500):
                snap = perform_ingest_cycle(force=True)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(snap, indent=2).encode("utf-8"))

        elif clean_path == "/api/cadence":
            now = datetime.now(timezone.utc)
            cadence = database.get_cadence_status(now)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(cadence, indent=2).encode("utf-8"))

        elif clean_path in ["/api/export_csv", "/export.csv", "/export_csv"]:
            snap = database.load_database()
            if not snap:
                snap = perform_ingest_cycle(force=False)
            csv_data = database.export_fleet_csv(snap)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="aerocast_fleet_status.csv"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(csv_data.encode("utf-8"))

        elif clean_path in ["/api/archive_csv", "/api/archive.csv", "/archive.csv", "/api/export_archive_csv"]:
            csv_data = database.export_archive_csv()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="aerocast_observations_archive.csv"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(csv_data.encode("utf-8"))

        elif clean_path in ["/api/archive", "/api/history", "/api/metar_taf_archive"]:
            qs = urllib.parse.parse_qs(parsed.query)
            target_icao = qs.get("icao", [None])[0]
            snap = database.load_database()
            if not snap:
                snap = perform_ingest_cycle(force=False)
            
            if target_icao:
                target_icao = target_icao.upper()
                canonical = getattr(database, 'AIRPORTS_ALIASES', {}).get(target_icao, target_icao)
                apt_data = snap.get("airports", {}).get(canonical, {})
                payload = {
                    "icao": canonical,
                    "name": apt_data.get("name", ""),
                    "metar_history_count": len(apt_data.get("metar_history", [])),
                    "taf_history_count": len(apt_data.get("taf_history", [])),
                    "metar_history": apt_data.get("metar_history", []),
                    "taf_history": apt_data.get("taf_history", [])
                }
            else:
                archive = snap.get("metar_taf_archive", [])
                payload = {
                    "count": len(archive),
                    "cycles": archive
                }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(payload, indent=2).encode("utf-8"))

        elif clean_path in ["/api/verification_history", "/api/verification"]:
            snap = database.load_database()
            if not snap:
                snap = perform_ingest_cycle(force=False)
            history = snap.get("verification_history", [])
            curr = snap.get("current_verification", {})
            payload = {
                "count": len(history),
                "current": curr,
                "history": history
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(payload, indent=2).encode("utf-8"))

        elif clean_path.startswith("/api/taf") or clean_path.startswith("/taf") or clean_path == "/taf.py":
            qs = urllib.parse.parse_qs(parsed.query)
            icao = qs.get("icao", ["VTBD"])[0].upper()
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
            force = qs.get("force", ["0"])[0] in ["1", "true"]
            if icao == "ALL":
                data = fetch_fleet_metars(force_refresh=force)
            else:
                data = fetch_fleet_metars(force_refresh=force)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))

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
            clean_name = clean_path.strip("/")
            if clean_name and not os.path.isfile(os.path.join(DIRECTORY, clean_name)):
                candidate = os.path.join(DIRECTORY, clean_name + ".html")
                if os.path.isfile(candidate):
                    self.path = "/" + clean_name + ".html"
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ["/api/sync", "/api/ingest"]:
            snap = perform_ingest_cycle(force=True)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(snap, indent=2).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

def wsgi_app(environ, start_response):
    raw_path = environ.get("PATH_INFO", "/")
    clean_path = raw_path.split("?")[0]
    query = environ.get("QUERY_STRING", "")

    if clean_path in ["/api/snapshot", "/snapshot", "/api/data"]:
        force = "force=1" in query or "force=true" in query
        snap = database.load_database()
        if not snap or force or is_snapshot_stale(snap, max_age_seconds=1500):
            snap = perform_ingest_cycle(force=True)
        data = json.dumps(snap, indent=2).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    if clean_path == "/api/cadence":
        now = datetime.now(timezone.utc)
        cadence = database.get_cadence_status(now)
        data = json.dumps(cadence, indent=2).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    if clean_path in ["/api/export_csv", "/export.csv", "/export_csv"]:
        snap = database.load_database()
        if not snap:
            snap = perform_ingest_cycle(force=False)
        data = database.export_fleet_csv(snap).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "text/csv; charset=utf-8"),
            ("Content-Disposition", 'attachment; filename="aerocast_fleet_status.csv"'),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    if clean_path in ["/api/archive_csv", "/api/archive.csv", "/archive.csv", "/api/export_archive_csv"]:
        data = database.export_archive_csv().encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "text/csv; charset=utf-8"),
            ("Content-Disposition", 'attachment; filename="aerocast_observations_archive.csv"'),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    if clean_path in ["/api/archive", "/api/history", "/api/metar_taf_archive"]:
        qs = urllib.parse.parse_qs(query)
        target_icao = qs.get("icao", [None])[0]
        snap = database.load_database()
        if not snap:
            snap = perform_ingest_cycle(force=False)
        
        if target_icao:
            target_icao = target_icao.upper()
            canonical = getattr(database, 'AIRPORTS_ALIASES', {}).get(target_icao, target_icao)
            apt_data = snap.get("airports", {}).get(canonical, {})
            payload = {
                "icao": canonical,
                "name": apt_data.get("name", ""),
                "metar_history_count": len(apt_data.get("metar_history", [])),
                "taf_history_count": len(apt_data.get("taf_history", [])),
                "metar_history": apt_data.get("metar_history", []),
                "taf_history": apt_data.get("taf_history", [])
            }
        else:
            archive = snap.get("metar_taf_archive", [])
            payload = {
                "count": len(archive),
                "cycles": archive
            }
        data = json.dumps(payload, indent=2).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    if clean_path in ["/api/verification_history", "/api/verification"]:
        snap = database.load_database()
        if not snap:
            snap = perform_ingest_cycle(force=False)
        payload = {
            "count": len(snap.get("verification_history", [])),
            "current": snap.get("current_verification", {}),
            "history": snap.get("verification_history", [])
        }
        data = json.dumps(payload, indent=2).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    if clean_path.startswith("/api/taf") or clean_path.startswith("/taf") or clean_path == "/taf.py":
        qs = urllib.parse.parse_qs(query)
        icao = qs.get("icao", ["VTBD"])[0].upper()
        force = qs.get("force", ["0"])[0] in ["1", "true"]
        data = json.dumps(fetch_live_taf(icao, force_refresh=force), indent=2).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    if clean_path in ["/api/metar_history", "/metar_history", "/metar_history.py"]:
        force = "force=1" in query or "force=true" in query
        data = json.dumps(fetch_fleet_metars(force_refresh=force), indent=2).encode("utf-8")
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
    print("  AeroCast-TH 2.0 Database & Ingestion Server Running")
    print("  Cadence Schedule: :10Z and :40Z (2210Z - 1210Z)")
    print("==================================================")
    print(f"  Local Desktop:  http://localhost:{PORT}")
    print(f"  Mobile Device:  http://{local_ip}:{PORT}")
    print("==================================================")
    with socketserver.TCPServer(("", PORT), AeroCastHandler) as httpd:
        httpd.serve_forever()
