"""Point-in-time feature construction.

All features for a race on date D are computed from information recorded *before*
D (prior results) plus what is known pre-race on D (entries, morning line, scratches,
conditions). Training and live prediction share this exact code path, which is the
main guard against leakage: ``HistoryBuilder`` is advanced one race-date at a time
and results for date D are only added after every race on D has been featurized.

Optional rich data (past-performance lines with speed/pace figures, call positions,
trouble notes, workouts) is used when a source provides it in ``runners.extra``;
otherwise those features are missing (NaN) and the factor is reported as unavailable.
"""
from __future__ import annotations

import bisect
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .db import Database
from .util import days_between, norm_name

OFF_TRACK = ("SLOPPY", "MUDDY", "SLOP", "MUD", "WET", "SEALED", "HEAVY", "YIELDING", "SOFT")
NAN = float("nan")

FEATURES = [
    # market (track handicapper's morning line — known before betting opens)
    "ml_logp", "has_ml",
    # horse form
    "h_starts_log", "h_win_rate", "h_itm_rate", "h_last_won", "h_last_itm", "h_last3_itm",
    "h_days_log", "h_layoff", "h_no_history", "h_surface_itm", "h_surface_starts_log",
    "h_dist_itm", "h_track_itm", "h_offtrack_itm", "h_first_surface",
    "class_change", "claim_change", "weight", "weight_change", "equip_change", "first_lasix",
    # connections
    "j_win", "j_win_30d", "j_track_win", "j_surface_win", "j_change_delta",
    "t_win", "t_win_30d", "t_track_win", "t_layoff_win", "t_debut_win", "jt_win",
    # pedigree
    "sire_surface_win", "sire_dist_win", "sire_offtrack_win",
    # post / bias
    "post_rel", "post_bias",
    # race context (constant within a race; useful to the tree model in interactions)
    "field_size", "off_track", "is_turf", "is_route",
    # rich past-performance data (NaN when the source does not provide it)
    "pp_last_speed", "pp_best_speed3", "pp_avg_speed3", "pp_speed_trend", "pp_early_pos",
    "pp_pace_adv", "pp_last_trouble", "pp_beaten_last", "pp_works_30d", "pp_best_work_pct",
]
RACE_CONSTANT = {"field_size", "off_track", "is_turf", "is_route"}
RICH = {f for f in FEATURES if f.startswith("pp_")}

# Empirical-Bayes pseudo-counts for shrinking rates toward the prior.
K_HORSE, K_PERSON, K_COMBO, K_SIRE, K_POST = 6.0, 25.0, 15.0, 40.0, 60.0


def _shrunk(w: float, n: float, prior: float, k: float) -> float:
    return (w + k * prior) / (n + k)


def _is_route(yards: int | None) -> bool:
    return bool(yards and yards >= 1760)


def _dist_band(yards: int | None) -> int:
    return int(round((yards or 0) / 220))


def _off(cond: str | None) -> bool:
    return bool(cond) and any(w in cond.upper() for w in OFF_TRACK)


@dataclass
class Start:
    date: str
    track: str
    surface: str | None
    yards: int | None
    off: bool
    field_size: int
    won: bool
    itm: bool
    purse: int | None
    claim: int | None
    weight: float | None
    equipment: str | None
    medication: str | None
    jockey: str | None
    trainer: str | None
    days_off: int | None  # layoff before this start


class Counter2:
    """Dated win/start counter supporting all-time and trailing-window rates."""

    def __init__(self):
        self.dates: list[str] = []
        self.wins: list[int] = []
        self.cum_w = 0
        self.cum_wins: list[int] = []

    def add(self, date: str, won: bool):
        self.dates.append(date)
        self.cum_w += int(won)
        self.cum_wins.append(self.cum_w)

    def window(self, date_from: str | None, date_to: str) -> tuple[int, int]:
        """(wins, starts) for date_from <= date < date_to."""
        j = bisect.bisect_left(self.dates, date_to)
        i = bisect.bisect_left(self.dates, date_from) if date_from else 0
        if j <= i:
            return 0, 0
        w = self.cum_wins[j - 1] - (self.cum_wins[i - 1] if i > 0 else 0)
        return w, j - i


@dataclass
class HistoryBuilder:
    starts: dict = field(default_factory=lambda: defaultdict(list))         # horse -> [Start]
    cnt: dict = field(default_factory=lambda: defaultdict(Counter2))        # key -> Counter2
    asof: str = "9999-12-31"   # rates only use races strictly before this date
    chart: dict = field(default_factory=dict)  # horse name key -> result-chart lines (charts.py)
    programs: dict = field(default_factory=lambda: defaultdict(list))

    def rate(self, key, prior: float, k: float, since: str | None = None) -> float:
        c = self.cnt.get(key)
        if c is None:
            return prior
        w, n = c.window(since, self.asof)
        return _shrunk(w, n, prior, k)

    def base_rate(self) -> float:
        w, n = self.cnt[("__all__",)].window(None, self.asof)
        return w / n if n >= 100 else 0.12

    def add_programs(self, race: dict, runners: list[dict]) -> None:
        for runner in runners:
            for line in _parse_extra(runner).get("past_performances") or []:
                if str(line.get("date", "")) < race["race_date"]:
                    self.programs[runner["horse_id"]].append(line)

    def add_race(self, race: dict, runners: list[dict], finish: dict[str, int]) -> None:
        """Add an official race. ``finish`` maps program_number -> official position."""
        live = [r for r in runners if not r["scratched"]]
        fs = len(live)
        if fs < 2:
            return
        d, trk, surf, yards = race["race_date"], race["track_id"], race.get("surface"), race.get("distance_yards")
        off = _off(race.get("track_condition"))
        route = _is_route(yards)
        for r in live:
            pos = finish.get(r["program_number"])
            if pos is None:
                continue
            won, itm = pos == 1, pos <= 3
            prev = self.starts[r["horse_id"]][-1] if self.starts[r["horse_id"]] else None
            self.starts[r["horse_id"]].append(Start(
                d, trk, surf, yards, off, fs, won, itm, race.get("purse"), r.get("claiming") or race.get("max_claim"),
                r.get("weight"), r.get("equipment"), r.get("medication"), r.get("jockey_id"), r.get("trainer_id"),
                days_between(prev.date, d) if prev else None))
            self.cnt[("__all__",)].add(d, won)
            j, t = r.get("jockey_id"), r.get("trainer_id")
            keys = []
            if j:
                keys += [("j", j), ("j_trk", j, trk), ("j_srf", j, surf)]
            if t:
                keys += [("t", t), ("t_trk", t, trk)]
                if prev is None:
                    keys.append(("t_debut", t))
                elif prev and days_between(prev.date, d) > 60:
                    keys.append(("t_layoff", t))
            if j and t:
                keys.append(("jt", j, t))
            if r.get("sire"):
                keys += [("s_srf", r["sire"], surf), ("s_rt", r["sire"], route)]
                if off:
                    keys.append(("s_off", r["sire"]))
            pg = _post_group(r.get("post_position"), fs)
            if pg is not None:
                keys.append(("post", trk, surf, route, pg))
                dl, cl = self._post_expect[(trk, surf, route, pg)]
                dl.append(d)
                cl.append((cl[-1] if cl else 0.0) + 1.0 / fs)
            for k in keys:
                self.cnt[k].add(d, won)

    def __post_init__(self):
        self._post_expect = defaultdict(lambda: ([], []))

    def post_expected(self, key) -> float:
        if key not in self._post_expect:
            return 0.0
        dl, cl = self._post_expect[key]
        j = bisect.bisect_left(dl, self.asof)
        return cl[j - 1] if j else 0.0


def _post_group(pp: int | None, fs: int) -> int | None:
    if not pp:
        return None
    rel = (pp - 1) / max(fs - 1, 1)
    return 0 if pp == 1 else 1 if rel < 0.34 else 2 if rel < 0.67 else 3


def _sub_days(date: str, days: int) -> str:
    from datetime import date as _d, timedelta
    return (_d.fromisoformat(date) - timedelta(days=days)).isoformat()


def _parse_extra(r: dict) -> dict:
    e = r.get("extra")
    if not e:
        return {}
    if isinstance(e, str):
        try:
            return json.loads(e)
        except ValueError:
            return {}
    return e


def _with_chart_lines(program_lines: list[dict], chart_lines: list[dict]) -> list[dict]:
    """Program past performances plus result-chart lines for the same horse. A race in
    both keeps the program line (it has the speed figure) and gains the chart's lengths."""
    by_key = {(p.get("date"), p.get("track")): dict(p) for p in program_lines}
    for c in chart_lines:
        key = (c.get("date"), c.get("track"))
        if key in by_key:
            for k in ("beaten_lengths", "call_lengths", "final_time", "surface", "field_size", "distance_yards", "finish", "condition"):
                if by_key[key].get(k) is None and c.get(k) is not None:
                    by_key[key][k] = c[k]
        else:
            by_key[key] = c
    return list(by_key.values())


def featurize_race(h: HistoryBuilder, race: dict, runners: list[dict]) -> tuple[np.ndarray, list[dict]]:
    """Return (X [n_live x F], evidence) for the non-scratched runners, in input order."""
    live = [r for r in runners if not r["scratched"]]
    n = len(live)
    d, trk, surf, yards = race["race_date"], race["track_id"], race.get("surface"), race.get("distance_yards")
    off, route = _off(race.get("track_condition")), _is_route(yards)
    d30, d365 = _sub_days(d, 30), _sub_days(d, 365)
    h.asof = d
    base = h.base_rate()
    ml = np.array([r.get("ml_odds") if r.get("ml_odds") is not None else np.nan for r in live], dtype=float)
    mlp = 1.0 / (ml + 1.0)
    has_ml = ~np.isnan(mlp)
    if has_ml.any():
        mlp = np.where(has_ml, mlp, np.nanmin(mlp) if has_ml.any() else 1.0 / n)
        mlp = mlp / mlp.sum()
    else:
        mlp = np.full(n, 1.0 / n)

    rows, evidence = [], []
    early_positions = []
    for i, r in enumerate(live):
        f = dict.fromkeys(FEATURES, NAN)
        ev: dict = {}
        f["ml_logp"] = math.log(mlp[i])
        f["has_ml"] = float(has_ml[i])
        starts = [s for s in h.starts.get(r["horse_id"], []) if s.date < d]
        ns = len(starts)
        f["h_starts_log"] = math.log1p(ns)
        f["h_no_history"] = float(ns == 0)
        if ns:
            last = starts[-1]
            wins = sum(s.won for s in starts)
            itm = sum(s.itm for s in starts)
            f["h_win_rate"] = _shrunk(wins, ns, base, K_HORSE)
            f["h_itm_rate"] = _shrunk(itm, ns, min(3 * base, 0.6), K_HORSE)
            f["h_last_won"], f["h_last_itm"] = float(last.won), float(last.itm)
            f["h_last3_itm"] = float(np.mean([s.itm for s in starts[-3:]]))
            days = days_between(last.date, d)
            f["h_days_log"], f["h_layoff"] = math.log1p(days), float(days > 60)
            srf = [s for s in starts if s.surface == surf]
            f["h_surface_itm"] = _shrunk(sum(s.itm for s in srf), len(srf), f["h_itm_rate"], 3)
            f["h_surface_starts_log"] = math.log1p(len(srf))
            f["h_first_surface"] = float(len(srf) == 0 and surf is not None)
            dst = [s for s in starts if abs(_dist_band(s.yards) - _dist_band(yards)) <= 1]
            f["h_dist_itm"] = _shrunk(sum(s.itm for s in dst), len(dst), f["h_itm_rate"], 3)
            tk = [s for s in starts if s.track == trk]
            f["h_track_itm"] = _shrunk(sum(s.itm for s in tk), len(tk), f["h_itm_rate"], 3)
            if off:
                ot = [s for s in starts if s.off]
                f["h_offtrack_itm"] = _shrunk(sum(s.itm for s in ot), len(ot), f["h_itm_rate"], 3)
            today_lvl = r.get("claiming") or race.get("max_claim") or race.get("purse")
            last_lvl = last.claim or last.purse
            if today_lvl and last_lvl:
                f["class_change"] = math.log(today_lvl / last_lvl)
            if race.get("purse") and last.purse:
                f["class_change"] = math.log(race["purse"] / last.purse)
            if (r.get("claiming") or race.get("max_claim")) and last.claim:
                f["claim_change"] = math.log((r.get("claiming") or race.get("max_claim")) / last.claim)
            if r.get("weight") and last.weight:
                f["weight_change"] = r["weight"] - last.weight
            f["equip_change"] = float((r.get("equipment") or "") != (last.equipment or ""))
            f["first_lasix"] = float("L" in (r.get("medication") or "").upper() and
                                     "L" not in (last.medication or "").upper())
            if last.jockey and r.get("jockey_id") and last.jockey != r["jockey_id"]:
                f["j_change_delta"] = (h.rate(("j", r["jockey_id"]), base, K_PERSON, d365) -
                                       h.rate(("j", last.jockey), base, K_PERSON, d365))
            else:
                f["j_change_delta"] = 0.0
            ev.update(starts=ns, wins=wins, itm=itm, days=days, last_itm=last.itm, last_won=last.won)
        if r.get("weight"):
            f["weight"] = float(r["weight"])
        j, t = r.get("jockey_id"), r.get("trainer_id")
        if j:
            f["j_win"] = h.rate(("j", j), base, K_PERSON, d365)
            f["j_win_30d"] = h.rate(("j", j), base, K_PERSON / 2, d30)
            f["j_track_win"] = h.rate(("j_trk", j, trk), f["j_win"], K_PERSON)
            f["j_surface_win"] = h.rate(("j_srf", j, surf), f["j_win"], K_PERSON)
        if t:
            f["t_win"] = h.rate(("t", t), base, K_PERSON, d365)
            f["t_win_30d"] = h.rate(("t", t), base, K_PERSON / 2, d30)
            f["t_track_win"] = h.rate(("t_trk", t, trk), f["t_win"], K_PERSON)
            f["t_layoff_win"] = h.rate(("t_layoff", t), f["t_win"], K_PERSON)
            f["t_debut_win"] = h.rate(("t_debut", t), f["t_win"], K_PERSON)
        if j and t:
            f["jt_win"] = h.rate(("jt", j, t), (f["j_win"] + f["t_win"]) / 2, K_COMBO)
        if r.get("sire"):
            f["sire_surface_win"] = h.rate(("s_srf", r["sire"], surf), base, K_SIRE)
            f["sire_dist_win"] = h.rate(("s_rt", r["sire"], route), base, K_SIRE)
            if off:
                f["sire_offtrack_win"] = h.rate(("s_off", r["sire"]), base, K_SIRE)
        pp = r.get("post_position")
        if pp:
            f["post_rel"] = (pp - 1) / max(n - 1, 1)
            pg = _post_group(pp, n)
            c = h.cnt.get(("post", trk, surf, route, pg))
            if c is not None:
                w, cnt = c.window(None, d)
                exp = h.post_expected((trk, surf, route, pg))
                # actual wins / field-size-expected wins for this post group, shrunk toward 1.0
                f["post_bias"] = (w + K_POST * 0.12) / (exp + K_POST * 0.12)
        f["field_size"], f["off_track"] = float(n), float(off)
        f["is_turf"], f["is_route"] = float(surf == "T"), float(route)

        # ---- optional rich data ----
        ex = _parse_extra(r)
        program_lines = h.programs.get(r["horse_id"], []) + (ex.get("past_performances") or [])
        pps = sorted([p for p in _with_chart_lines(program_lines,
                                                   h.chart.get(norm_name(r.get("horse_name")), []))
                      if str(p.get("date", "")) < d],
                     key=lambda p: p["date"], reverse=True)
        if pps:
            # Imported programs and standalone charts contain real prior starts even
            # when the same races were never imported as complete cards.
            tracked_dates = {s.date for s in starts}
            additional = [p for p in pps if p["date"] not in tracked_dates
                          and isinstance(p.get("finish"), (int, float)) and p["finish"] > 0]
            # One start per horse per day, preferring the merged program/chart line.
            additional = list({p["date"]: p for p in additional}.values())
            if additional:
                form = [(s.date, s.won, s.itm) for s in starts]
                form += [(p["date"], p["finish"] == 1, p["finish"] <= 3) for p in additional]
                form.sort()
                count = len(form)
                f["h_starts_log"] = math.log1p(count)
                f["h_no_history"] = 0.0
                f["h_win_rate"] = _shrunk(sum(x[1] for x in form), count, base, K_HORSE)
                f["h_itm_rate"] = _shrunk(sum(x[2] for x in form), count, min(3 * base, 0.6), K_HORSE)
                f["h_last_won"], f["h_last_itm"] = float(form[-1][1]), float(form[-1][2])
                f["h_last3_itm"] = float(np.mean([x[2] for x in form[-3:]]))
                days = days_between(form[-1][0], d)
                f["h_days_log"], f["h_layoff"] = math.log1p(days), float(days > 60)
                # Fill contextual form using only records with known context.
                for name, matches in (
                    ("h_surface_itm", lambda p: surf is not None and p.get("surface") == surf),
                    ("h_dist_itm", lambda p: yards and p.get("distance_yards") and
                     abs(_dist_band(p["distance_yards"]) - _dist_band(yards)) <= 1),
                    ("h_track_itm", lambda p: p.get("track") == trk),
                    ("h_offtrack_itm", lambda p: off and _off(p.get("condition"))),
                ):
                    histories = {
                        "h_surface_itm": [s for s in starts if surf is not None and s.surface == surf],
                        "h_dist_itm": [s for s in starts if yards and s.yards and abs(_dist_band(s.yards) - _dist_band(yards)) <= 1],
                        "h_track_itm": [s for s in starts if s.track == trk],
                        "h_offtrack_itm": [s for s in starts if off and s.off],
                    }[name]
                    matching = [p for p in additional if matches(p)]
                    if matching or histories:
                        flags = [s.itm for s in histories] + [p["finish"] <= 3 for p in matching]
                        f[name] = _shrunk(sum(flags), len(flags), f["h_itm_rate"], 3)
                ev["additional_pp_starts"] = len(additional)
            figs = [p.get("speed_fig") for p in pps if p.get("speed_fig") is not None]
            if figs:
                f["pp_last_speed"] = float(figs[0])
                f["pp_best_speed3"] = float(max(figs[:3]))
                f["pp_avg_speed3"] = float(np.mean(figs[:3]))
                if len(figs) >= 3:
                    f["pp_speed_trend"] = float(figs[0] - np.mean(figs[1:3]))
            calls = [p["call_positions"][0] / max(p.get("field_size") or n, 1)
                     for p in pps[:3] if p.get("call_positions") and p.get("field_size")]
            if calls:
                f["pp_early_pos"] = float(np.mean(calls))
            f["pp_last_trouble"] = float(bool(pps[0].get("trouble")))
            if pps[0].get("beaten_lengths") is not None:
                f["pp_beaten_last"] = float(pps[0]["beaten_lengths"])
            ev["pp_lines"] = len(pps)
        works = [w for w in ex.get("workouts") or [] if _sub_days(d, 30) <= str(w.get("date", "")) < d]
        if ex.get("workouts") is not None:
            f["pp_works_30d"] = float(len(works))
            ranked = [w["rank"] / w["of"] for w in works if w.get("rank") and w.get("of")]
            if ranked:
                f["pp_best_work_pct"] = float(min(ranked))
        early_positions.append(f["pp_early_pos"])
        rows.append(f)
        evidence.append(ev)

    # pace advantage: early speed relative to the rest of the field (only with call data)
    ep = np.array(early_positions, dtype=float)
    if np.isfinite(ep).sum() >= max(3, n // 2):
        n_speed = np.nansum(ep < 0.34)
        for i, f in enumerate(rows):
            if np.isfinite(ep[i]):
                if ep[i] < 0.34:   # speed horse: best alone on the lead, worse in a speed duel
                    f["pp_pace_adv"] = 1.0 / max(n_speed, 1)
                else:              # stalkers/closers benefit when several speed horses compete
                    f["pp_pace_adv"] = 0.1 * (n_speed - 2)
    X = np.array([[f[k] for k in FEATURES] for f in rows], dtype=float)
    return X, evidence


def projected_pace(X: np.ndarray) -> dict | None:
    """Projected pace scenario from early-position data; None when unavailable."""
    i = FEATURES.index("pp_early_pos")
    ep = X[:, i]
    if np.isfinite(ep).sum() < max(3, len(ep) // 2):
        return None
    n_speed = int(np.nansum(ep < 0.34))
    scenario = "Hot" if n_speed >= 3 else "Honest" if n_speed == 2 else "Lone speed" if n_speed == 1 else "Slow"
    return {"scenario": scenario, "early_speed_count": n_speed}


# ---------------------------------------------------------------- dataset assembly
@dataclass
class RaceSample:
    race_id: str
    race_date: str
    track_id: str
    race: dict
    runners: list[dict]           # live runners, aligned with X rows
    X: np.ndarray
    evidence: list[dict]
    finish: np.ndarray | None     # official positions (NaN if unknown), aligned with runners
    payoffs: list[dict] = field(default_factory=list)


def load_races(db: Database, start: str | None = None, end: str | None = None,
               official_only: bool = False) -> tuple[dict, dict, dict, dict]:
    where, params = ["is_cancelled=0"], []
    if start:
        where.append("race_date>=?")
        params.append(start)
    if end:
        where.append("race_date<=?")
        params.append(end)
    if official_only:
        where.append("status='official'")
    races = {r["race_id"]: r for r in db.query(
        f"SELECT * FROM races WHERE {' AND '.join(where)} ORDER BY race_date, track_id, race_number", params)}
    runners = defaultdict(list)
    fin = defaultdict(dict)
    pay = defaultdict(list)
    if not races:
        return races, runners, fin, pay
    rng = [start or "0000-00-00", end or "9999-99-99"]
    for r in db.query("SELECT ru.* FROM runners ru JOIN races ra ON ra.race_id=ru.race_id "
                      "WHERE ra.race_date BETWEEN ? AND ? ORDER BY ru.race_id, ru.post_position, ru.program_number", rng):
        if r["race_id"] in races:
            runners[r["race_id"]].append(r)
    for r in db.query("SELECT rf.* FROM result_finishers rf JOIN races ra ON ra.race_id=rf.race_id "
                      "WHERE ra.race_date BETWEEN ? AND ?", rng):
        if r["program_number"]:
            prev = fin[r["race_id"]].get(r["program_number"])
            fin[r["race_id"]][r["program_number"]] = min(prev, r["finish_position"]) if prev else r["finish_position"]
    for r in db.query("SELECT rp.* FROM result_payoffs rp JOIN races ra ON ra.race_id=rp.race_id "
                      "WHERE ra.race_date BETWEEN ? AND ?", rng):
        pay[r["race_id"]].append(r)
    return races, runners, fin, pay


def _chart_lines(db: Database) -> dict:
    from .charts import load_lines
    return load_lines(db)


def build_history(db: Database, before: str) -> HistoryBuilder:
    h = HistoryBuilder(chart=_chart_lines(db))
    races, runners, fin, _ = load_races(db, end=_sub_days(before, 1), official_only=True)
    for rid, race in races.items():
        h.add_programs(race, runners[rid])
        if fin.get(rid):
            h.add_race(race, runners[rid], fin[rid])
    return h


def build_dataset(db: Database, start: str | None = None, end: str | None = None,
                  min_field: int = 3) -> list[RaceSample]:
    """Chronological samples for official races in [start, end], features point-in-time."""
    h = HistoryBuilder(chart=_chart_lines(db))
    races, runners, fin, pay = load_races(db, end=end, official_only=True)
    by_date = defaultdict(list)
    for rid, race in races.items():
        by_date[race["race_date"]].append(rid)
    samples: list[RaceSample] = []
    for d in sorted(by_date):
        todays = by_date[d]
        if start is None or d >= start:
            for rid in todays:
                live = [r for r in runners[rid] if not r["scratched"]]
                if len(live) < min_field or not fin.get(rid):
                    continue
                X, evidence = featurize_race(h, races[rid], runners[rid])
                finish = np.array([fin[rid].get(r["program_number"], np.nan) for r in live], dtype=float)
                if not np.any(finish == 1):
                    continue
                samples.append(RaceSample(rid, d, races[rid]["track_id"], races[rid], live, X, evidence,
                                          finish, pay.get(rid, [])))
        for rid in todays:  # only now add date d to history
            h.add_programs(races[rid], runners[rid])
            if fin.get(rid):
                h.add_race(races[rid], runners[rid], fin[rid])
    return samples


def data_availability(X: np.ndarray) -> dict[str, bool]:
    """Which factor groups had data for this race."""
    def any_col(names):
        idx = [FEATURES.index(n) for n in names]
        return bool(np.isfinite(X[:, idx]).any())
    return {
        "morning_line": bool(np.nanmax(X[:, FEATURES.index("has_ml")]) > 0) if len(X) else False,
        "horse_history": bool((X[:, FEATURES.index("h_no_history")] == 0).any()) if len(X) else False,
        "jockey_trainer": any_col(["j_win", "t_win"]),
        "pedigree": any_col(["sire_surface_win"]),
        "post_bias": any_col(["post_bias"]),
        "speed_figures": any_col(["pp_last_speed"]),
        "pace_figures": any_col(["pp_early_pos"]),
        "trouble_notes": any_col(["pp_last_trouble"]),
        "workouts": any_col(["pp_works_30d"]),
    }
