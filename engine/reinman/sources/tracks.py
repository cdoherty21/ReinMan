"""Track registry: time zone and approximate coordinates (for weather lookups only).

Keys are Equibase-style track codes. Coordinates are approximate track locations
(enough for an NWS forecast grid cell). Unknown tracks still work — they just skip
the NWS weather supplement. Add tracks here as they appear.
"""
from __future__ import annotations

TRACKS: dict[str, dict] = {
    "AQU": {"name": "Aqueduct", "tz": "America/New_York", "lat": 40.672, "lon": -73.830},
    "BAQ": {"name": "Belmont at Aqueduct", "tz": "America/New_York", "lat": 40.672, "lon": -73.830},
    "BEL": {"name": "Belmont Park", "tz": "America/New_York", "lat": 40.715, "lon": -73.722},
    "SAR": {"name": "Saratoga", "tz": "America/New_York", "lat": 43.073, "lon": -73.771},
    "FL": {"name": "Finger Lakes", "tz": "America/New_York", "lat": 42.974, "lon": -77.329},
    "GP": {"name": "Gulfstream Park", "tz": "America/New_York", "lat": 25.979, "lon": -80.139},
    "GPW": {"name": "Gulfstream Park West", "tz": "America/New_York", "lat": 25.979, "lon": -80.139},
    "TAM": {"name": "Tampa Bay Downs", "tz": "America/New_York", "lat": 28.034, "lon": -82.646},
    "CD": {"name": "Churchill Downs", "tz": "America/New_York", "lat": 38.203, "lon": -85.770},
    "KEE": {"name": "Keeneland", "tz": "America/New_York", "lat": 38.046, "lon": -84.607},
    "ELP": {"name": "Ellis Park", "tz": "America/Chicago", "lat": 37.910, "lon": -87.563},
    "KD": {"name": "Kentucky Downs", "tz": "America/Chicago", "lat": 36.640, "lon": -86.582},
    "TDN": {"name": "Thistledown", "tz": "America/New_York", "lat": 41.404, "lon": -81.519},
    "MVR": {"name": "Mahoning Valley", "tz": "America/New_York", "lat": 41.094, "lon": -80.712},
    "BTP": {"name": "Belterra Park", "tz": "America/New_York", "lat": 39.009, "lon": -84.367},
    "IND": {"name": "Horseshoe Indianapolis", "tz": "America/Indiana/Indianapolis", "lat": 39.598, "lon": -85.857},
    "LRL": {"name": "Laurel Park", "tz": "America/New_York", "lat": 39.103, "lon": -76.828},
    "PIM": {"name": "Pimlico", "tz": "America/New_York", "lat": 39.352, "lon": -76.674},
    "MTH": {"name": "Monmouth Park", "tz": "America/New_York", "lat": 40.311, "lon": -74.010},
    "PRX": {"name": "Parx Racing", "tz": "America/New_York", "lat": 40.115, "lon": -74.961},
    "PEN": {"name": "Penn National", "tz": "America/New_York", "lat": 40.372, "lon": -76.700},
    "PID": {"name": "Presque Isle Downs", "tz": "America/New_York", "lat": 42.064, "lon": -80.035},
    "DEL": {"name": "Delaware Park", "tz": "America/New_York", "lat": 39.692, "lon": -75.665},
    "CT": {"name": "Charles Town", "tz": "America/New_York", "lat": 39.281, "lon": -77.845},
    "MNR": {"name": "Mountaineer", "tz": "America/New_York", "lat": 40.517, "lon": -80.585},
    "CNL": {"name": "Colonial Downs", "tz": "America/New_York", "lat": 37.441, "lon": -77.041},
    "FG": {"name": "Fair Grounds", "tz": "America/Chicago", "lat": 29.983, "lon": -90.081},
    "DED": {"name": "Delta Downs", "tz": "America/Chicago", "lat": 30.244, "lon": -93.655},
    "EVD": {"name": "Evangeline Downs", "tz": "America/Chicago", "lat": 30.272, "lon": -92.023},
    "LAD": {"name": "Louisiana Downs", "tz": "America/Chicago", "lat": 32.522, "lon": -93.658},
    "OP": {"name": "Oaklawn Park", "tz": "America/Chicago", "lat": 34.483, "lon": -93.057},
    "LS": {"name": "Lone Star Park", "tz": "America/Chicago", "lat": 32.786, "lon": -96.999},
    "HOU": {"name": "Sam Houston", "tz": "America/Chicago", "lat": 29.937, "lon": -95.509},
    "RP": {"name": "Remington Park", "tz": "America/Chicago", "lat": 35.537, "lon": -97.446},
    "WRD": {"name": "Will Rogers Downs", "tz": "America/Chicago", "lat": 36.304, "lon": -95.632},
    "PRM": {"name": "Prairie Meadows", "tz": "America/Chicago", "lat": 41.596, "lon": -93.497},
    "CBY": {"name": "Canterbury Park", "tz": "America/Chicago", "lat": 44.774, "lon": -93.503},
    "FON": {"name": "Fonner Park", "tz": "America/Chicago", "lat": 40.912, "lon": -98.335},
    "HAW": {"name": "Hawthorne", "tz": "America/Chicago", "lat": 41.824, "lon": -87.752},
    "FAN": {"name": "FanDuel Sportsbook & Horse Racing", "tz": "America/Chicago", "lat": 38.626, "lon": -90.110},
    "SA": {"name": "Santa Anita", "tz": "America/Los_Angeles", "lat": 34.140, "lon": -118.043},
    "DMR": {"name": "Del Mar", "tz": "America/Los_Angeles", "lat": 32.976, "lon": -117.260},
    "GG": {"name": "Golden Gate Fields", "tz": "America/Los_Angeles", "lat": 37.885, "lon": -122.309},
    "LRC": {"name": "Los Alamitos", "tz": "America/Los_Angeles", "lat": 33.804, "lon": -118.047},
    "EMD": {"name": "Emerald Downs", "tz": "America/Los_Angeles", "lat": 47.326, "lon": -122.225},
    "TUP": {"name": "Turf Paradise", "tz": "America/Phoenix", "lat": 33.641, "lon": -112.098},
    "ZIA": {"name": "Zia Park", "tz": "America/Denver", "lat": 32.707, "lon": -103.144},
    "SUN": {"name": "Sunland Park", "tz": "America/Denver", "lat": 31.799, "lon": -106.556},
    "RUI": {"name": "Ruidoso Downs", "tz": "America/Denver", "lat": 33.338, "lon": -105.669},
    "ARP": {"name": "Arapahoe Park", "tz": "America/Denver", "lat": 39.636, "lon": -104.745},
    "WO": {"name": "Woodbine", "tz": "America/Toronto", "lat": 43.712, "lon": -79.604},
}


def track_info(track_id: str) -> dict:
    return TRACKS.get((track_id or "").upper(), {})
