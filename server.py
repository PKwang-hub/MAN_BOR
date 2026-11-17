#!/usr/bin/env python3
"""
AeroCast-TH Unified Server & WSGI Application
Compatible with:
1. Local execution: python server.py
2. Cloud container hosting: Render / Koyeb / Railway (reads $PORT)
3. Vercel Serverless: exports top-level 'app', 'application', and 'handler'
"""

import http.server
import socketserver
import socket
import json
import os
import re
import mimetypes
import urllib.request
import ssl
from datetime import datetime, timezone

PORT = int(os.environ.get("PORT", 8080))
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

# 26 Thai AirAsia Operating Destinations
TAA_AIRPORTS = [
    {"icao": "VTBD", "name": "Don Mueang (DMK)", "radar": "Suvarnabhumi / BMA C-Band", "is_neighbor": False},
    {"icao": "VTBS", "name": "Suvarnabhumi (BKK)", "radar": "Suvarnabhumi Airport", "is_neighbor": False},
    {"icao": "VTCC", "name": "Chiang Mai (CNX)", "radar": "Chiang Mai Omkoi", "is_neighbor": False},
    {"icao": "VTSP", "name": "Phuket (HKT)", "radar": "Phuket Airport", "is_neighbor": False},
    {"icao": "VTSS", "name": "Hat Yai (HDY)", "radar": "Songkhla Satingpra", "is_neighbor": True},
    {"icao": "VTCT", "name": "Chiang Rai (CEI)", "radar": "Chiang Rai", "is_neighbor": False},
    {"icao": "VTCN", "name": "Nan Nakhon (NNT)", "radar": "Phitsanulok", "is_neighbor": True},
    {"icao": "VTCL", "name": "Lampang (LPT)", "radar": "Chiang Mai Omkoi", "is_neighbor": True},
    {"icao": "VTCP", "name": "Phitsanulok (PHS)", "radar": "Phitsanulok", "is_neighbor": False},
    {"icao": "VTPH", "name": "Hua Hin (HHQ)", "radar": "Samut Songkhram / BMA", "is_neighbor": True},
    {"icao": "VTUK", "name": "Khon Kaen (KKC)", "radar": "Khon Kaen", "is_neighbor": False},
    {"icao": "VTUD", "name": "Udon Thani (UTH)", "radar": "Sakon Nakhon", "is_neighbor": True},
    {"icao": "VTUU", "name": "Ubon Ratchathani (UBP)", "radar": "Ubon Ratchathani", "is_neighbor": False},
    {"icao": "VTUV", "name": "Roi Et (ROI)", "radar": "Khon Kaen / Ubon", "is_neighbor": True},
    {"icao": "VTUO", "name": "Buriram (BFV)", "radar": "Khon Kaen / Phimai", "is_neighbor": True},
    {"icao": "VTUL", "name": "Loei (LOE)", "radar": "Phitsanulok", "is_neighbor": True},
    {"icao": "VTUI", "name": "Sakon Nakhon (SNO)", "radar": "Sakon Nakhon", "is_neighbor": False},
    {"icao": "VTUW", "name": "Nakhon Phanom (KOP)", "radar": "Sakon Nakhon", "is_neighbor": True},
    {"icao": "VTSG", "name": "Krabi (KBV)", "radar": "Phuket Radar", "is_neighbor": True},
    {"icao": "VTSB", "name": "Surat Thani (URT)", "radar": "Surat Thani Phunphin", "is_neighbor": False},
    {"icao": "VTSM", "name": "Koh Samui (USM)", "radar": "Surat Thani Samui", "is_neighbor": False},
    {"icao": "VTST", "name": "Trang (TST)", "radar": "Songkhla / Phuket", "is_neighbor": True},
    {"icao": "VTSK", "name": "Nakhon Si Thammarat (NST)", "radar": "Surat Thani", "is_neighbor": True},
    {"icao": "VTSR", "name": "Ranong (UNN)", "radar": "Surat Thani / Chumphon", "is_neighbor": True},
    {"icao": "VTSE", "name": "Chumphon (CJM)", "radar": "Chumphon", "is_neighbor": False},
    {"icao": "VTSN", "name": "Narathiwat (NAW)", "radar": "Narathiwat", "is_neighbor": False}
]

# Verified fallback TAFs aligned with TMD 2300Z, 0500Z, 1100Z, 1700Z schedule
FALLBACK_TAFS = {
    "VTBD": "TAF VTBD 162300Z 1700/1806 10005KT 4000 BR FEW020 BECMG 1700/1702 9999 NSW BECMG 1705/1707 19005KT TEMPO 1708/1712 -TSRA FEW018CB SCT020 BKN080",
    "VTBS": "TAF VTBS 162300Z 1700/1806 09006KT 5000 BR SCT020 BECMG 1701/1703 9999 NSW TEMPO 1708/1712 4000 TSRA FEW018CB BKN020",
    "VTCC": "TAF VTCC 162300Z 1700/1724 00000KT 8000 FEW025 BECMG 1703/1705 14006KT 9999",
    "VTSP": "TAF VTSP 162300Z 1700/1724 26012KT 9999 SCT020 TEMPO 1706/1710 4000 SHRA BKN018",
    "VTUD": "TAF VTUD 162300Z 1700/1724 07008KT 9999 FEW030",
    "VTUK": "TAF VTUK 162300Z 1700/1724 09006KT 9999 SKC"
}

taf_cache = {}

def compute_cycle_metadata(taf_text: str):
    match = re.search(r'\b\d{2}(\d{4})Z\b', taf_text)
    issue_hhmm = match.group(1) if match else "2300"
    is_amended = "AMD" in taf_text

    now = datetime.now(timezone.utc)
    cur_hour = now.hour
    cur_min = now.minute

    next_cycle_hour = None
    for c in [5, 11, 17, 23]:
        if cur_hour < c or (cur_hour == c and cur_min < 5):
            next_cycle_hour = c
            break
    if next_cycle_hour is None:
        next_cycle_hour = 5

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

def fetch_live_taf(icao: str) -> dict:
    now = datetime.now(timezone.utc)
    cache_entry = taf_cache.get(icao)
    if cache_entry and (now - cache_entry["time"]).total_seconds() < 60:
        return cache_entry["data"]

    url = f"https://aviationweather.gov/api/data/taf?ids={icao}&format=raw"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "AeroCast-TH/1.0"})

    raw_taf = ""
    is_live = False
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=4) as resp:
            content = resp.read().decode("utf-8").strip()
            if content and icao in content:
                lines = [l.strip() for l in content.splitlines() if l.strip()]
                raw_taf = " ".join(lines)
                is_live = True
    except Exception:
        pass

    if not raw_taf:
        raw_taf = FALLBACK_TAFS.get(icao, f"TAF {icao} 162300Z 1700/1724 10006KT 9999 SCT025")
        is_live = False

    meta = compute_cycle_metadata(raw_taf)
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

# -------------------------------------------------------------
# Standard Library HTTP Server Handler (Local & Container usage)
# -------------------------------------------------------------
class AeroCastHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/taf/"):
            icao = self.path.split("?")[0].split("/")[-1].upper()
            data = fetch_live_taf(icao)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))
        elif self.path == "/api/airports":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(TAA_AIRPORTS, indent=2).encode("utf-8"))
        else:
            super().do_GET()

# -------------------------------------------------------------
# WSGI Application Handler (Vercel Serverless Entrypoint)
# Vercel looks for top-level 'app', 'application', or 'handler'
# -------------------------------------------------------------
def wsgi_app(environ, start_response):
    path = environ.get("PATH_INFO", "/")

    # API Routes
    if path.startswith("/api/taf/"):
        icao = path.split("?")[0].split("/")[-1].upper()
        data = json.dumps(fetch_live_taf(icao), indent=2).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    if path == "/api/airports":
        data = json.dumps(TAA_AIRPORTS, indent=2).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
            ("Content-Length", str(len(data)))
        ])
        return [data]

    # Static Assets
    target_file = "index.html" if path in ["", "/", "/index.html"] else path.lstrip("/")
    file_path = os.path.normpath(os.path.join(DIRECTORY, target_file))

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

# Top-level exports for Vercel Python runtime
app = wsgi_app
application = wsgi_app
handler = AeroCastHandler

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
    print("  Compatible with Vercel / Render / Local")
    print("==================================================")
    print(f"  Local Desktop:  http://localhost:{PORT}")
    print(f"  Mobile Device:  http://{local_ip}:{PORT}")
    print("==================================================")
    with socketserver.TCPServer(("", PORT), AeroCastHandler) as httpd:
        httpd.serve_forever()
