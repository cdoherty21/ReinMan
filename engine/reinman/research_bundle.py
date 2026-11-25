"""Read the structured per-race research bundles exported for Rein Man."""
from __future__ import annotations

import re

from .sources.tracks import canonical_track_code
from .medication import normalize
from .util import jockey_id, norm_name, now_iso, parse_distance_yards, parse_odds, parse_post_time, surface_code, to_int, trainer_id


def cards_from_bundle(data: object) -> list[dict] | None:
    if not isinstance(data, list) or not data or not all(isinstance(r, dict) and
            {"c", "n", "date", "track", "breed", "entries", "starters", "url"} <= r.keys() for r in data):
        return None
    cards = []
    observed = now_iso()
    for race in data:
        if race["breed"] != "thoroughbred":
            continue
        track = canonical_track_code(race["track"], race["c"])
        if not track:
            continue
        day = race["date"]
        post = re.fullmatch(r"(\d{1,2}:\d{2} [AP]M) (ET|CT|MT|PT)", race.get("post", ""))
        post_time = parse_post_time(day, post[1], tz=post[2]) if post else None
        prepost = bool(post_time and observed < post_time)
        # Pedigree strings are keyed by the printed program number.
        pedigree = {}
        raw = race.get("metadata", {}).get("Pedigrees (Sire - Dam, by Dam Sire)", "")
        for part in raw.split(";"):
            m = re.fullmatch(r"\s*(\d+[A-Z]?)\s*-\s*(.+?)\s+-\s+(.+?)\s*,\s*by\s+(.+?)\s*", part)
            if m:
                pedigree[m[1]] = dict(zip(("sire", "dam", "dam_sire"), m.groups()[1:]))
        metadata = race.get("metadata") or {}
        owners, breeders, equipment_changes = {}, {}, {}
        for field, target in (("Owners", owners), ("Breeders", breeders), ("Equipment Changes", equipment_changes)):
            for part in (metadata.get(field) or "").split(";"):
                match = re.match(r"\s*(\d+[A-Z]?)\s*-\s*(.+)", part.strip())
                if match:
                    target[match[1]] = match[2].strip()
        runners = []
        for row in race["entries"]:
            program = str(row.get("program") or "").upper()
            name = row.get("horse", "")
            if not norm_name(name):
                continue
            program = program if re.fullmatch(r"\d+[A-Z]?", program) else None
            extra = {"research_source": race["url"]}
            identity = {"age_sex": row.get("age_sex"), "owner": owners.get(program), "breeder": breeders.get(program),
                        "equipment_change": equipment_changes.get(program), "source": race["url"]}
            extra["horse_identity"] = {key: value for key, value in identity.items() if value}
            if row.get("medication") is not None:
                extra["medication_report"] = {"code": normalize(row.get("medication")), "source": race["url"]}
            figures = str(row.get("last_three_beyer") or "").split()
            if prepost and figures:
                reported = [int(v) if re.fullmatch(r"-?\d+", v) else None for v in figures[:3]]
                known = []
                for value in reported:
                    if value is None:
                        break
                    known.append(value)
                if any(v is not None for v in reported):
                    extra["beyer_summary"] = {"race_date": day, "observed_at": observed, "figures": known,
                                              "reported_figures": reported, "source": row.get("supplemental_source")}
            for person in ("jockey", "trainer"):
                starts = to_int(row.get(person + "_2026_starts"))
                pct = to_int(row.get(person + "_2026_win_pct"))
                itm_pct = to_int(row.get(person + "_2026_itm_pct"))
                if prepost and starts and pct is not None and 0 <= pct <= 100:
                    extra[person + "_snapshot"] = {"race_date": day, "observed_at": observed, "starts": starts, "win_pct": pct,
                                                   "itm_pct": itm_pct if itm_pct is not None and 0 <= itm_pct <= 100 else None,
                                                   "source": row.get("supplemental_source")}
            weight = re.match(r"\d+", str(row.get("weight") or ""))
            runner = {"program_number": program, "horse_name": name, "horse_id": f"name:{norm_name(name)}",
                      "post_position": to_int(row.get("post_position")), "jockey_name": row.get("jockey"),
                      "trainer_name": row.get("trainer"),
                      "jockey_id": jockey_id(row.get("jockey")),
                      "trainer_id": trainer_id(row.get("trainer")),
                      "ml_odds": parse_odds(row.get("morning_line")),
                      "weight": int(weight[0]) if weight else None, "medication": normalize(row.get("medication")),
                      "claiming": to_int(row.get("claim_price")), "extra": extra,
                      **pedigree.get(program, {})}
            runners.append(runner)
        complete = all(r["program_number"] for r in runners) and len(runners) == race["starters"] and len({r["program_number"] for r in runners}) == len(runners)
        conditional = any(e.get("status") in ("also eligible", "main track only") for e in race["entries"])
        cards.append({"meet": {"meet_id": f"{track}-{day}", "track_id": track, "track_name": race["track"],
                               "race_date": day, "country": "USA"},
                      "races": [{"race_number": int(race["n"]), "post_time": post_time,
                                 "race_class": race.get("type"), "distance_desc": race.get("distance"),
                                 "distance_yards": parse_distance_yards(race.get("distance")),
                                 "surface": surface_code(race.get("surface")), "purse": to_int(race.get("purse")),
                                 "breed": "thoroughbred", "runners": runners}],
                      "complete": complete and not conditional})
    return cards

