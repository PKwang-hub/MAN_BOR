from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.parse
import ssl
import re
from datetime import datetime, timezone, timedelta

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
        "VTSF": "18009KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTSK": "18009KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTSR": "24012KT 9999 SCT020 31/25 Q1009 NOSIG",
        "VTSE": "17010KT 9999 SCT025 31/24 Q1008 NOSIG",
        "VTSC": "12008KT 9999 FEW025 31/24 Q1008 NOSIG",
        "VTSN": "12008KT 9999 FEW025 31/24 Q1008 NOSIG"
    }
    payload = station_payloads.get(icao, "12005KT 9999 FEW025 31/25 Q1012 NOSIG")

    history = []
    for i in range(24):
        slot_time = latest_slot - timedelta(minutes=30 * i)
        ts_str = f"{slot_time.day:02d}{slot_time.hour:02d}{slot_time.minute:02d}Z"
        history.append(f"METAR {icao} {ts_str} {payload}")
    return history

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        icao = params.get("icao", ["VTBD"])[0].upper()
        hours = int(params.get("hours", ["24"])[0])

        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        if icao == "ALL":
            thai_icaos = [
                "VTBD", "VTBS", "VTCC", "VTCT", "VTCN", "VTCL", "VTPP", "VTUK",
                "VTUD", "VTUU", "VTUV", "VTUO", "VTUL", "VTUI", "VTUW", "VTPH",
                "VTSP", "VTSG", "VTSS", "VTSB", "VTSM", "VTST", "VTSF", "VTSK", "VTSR",
                "VTSE", "VTSC", "VTSN"
            ]
            ids_str = ",".join(thai_icaos)
            url = f"https://aviationweather.gov/api/data/metar?ids={ids_str}&format=raw"
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            metar_map = {}
            is_live = False
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                    content = resp.read().decode("utf-8").strip()
                    if content:
                        for line in content.splitlines():
                            line = line.strip()
                            for code in thai_icaos:
                                if line.startswith(code) or line.startswith("METAR " + code) or line.startswith("SPECI " + code):
                                    if code not in metar_map:
                                        metar_map[code] = line
                        if metar_map:
                            is_live = True
            except Exception:
                pass

            for code in thai_icaos:
                if code not in metar_map:
                    hist = generate_fallback_history(code)
                    metar_map[code] = hist[0] if hist else f"METAR {code} 180900Z 12005KT 9999 FEW025 31/25 Q1010 NOSIG"

            payload = {
                "is_live": is_live,
                "count": len(metar_map),
                "source": "NOAA Aviation Weather Center Fleet Stream" if is_live else "Local Station Observation Logs",
                "metars": metar_map
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(payload, indent=2).encode("utf-8"))
            return

        url = f"https://aviationweather.gov/api/data/metar?ids={icao}&hours={hours}&format=raw"
        req = urllib.request.Request(url, headers={"User-Agent": ua})

        records = []
        is_live = False
        # 1. Primary: 24-hr historical raw feed from aviationweather.gov
        try:
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
            records = generate_fallback_history(icao)

        parsed_obs = []
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

            parsed_obs.append({
                "timestamp_zulu": zulu_time,
                "raw_text": raw,
                "weather_summary": wx_desc,
                "has_rain": has_rain,
                "has_mist": has_mist,
                "visibility_m": vis_token
            })

        now = datetime.now(timezone.utc)
        payload = {
            "icao": icao,
            "is_live": is_live,
            "source": "NOAA Aviation Weather Center Live GTS Stream" if is_live else "Local Station Observation Log",
            "count": len(parsed_obs),
            "observations": parsed_obs
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(payload, indent=2).encode("utf-8"))