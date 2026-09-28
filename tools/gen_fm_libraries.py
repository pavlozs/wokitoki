"""Generate FM broadcast libraries (library/cz/fm-*.yaml) from ČTÚ open data.

Source: ČTÚ open data "Rozhlasové vysílače" (https://data.ctu.gov.cz/dataset/rozhlasove-vysilace),
CSV with every licensed transmitter: programme, frequency, ERP and position.

For each regional capital the strongest transmitter of every programme is
estimated from ERP / distance² (terrain is ignored – a rough guide, not a
coverage map). Stations are ordered by that estimate, so a profile that takes
the first N gets the strongest ones.

    python tools/gen_fm_libraries.py                 # download the CSV
    python tools/gen_fm_libraries.py --csv file.csv  # use a local copy
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import math
import re
import unicodedata
import urllib.request
from pathlib import Path

CSV_URL = (
    "https://data.ctu.gov.cz/sites/default/files/imports/import_rozhlas/prehled_rozhlasovych_kmitoctu.csv"
)
OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "wokitoki" / "library" / "cz"

# id, name, region, latitude, longitude
PLACES = [
    ("praha", "Praha", "Prague", 50.0755, 14.4378),
    ("brno", "Brno", "South Moravian region", 49.1951, 16.6068),
    ("ostrava", "Ostrava", "Moravian-Silesian region", 49.8209, 18.2625),
    ("plzen", "Plzeň", "Plzeň region", 49.7384, 13.3736),
    ("ceske-budejovice", "České Budějovice", "South Bohemian region", 48.9745, 14.4743),
    ("hradec-kralove", "Hradec Králové", "Hradec Králové region", 50.2104, 15.8327),
    ("liberec", "Liberec", "Liberec region", 50.7663, 15.0543),
    ("olomouc", "Olomouc", "Olomouc region", 49.5938, 17.2509),
    ("pardubice", "Pardubice", "Pardubice region", 50.0343, 15.7812),
    ("jihlava", "Jihlava", "Vysočina region", 49.3961, 15.5912),
    ("zlin", "Zlín", "Zlín region", 49.2265, 17.6707),
    ("usti-nad-labem", "Ústí nad Labem", "Ústí nad Labem region", 50.6607, 14.0323),
    ("karlovy-vary", "Karlovy Vary", "Karlovy Vary region", 50.2319, 12.8720),
]
MIN_SCORE = 0.5  # ERP [W] / distance² [km²]: ≈ 1 kW at 45 km, 100 W at 14 km
MAX_DISTANCE_KM = 80.0  # a handheld whip does not get further, whatever the ERP
MAX_STATIONS = 30
MIN_DISTANCE_KM = 5.0

# ČRo regional programmes: key words → short name (≤ 12 ASCII characters)
CRO_REGIONS = {
    "brno": "CRo Brno", "hradec": "CRo Hradec", "karlovy": "CRo K. Vary", "liberec": "CRo Liberec",
    "olomouc": "CRo Olomouc", "ostrava": "CRo Ostrava", "pardubice": "CRo Pardubic", "plzen": "CRo Plzen",
    "sever": "CRo Sever", "stredni": "CRo StrCechy", "sc": "CRo StrCechy", "vysocina": "CRo Vysocina",
    "zlin": "CRo Zlin", "budejovice": "CRo CBudejov", "radio praha": "CRo R. Praha",
}  # fmt: skip
KNOWN = [  # (regex on the normalised programme name, full name, short name)
    (r"^cro (1|radiozurnal)$", "ČRo Radiožurnál", "Radiozurnal"),
    (r"^cro (2|dvojka)$", "ČRo Dvojka", "CRo Dvojka"),
    (r"^cro (3|vltava)$", "ČRo Vltava", "CRo Vltava"),
    (r"^cro plus$", "ČRo Plus", "CRo Plus"),
    (r"^cro 5$", "ČRo regional (ČRo 5)", "CRo Region"),
    (r"evropa 2", "Evropa 2", "Evropa 2"),
    (r"^frekvence 1$", "Frekvence 1", "Frekvence 1"),
    (r"^radio impuls$", "Rádio Impuls", "Impuls"),
    (r"^kiss", "Kiss", "Kiss"),
    (r"^radio beat$", "Radio Beat", "Radio Beat"),
    (r"^rock radio$", "Rock Radio", "Rock Radio"),
    (r"^country", "Country Radio", "Country"),
    (r"^radio blanik$", "Rádio Blaník", "Blanik"),
    (r"^hey radio$", "HEY Radio", "Hey Radio"),
    (r"^fajn radio$", "Fajn Radio", "Fajn Radio"),
    (r"^radio cas rock$", "Radio Čas Rock", "Cas Rock"),
    (r"^radio cas", "Radio Čas", "Radio Cas"),
    (r"skyrock", "Rádio Haná SkyRock", "Hana SkyRock"),
    (r"^radio hana", "Rádio Haná", "Radio Hana"),
    (r"^expres fm$", "Expres FM", "Expres FM"),
    (r"^hitradio cerna hora$", "Hitrádio Černá Hora", "Hit C. Hora"),
    (r"^hitradio north music$", "Hitrádio North Music", "Hit North"),
    (r"^hitradio fm plus$", "Hitrádio FM Plus", "Hit FM Plus"),
    (r"^hitradio (.+)$", None, None),  # "Hit <rest>"
    (r"^signal radio", "Signál Rádio", "Signal Radio"),
    (r"^radio krokodyl$", "Rádio Krokodýl", "Krokodyl"),
    (r"^radio proglas$", "Radio Proglas", "Proglas"),
    (r"^classic praha$", "Classic Praha", "Classic"),
    (r"^radio dechovka$", "Rádio Dechovka", "Dechovka"),
    (r"^radio jih cimbalka$", "Rádio Jih Cimbálka", "Cimbalka"),
]


def ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def normalise(program: str) -> str:
    text = ascii_fold(program).lower().replace("rádio", "radio")
    text = re.sub(r"\s*-\s*", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def names(program: str) -> tuple[str, str, str]:
    """→ (key to merge spellings, full name, short name ≤ 12 ASCII)."""
    norm = normalise(program)
    if norm.startswith("cro "):
        for word, short in CRO_REGIONS.items():
            if word in norm and not re.match(r"^cro (1|2|3|5|plus|dvojka|vltava|radiozurnal)$", norm):
                return short, program.strip(), short
    for pattern, full, short in KNOWN:
        match = re.search(pattern, norm)
        if match:
            if full is None:  # Hitrádio <name>
                rest = match.group(1).title()
                return f"hit {rest}", f"Hitrádio {rest}", f"Hit {ascii_fold(rest)}"[:12]
            return short, full, short
    words = ascii_fold(program).title().split()
    short = " ".join(words)
    if len(short) > 12 and words[0] in ("Radio", "Rádio"):
        short = " ".join(words[1:])
    return norm, program.strip(), short[:12].rstrip()


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load(text: str) -> list[dict]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        if not row["Typ"].startswith("FM"):
            continue
        freq = float(row["Kmitočet MHz"])
        if not 87.5 <= freq <= 108.0:
            continue
        out.append({
            "transmitter": row["Vysílač"].strip(),
            "program": row["Program"].strip(),
            "freq": round(freq, 2),
            "erp": float(row["ERP W"] or 0),
            "lat": float(row["Zeměpisná šířka"]),
            "lon": float(row["Zeměpisná délka"]),
        })  # fmt: skip
    return out


def stations_for(rows: list[dict], lat: float, lon: float) -> list[dict]:
    best: dict[str, dict] = {}
    for row in rows:
        d = max(distance_km(lat, lon, row["lat"], row["lon"]), MIN_DISTANCE_KM)
        if d > MAX_DISTANCE_KM:
            continue
        score = row["erp"] / d**2
        if score < MIN_SCORE:
            continue
        key, full, short = names(row["program"])
        if key not in best or score > best[key]["score"]:
            best[key] = {**row, "score": score, "distance": d, "name": short, "full": full}
    by_freq: dict[float, dict] = {}
    for station in sorted(best.values(), key=lambda s: -s["score"]):
        by_freq.setdefault(station["freq"], station)  # one programme per frequency
    return sorted(by_freq.values(), key=lambda s: -s["score"])[:MAX_STATIONS]


def quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_library(place: tuple, stations: list[dict], today: str) -> Path:
    pid, name, region, lat, lon = place
    lines = [
        f"# FM broadcast stations receivable around {name} ({region}).",
        "# GENERATED by tools/gen_fm_libraries.py from ČTÚ open data – do not edit by hand.",
        f"library: cz/fm-{pid}",
        "kind: broadcast",
        f"title: {quote(f'FM broadcast – {name}')}",
        "country: CZ",
        "band: fm",
        f"location: {{name: {quote(name)}, lat: {lat}, lon: {lon}}}",
        "source: >",
        f"  ČTÚ open data 'Rozhlasové vysílače' (data.ctu.gov.cz), downloaded {today}. Strongest",
        "  transmitter per programme within 80 km, estimated from ERP / distance² – terrain ignored, so",
        "  a station may be weaker or stronger in practice. Ordered from the strongest.",
        "stations:",
    ]
    for s in stations:
        lines.append(
            f"  - {{name: {quote(s['name'])}, freq: {s['freq']:g}, program: {quote(s['full'])}, "
            f"transmitter: {quote(s['transmitter'].title())}, erp_kw: {s['erp'] / 1000:.3g}, "
            f"distance_km: {s['distance']:.0f}}}"
        )
    path = OUT_DIR / f"fm-{pid}.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--csv", type=Path, help="local copy of the ČTÚ CSV")
    args = parser.parse_args()
    if args.csv:
        text = args.csv.read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(CSV_URL, timeout=60) as response:
            text = response.read().decode("utf-8")
    rows = load(text)
    # the date the data was downloaded: now, or the local file's modification time
    when = (
        dt.datetime.fromtimestamp(args.csv.stat().st_mtime).astimezone()
        if args.csv
        else dt.datetime.now().astimezone()
    )
    today = when.date().isoformat()
    for place in PLACES:
        stations = stations_for(rows, place[3], place[4])
        path = write_library(place, stations, today)
        print(f"{path.name}: {len(stations)} stations")


if __name__ == "__main__":
    main()
