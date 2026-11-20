#!/usr/bin/env python3
"""
AeroCast TAF & METAR Aviation Decoder Engine
Implements strict decoding standards from:
- NOAA / NWS Aviation TAF Card (weather.gov/media/okx/Aviation/TAF_Card.pdf)
- FAA ATO METAR Key (faa.gov/sites/faa.gov/files/about/office_org/headquarters_offices/ato/METAR_KEY.pdf)
- ICAO Annex 3 / WMO Manual No. 782

Handles full TAF structure:
- Header: Type (TAF, AMD, COR), Station ICAO, Issue Time (DDHHMMZ), Valid Window (DDHH/DDHH)
- Initial Base Condition (Wind, Vis, Weather, Clouds, LLWS)
- Change Groups:
  - FMddhhmm (From: rapid full condition reset)
  - BECMG ddhh/ddhh (Becoming: gradual change, modifies specified elements)
  - TEMPO ddhh/ddhh (Temporary: fluctuations lasting < 1 hr each, < 50% window)
  - PROB30 / PROB40 [TEMPO] ddhh/ddhh (Probability 30%/40% of phenomena)
- Exact point-in-time forecast resolution for any observation timestamp T_obs.
"""

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any, Tuple

# Weather Phenomena Components
INTENSITY_MAP = {
    "-": "Light",
    "+": "Heavy",
    "VC": "In Vicinity",
    "": "Moderate"
}

DESCRIPTOR_MAP = {
    "MI": "Shallow",
    "PR": "Partial",
    "BC": "Patches",
    "DR": "Low Drifting",
    "BL": "Blowing",
    "SH": "Showers of",
    "TS": "Thunderstorm with",
    "FZ": "Freezing"
}

PRECIPITATION_MAP = {
    "DZ": "Drizzle",
    "RA": "Rain",
    "SN": "Snow",
    "SG": "Snow Grains",
    "IC": "Ice Crystals",
    "PL": "Ice Pellets",
    "GR": "Hail (>5mm)",
    "GS": "Small Hail / Snow Pellets (<5mm)",
    "UP": "Unknown Precipitation"
}

OBSCURATION_MAP = {
    "BR": "Mist (Vis 1000m-5000m)",
    "FG": "Fog (Vis <1000m)",
    "FU": "Smoke",
    "VA": "Volcanic Ash",
    "DU": "Widespread Dust",
    "SA": "Sand",
    "HZ": "Haze",
    "PY": "Spray"
}

OTHER_WX_MAP = {
    "SQ": "Squalls",
    "PO": "Dust Whirls",
    "FC": "Funnel Cloud / Tornado",
    "SS": "Sandstorm",
    "DS": "Duststorm",
    "NSW": "No Significant Weather"
}

CLOUD_COVER_MAP = {
    "SKC": "Sky Clear",
    "CLR": "Clear below 12000ft",
    "FEW": "Few (1-2 oktas)",
    "SCT": "Scattered (3-4 oktas)",
    "BKN": "Broken Ceiling (5-7 oktas)",
    "OVC": "Overcast Ceiling (8 oktas)",
    "VV": "Vertical Visibility / Indefinite Ceiling",
    "NSC": "Nil Significant Cloud"
}

WEATHER_TOKEN_REGEX = re.compile(
    r'^(-|\+|VC)?(MI|PR|BC|DR|BL|SH|TS|FZ)?(DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|SQ|PO|FC|SS|DS)+$|^NSW$'
)

WIND_REGEX = re.compile(r'^(VRB|\d{3})(\d{2,3})(?:G(\d{2,3}))?KT$')
VIS_METERS_REGEX = re.compile(r'^\d{4}$')
VIS_SM_REGEX = re.compile(r'^(P)?(\d+)?(?:(\d+)/(\d+))?SM$')
CLOUD_REGEX = re.compile(r'^(SKC|CLR|NSC|FEW|SCT|BKN|OVC|VV)(\d{3})?(CB|TCU)?$')

@dataclass
class WeatherPhenomenon:
    raw: str
    intensity: str = ""
    descriptor: str = ""
    precip: str = ""
    obscuration: str = ""
    other: str = ""
    is_rain: bool = False
    is_ts: bool = False
    is_reduced_vis: bool = False
    description: str = ""

@dataclass
class CloudLayer:
    raw: str
    cover: str
    height_ft: Optional[int]
    is_ceiling: bool
    convective_type: Optional[str] = None
    description: str = ""

@dataclass
class ConditionSet:
    wind_dir: Optional[int] = None
    wind_dir_str: str = "VRB"
    wind_speed_kt: int = 0
    wind_gust_kt: Optional[int] = None
    visibility_m: int = 9999
    visibility_sm: Optional[float] = None
    cavok: bool = False
    weather: List[WeatherPhenomenon] = field(default_factory=list)
    clouds: List[CloudLayer] = field(default_factory=list)
    raw_tokens: List[str] = field(default_factory=list)

    @property
    def has_rain(self) -> bool:
        return any(w.is_rain for w in self.weather)

    @property
    def has_ts(self) -> bool:
        return any(w.is_ts for w in self.weather)

    @property
    def has_reduced_vis(self) -> bool:
        return self.visibility_m < 5000 or (self.visibility_sm is not None and self.visibility_sm < 3.0)

    @property
    def ceiling_ft(self) -> Optional[int]:
        for c in self.clouds:
            if c.is_ceiling and c.height_ft is not None:
                return c.height_ft
        return None

@dataclass
class TafGroup:
    group_type: str  # BASE, FM, BECMG, TEMPO, PROB30, PROB40, PROB30_TEMPO, PROB40_TEMPO
    raw_header: str
    start_day: Optional[int] = None
    start_hour: Optional[int] = None
    start_minute: Optional[int] = 0
    end_day: Optional[int] = None
    end_hour: Optional[int] = None
    conditions: ConditionSet = field(default_factory=ConditionSet)
    raw_text: str = ""

@dataclass
class DecodedTaf:
    raw_text: str
    icao: str
    issue_day: int
    issue_hour: int
    issue_minute: int
    issue_zulu: str
    valid_start_day: int
    valid_start_hour: int
    valid_end_day: int
    valid_end_hour: int
    valid_str: str
    is_amended: bool = False
    is_corrected: bool = False
    base_group: Optional[TafGroup] = None
    change_groups: List[TafGroup] = field(default_factory=list)
    groups: List[TafGroup] = field(default_factory=list)

def parse_weather_token(token: str) -> Optional[WeatherPhenomenon]:
    """Decodes an ICAO/FAA weather token like +TSRA, -SHRA, 4000BR, NSW."""
    token = token.upper()
    if token == "NSW":
        return WeatherPhenomenon(raw=token, other="NSW", description="No Significant Weather")

    # Cloud/convective tokens like FEW020CB are handled by cloud parser, not weather
    if CLOUD_REGEX.match(token):
        return None

    match = WEATHER_TOKEN_REGEX.match(token)
    if not match:
        return None

    intensity_code = match.group(1) or ""
    descriptor_code = match.group(2) or ""
    remaining = token[len(intensity_code) + len(descriptor_code):]

    intensity_text = INTENSITY_MAP.get(intensity_code, "")
    descriptor_text = DESCRIPTOR_MAP.get(descriptor_code, "")

    precip_parts = []
    obscuration_parts = []
    other_parts = []

    # Parse 2-letter tokens in remaining
    i = 0
    while i < len(remaining):
        chunk = remaining[i:i+2]
        if chunk in PRECIPITATION_MAP:
            precip_parts.append(PRECIPITATION_MAP[chunk])
        elif chunk in OBSCURATION_MAP:
            obscuration_parts.append(OBSCURATION_MAP[chunk])
        elif chunk in OTHER_WX_MAP:
            other_parts.append(OTHER_WX_MAP[chunk])
        i += 2

    is_rain = any(p in remaining for p in ["RA", "DZ", "GR", "GS"])
    is_ts = ("TS" in descriptor_code) or ("TS" in remaining)
    is_reduced = any(p in remaining for p in ["FG", "BR", "HZ", "FU", "SA", "DU"]) or is_rain

    desc_words = [intensity_text, descriptor_text] + precip_parts + obscuration_parts + other_parts
    clean_desc = " ".join([w for w in desc_words if w]).strip()

    return WeatherPhenomenon(
        raw=token,
        intensity=intensity_code,
        descriptor=descriptor_code,
        precip=",".join(precip_parts),
        obscuration=",".join(obscuration_parts),
        other=",".join(other_parts),
        is_rain=is_rain,
        is_ts=is_ts,
        is_reduced_vis=is_reduced,
        description=clean_desc
    )

def parse_cloud_token(token: str) -> Optional[CloudLayer]:
    """Decodes cloud token like FEW025, BKN018CB, SCT030, VV002, SKC, CAVOK."""
    token = token.upper()
    match = CLOUD_REGEX.match(token)
    if not match:
        return None

    cover = match.group(1)
    height_str = match.group(2)
    conv = match.group(3)

    height_ft = int(height_str) * 100 if height_str else None
    is_ceiling = cover in ["BKN", "OVC", "VV"]

    cover_text = CLOUD_COVER_MAP.get(cover, cover)
    h_text = f"{height_ft}ft" if height_ft is not None else ""
    c_text = " (Cumulonimbus)" if conv == "CB" else (" (Towering Cumulus)" if conv == "TCU" else "")
    full_desc = f"{cover_text} {h_text}{c_text}".strip()

    return CloudLayer(
        raw=token,
        cover=cover,
        height_ft=height_ft,
        is_ceiling=is_ceiling,
        convective_type=conv,
        description=full_desc
    )

def parse_condition_tokens(tokens: List[str]) -> ConditionSet:
    """Parses a list of meteorological tokens into a ConditionSet."""
    cond = ConditionSet()
    cond.raw_tokens = list(tokens)

    for token in tokens:
        token = token.upper()

        if token == "CAVOK":
            cond.cavok = True
            cond.visibility_m = 9999
            cond.visibility_sm = 6.0
            cond.clouds = [CloudLayer(raw="CAVOK", cover="SKC", height_ft=None, is_ceiling=False, description="Ceiling And Visibility OK")]
            continue

        # Wind
        w_match = WIND_REGEX.match(token)
        if w_match:
            d_str = w_match.group(1)
            cond.wind_dir_str = d_str
            cond.wind_dir = 0 if d_str == "VRB" else int(d_str)
            cond.wind_speed_kt = int(w_match.group(2))
            if w_match.group(3):
                cond.wind_gust_kt = int(w_match.group(3))
            continue

        # Visibility Meters (ICAO standard 4-digit)
        if VIS_METERS_REGEX.match(token):
            cond.visibility_m = int(token)
            cond.visibility_sm = round(int(token) / 1609.34, 1)
            continue

        # Visibility Statute Miles (FAA standard)
        sm_match = VIS_SM_REGEX.match(token)
        if sm_match:
            is_plus = bool(sm_match.group(1))
            whole = int(sm_match.group(2)) if sm_match.group(2) else 0
            num = int(sm_match.group(3)) if sm_match.group(3) else 0
            den = int(sm_match.group(4)) if sm_match.group(4) else 1
            sm_val = float(whole) + (float(num) / float(den) if sm_match.group(3) else 0.0)
            cond.visibility_sm = sm_val
            cond.visibility_m = int(sm_val * 1609.34)
            continue

        # Clouds
        cloud_obj = parse_cloud_token(token)
        if cloud_obj:
            cond.clouds.append(cloud_obj)
            continue

        # Weather Phenomena
        wx_obj = parse_weather_token(token)
        if wx_obj:
            if wx_obj.raw == "NSW":
                cond.weather = []
            else:
                cond.weather.append(wx_obj)
            continue

    return cond

def decode_taf(raw_taf: str) -> DecodedTaf:
    """
    Parses a raw TAF string into full DecodedTaf structure per NOAA / FAA specification.
    """
    clean_text = " ".join(raw_taf.strip().split())
    tokens = clean_text.split()

    if not tokens:
        raise ValueError("Empty TAF string")

    idx = 0
    is_amended = False
    is_corrected = False

    # Check leading report type
    if tokens[idx] == "TAF":
        idx += 1
    if idx < len(tokens) and tokens[idx] == "AMD":
        is_amended = True
        idx += 1
    elif idx < len(tokens) and tokens[idx] == "COR":
        is_corrected = True
        idx += 1

    # Station ICAO
    icao = tokens[idx].upper() if idx < len(tokens) else "VTBD"
    idx += 1

    # Issue Time: DDHHMMZ
    issue_day = 1
    issue_hour = 0
    issue_min = 0
    issue_zulu = "0000Z"
    if idx < len(tokens) and re.match(r'^\d{6}Z$', tokens[idx]):
        iz = tokens[idx]
        issue_day = int(iz[0:2])
        issue_hour = int(iz[2:4])
        issue_min = int(iz[4:6])
        issue_zulu = f"{iz[2:6]}Z"
        idx += 1

    # Valid Period: DDHH/DDHH or DDHHHH
    val_s_day = issue_day
    val_s_hour = issue_hour
    val_e_day = issue_day
    val_e_hour = (issue_hour + 24) % 24
    valid_str = f"{issue_day:02d}00/{(issue_day+1):02d}06"

    if idx < len(tokens):
        v_match = re.match(r'^(\d{2})(\d{2})/(\d{2})(\d{2})$', tokens[idx])
        if v_match:
            val_s_day = int(v_match.group(1))
            val_s_hour = int(v_match.group(2))
            val_e_day = int(v_match.group(3))
            val_e_hour = int(v_match.group(4))
            valid_str = tokens[idx]
            idx += 1
        else:
            v_match_short = re.match(r'^(\d{2})(\d{2})(\d{2})$', tokens[idx])
            if v_match_short:
                val_s_day = int(v_match_short.group(1))
                val_s_hour = int(v_match_short.group(2))
                val_e_hour = int(v_match_short.group(3))
                val_e_day = val_s_day if val_e_hour >= val_s_hour else val_s_day + 1
                valid_str = tokens[idx]
                idx += 1

    # Group splitting by change group indicators:
    # FMddhhmm, BECMG, TEMPO, PROB30 [TEMPO], PROB40 [TEMPO]
    raw_groups: List[Tuple[str, str, List[str]]] = []
    current_type = "BASE"
    current_header = f"{valid_str}"
    current_tokens: List[str] = []

    while idx < len(tokens):
        t = tokens[idx]

        if t.startswith("FM") and re.match(r'^FM\d{6}$', t):
            if current_tokens or current_type != "BASE":
                raw_groups.append((current_type, current_header, current_tokens))
            current_type = "FM"
            current_header = t
            current_tokens = []
            idx += 1
            continue

        if t == "BECMG":
            if current_tokens or current_type != "BASE":
                raw_groups.append((current_type, current_header, current_tokens))
            current_type = "BECMG"
            next_t = tokens[idx + 1] if idx + 1 < len(tokens) else ""
            current_header = f"BECMG {next_t}" if re.match(r'^\d{2}\d{2}/\d{2}\d{2}$', next_t) or re.match(r'^\d{4}/\d{4}$', next_t) else "BECMG"
            idx += 2 if " " in current_header else 1
            current_tokens = []
            continue

        if t == "TEMPO":
            if current_tokens or current_type != "BASE":
                raw_groups.append((current_type, current_header, current_tokens))
            current_type = "TEMPO"
            next_t = tokens[idx + 1] if idx + 1 < len(tokens) else ""
            current_header = f"TEMPO {next_t}" if re.match(r'^\d{2}\d{2}/\d{2}\d{2}$', next_t) or re.match(r'^\d{4}/\d{4}$', next_t) else "TEMPO"
            idx += 2 if " " in current_header else 1
            current_tokens = []
            continue

        if t in ["PROB30", "PROB40"]:
            if current_tokens or current_type != "BASE":
                raw_groups.append((current_type, current_header, current_tokens))
            prob_kind = t
            idx += 1
            has_tempo = False
            if idx < len(tokens) and tokens[idx] == "TEMPO":
                has_tempo = True
                idx += 1
            time_window = ""
            if idx < len(tokens) and (re.match(r'^\d{2}\d{2}/\d{2}\d{2}$', tokens[idx]) or re.match(r'^\d{4}/\d{4}$', tokens[idx])):
                time_window = tokens[idx]
                idx += 1
            current_type = f"{prob_kind}_TEMPO" if has_tempo else prob_kind
            current_header = f"{prob_kind} {'TEMPO ' if has_tempo else ''}{time_window}".strip()
            current_tokens = []
            continue

        current_tokens.append(t)
        idx += 1

    if current_tokens or current_type != "BASE":
        raw_groups.append((current_type, current_header, current_tokens))

    # Parse each group into TafGroup structures
    parsed_groups: List[TafGroup] = []
    base_group: Optional[TafGroup] = None

    for g_type, g_header, g_toks in raw_groups:
        cond = parse_condition_tokens(g_toks)
        s_day, s_h, s_m = None, None, 0
        e_day, e_h = None, None

        if g_type == "BASE":
            s_day, s_h = val_s_day, val_s_hour
            e_day, e_h = val_e_day, val_e_hour
        elif g_type == "FM":
            fm_match = re.match(r'^FM(\d{2})(\d{2})(\d{2})$', g_header)
            if fm_match:
                s_day = int(fm_match.group(1))
                s_h = int(fm_match.group(2))
                s_m = int(fm_match.group(3))
        else:
            w_match = re.search(r'(\d{2})(\d{2})/(\d{2})(\d{2})', g_header)
            if w_match:
                s_day = int(w_match.group(1))
                s_h = int(w_match.group(2))
                e_day = int(w_match.group(3))
                e_h = int(w_match.group(4))
            else:
                w_short = re.search(r'\b(\d{2})(\d{2})\b', g_header)
                if w_short:
                    s_h = int(w_short.group(1))
                    e_h = int(w_short.group(2))
                    s_day = val_s_day
                    e_day = val_s_day

        full_raw = f"{g_header} {' '.join(g_toks)}".strip()
        group_obj = TafGroup(
            group_type=g_type,
            raw_header=g_header,
            start_day=s_day,
            start_hour=s_h,
            start_minute=s_m,
            end_day=e_day,
            end_hour=e_h,
            conditions=cond,
            raw_text=full_raw
        )

        if g_type == "BASE" and base_group is None:
            base_group = group_obj
        parsed_groups.append(group_obj)

    change_groups = [g for g in parsed_groups if g.group_type != "BASE"]

    return DecodedTaf(
        raw_text=clean_text,
        icao=icao,
        issue_day=issue_day,
        issue_hour=issue_hour,
        issue_minute=issue_min,
        issue_zulu=issue_zulu,
        valid_start_day=val_s_day,
        valid_start_hour=val_s_hour,
        valid_end_day=val_e_day,
        valid_end_hour=val_e_hour,
        valid_str=valid_str,
        is_amended=is_amended,
        is_corrected=is_corrected,
        base_group=base_group,
        change_groups=change_groups,
        groups=parsed_groups
    )

def _is_time_in_window(target: datetime, s_day: Optional[int], s_hour: Optional[int], e_day: Optional[int], e_hour: Optional[int]) -> bool:
    """Evaluates whether target UTC falls in [start, end) window, taking month roll into account."""
    if s_day is None or s_hour is None or e_day is None or e_hour is None:
        return False

    t_day = target.day
    t_hour = target.hour
    t_min = target.minute

    # Convert to relative hour offset from start of observation month
    target_rel = (t_day * 24) + t_hour + (t_min / 60.0)
    start_rel = (s_day * 24) + s_hour
    end_rel = (e_day * 24) + e_hour

    # Handle end of month rollover (e.g., day 31 to day 01)
    if end_rel < start_rel:
        end_rel += (30 * 24)
        if target_rel < start_rel:
            target_rel += (30 * 24)

    return start_rel <= target_rel < end_rel

@dataclass
class ResolvedForecast:
    target_utc: datetime
    icao: str
    prevailing: ConditionSet
    active_fluctuations: List[TafGroup] = field(default_factory=list)
    has_forecast_rain: bool = False
    forecast_rain_intensity: Optional[str] = None
    has_forecast_reduced_vis: bool = False
    forecast_min_vis_m: int = 9999
    forecast_wind_str: str = ""
    forecast_clouds_str: str = ""
    active_tempo_groups: List[str] = field(default_factory=list)
    active_group_descriptions: List[str] = field(default_factory=list)
    human_summary: str = ""

def get_forecast_for_time(decoded_taf: DecodedTaf, target_utc: Optional[datetime] = None) -> ResolvedForecast:
    """
    Evaluates exact forecast condition active at target_utc per NOAA TAF Card rules:
    1. Start with base condition.
    2. Apply FM groups whose start time <= target_utc (FM completely resets prevailing conditions).
    3. Apply BECMG groups whose end time <= target_utc (modifies specific elements).
    4. Collect all active TEMPO and PROB groups covering target_utc (temporary fluctuations).
    """
    if target_utc is None:
        target_utc = datetime.now(timezone.utc)

    # 1. Base Prevailing
    base = decoded_taf.base_group.conditions if decoded_taf.base_group else ConditionSet()
    cur_wind_dir = base.wind_dir
    cur_wind_dir_str = base.wind_dir_str
    cur_wind_spd = base.wind_speed_kt
    cur_wind_gust = base.wind_gust_kt
    cur_vis_m = base.visibility_m
    cur_vis_sm = base.visibility_sm
    cur_cavok = base.cavok
    cur_weather = list(base.weather)
    cur_clouds = list(base.clouds)

    t_day = target_utc.day
    t_hour = target_utc.hour
    t_min = target_utc.minute
    t_rel = (t_day * 24) + t_hour + (t_min / 60.0)

    # 2 & 3. Process chronological FM and BECMG groups
    for g in decoded_taf.change_groups:
        if g.group_type == "FM":
            if g.start_day is not None and g.start_hour is not None:
                fm_rel = (g.start_day * 24) + g.start_hour + ((g.start_minute or 0) / 60.0)
                if fm_rel <= t_rel:
                    # FM is a complete reset of all elements
                    c = g.conditions
                    cur_wind_dir = c.wind_dir
                    cur_wind_dir_str = c.wind_dir_str
                    cur_wind_spd = c.wind_speed_kt
                    cur_wind_gust = c.wind_gust_kt
                    cur_vis_m = c.visibility_m
                    cur_vis_sm = c.visibility_sm
                    cur_cavok = c.cavok
                    cur_weather = list(c.weather)
                    cur_clouds = list(c.clouds)

        elif g.group_type == "BECMG":
            if g.start_day is not None and g.end_day is not None:
                becmg_end_rel = (g.end_day * 24) + g.end_hour
                if becmg_end_rel <= t_rel:
                    # BECMG has completed: replace only specified elements
                    c = g.conditions
                    if c.wind_speed_kt > 0 or c.wind_dir is not None:
                        cur_wind_dir = c.wind_dir
                        cur_wind_dir_str = c.wind_dir_str
                        cur_wind_spd = c.wind_speed_kt
                        cur_wind_gust = c.wind_gust_kt
                    if c.visibility_m != 9999 or "9999" in c.raw_tokens or c.cavok:
                        cur_vis_m = c.visibility_m
                        cur_vis_sm = c.visibility_sm
                        cur_cavok = c.cavok
                    if c.weather or "NSW" in c.raw_tokens:
                        cur_weather = list(c.weather)
                    if c.clouds:
                        cur_clouds = list(c.clouds)

    prevailing = ConditionSet(
        wind_dir=cur_wind_dir,
        wind_dir_str=cur_wind_dir_str,
        wind_speed_kt=cur_wind_spd,
        wind_gust_kt=cur_wind_gust,
        visibility_m=cur_vis_m,
        visibility_sm=cur_vis_sm,
        cavok=cur_cavok,
        weather=cur_weather,
        clouds=cur_clouds
    )

    # 4. Collect active TEMPO and PROB groups
    active_fluctuations: List[TafGroup] = []
    active_tempo_headers: List[str] = []
    active_descs: List[str] = []

    has_rain = prevailing.has_rain
    rain_phenom = ", ".join([w.raw for w in prevailing.weather if w.is_rain]) if has_rain else None
    min_vis_m = prevailing.visibility_m

    for g in decoded_taf.change_groups:
        if g.group_type in ["TEMPO", "PROB30", "PROB40", "PROB30_TEMPO", "PROB40_TEMPO"]:
            if _is_time_in_window(target_utc, g.start_day, g.start_hour, g.end_day, g.end_hour):
                active_fluctuations.append(g)
                active_tempo_headers.append(g.raw_header)
                active_descs.append(g.raw_text)

                if g.conditions.has_rain:
                    has_rain = True
                    fl_rain = [w.raw for w in g.conditions.weather if w.is_rain]
                    rain_phenom = ", ".join(fl_rain) if not rain_phenom else f"{rain_phenom} / {', '.join(fl_rain)}"

                if g.conditions.visibility_m < min_vis_m:
                    min_vis_m = g.conditions.visibility_m

    has_reduced_vis = (min_vis_m < 5000)

    # Generate human readable wind and cloud strings
    w_str = f"{cur_wind_dir_str}/{cur_wind_spd}KT"
    if cur_wind_gust:
        w_str += f" G{cur_wind_gust}KT"

    c_str = ", ".join([c.description for c in prevailing.clouds]) if prevailing.clouds else "SKC"

    # Human summary
    summary_parts = [f"Prevailing {w_str}, Vis {cur_vis_m}m"]
    if cur_weather:
        summary_parts.append(", ".join([w.description for w in cur_weather]))
    if active_tempo_headers:
        summary_parts.append(f"Active Fluctuations: {'; '.join(active_tempo_headers)}")

    return ResolvedForecast(
        target_utc=target_utc,
        icao=decoded_taf.icao,
        prevailing=prevailing,
        active_fluctuations=active_fluctuations,
        has_forecast_rain=has_rain,
        forecast_rain_intensity=rain_phenom,
        has_forecast_reduced_vis=has_reduced_vis,
        forecast_min_vis_m=min_vis_m,
        forecast_wind_str=w_str,
        forecast_clouds_str=c_str,
        active_tempo_groups=active_tempo_headers,
        active_group_descriptions=active_descs,
        human_summary=". ".join(summary_parts)
    )

def verify_metar_against_taf(raw_metar: str, raw_taf: str, metar_time: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Evaluates ground-truth METAR against decoded TAF forecast active at the observation time.
    Detects unforecast convective rain and unforecast reduced visibility (<5000m).
    """
    # Parse observation time from METAR (DDHHMMZ)
    if metar_time is None:
        t_match = re.search(r'\b\d{2}(\d{2})(\d{2})Z\b', raw_metar)
        now = datetime.now(timezone.utc)
        if t_match:
            obs_h = int(t_match.group(1))
            obs_m = int(t_match.group(2))
            metar_time = now.replace(hour=obs_h, minute=obs_m, second=0, microsecond=0)
        else:
            metar_time = now

    decoded_t = decode_taf(raw_taf)
    resolved = get_forecast_for_time(decoded_t, metar_time)

    # Parse METAR phenomena
    m_tokens = raw_metar.strip().split()
    m_rain = False
    m_rain_tokens = []
    m_vis_m = 9999

    for tok in m_tokens:
        tok_u = tok.upper()
        if tok_u == "CAVOK":
            m_vis_m = 9999
        elif VIS_METERS_REGEX.match(tok_u):
            m_vis_m = int(tok_u)

        if not CLOUD_REGEX.match(tok_u):
            w = parse_weather_token(tok_u)
            if w and w.is_rain:
                m_rain = True
                m_rain_tokens.append(tok_u)

    m_reduced_vis = (m_vis_m < 5000)

    # Discrepancy Evaluation
    rain_discrepancy = m_rain and not resolved.has_forecast_rain
    vis_discrepancy = m_reduced_vis and not resolved.has_forecast_reduced_vis

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
        "metar_has_rain": m_rain,
        "metar_rain_tokens": m_rain_tokens,
        "metar_vis_m": m_vis_m,
        "taf_has_rain": resolved.has_forecast_rain,
        "taf_rain_phenomena": resolved.forecast_rain_intensity,
        "taf_min_vis_m": resolved.forecast_min_vis_m,
        "active_groups": resolved.active_tempo_groups,
        "active_forecast_summary": resolved.human_summary,
        "observation_time_utc": metar_time.isoformat()
    }
