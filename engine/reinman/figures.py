"""Rein Man's own speed and pace figures, computed from result-chart running lines.

Inputs per line (charts.py): the race's final time, distance and surface, the horse's
lengths behind the leader at each point of call and at the finish, and the race's
fractional times. Program past-performance lines carry no times, so they get no figure;
the source's printed figure (``speed_fig``) is kept separately.

Scale (fixed, so a figure never depends on later data):

* A horse's time is the winner's time plus 0.2 s per length beaten (the usual
  convention, one length ≈ one fifth of a second).
* Its speed in yards per second is compared with a par curve for the distance:
  6 furlongs in 1:11.00 on dirt is 80, and longer races are slower per yard
  (par speed falls 2.0 yd/s per unit of log distance). Turf and synthetic pars are
  adjusted for their faster or slower going.
* 38 points per yard per second: about 2 points per length at six furlongs and 1.4 at
  a mile, close to the familiar figure scales.

Track variant: a fast or slow surface lifts or sinks every time on the card. The
winners of a track-day-surface (four or more charted races) are compared with the
long-run winner level; half of the difference, shrunk toward zero for small cards, is
taken out. Only that day's own races are used, so a figure for a race is fixed once its
card is charted and never uses anything later.

Pace figures use the same scale at the half-mile (880 yards): **early pace** is the
horse's time to the half (the half-mile fraction plus its lengths behind at that call),
and **late pace** is its speed from the half to the wire.
"""
from __future__ import annotations

import math
from collections import defaultdict
from statistics import median

SEC_PER_LENGTH = 0.2
POINTS_PER_YPS = 38.0
PAR_6F = 1320 / 71.0                     # yd/s for 6f in 1:11.00 = 80
PAR_SLOPE = 2.0                          # par speed drop per unit of ln(distance / 6f)
SURFACE_ADJ = {"D": 0.0, "T": 0.20, "A": 0.05, "S": 0.05}
BASE = 80.0
WINNER_LEVEL = 72.0                      # long-run winner level the variant is measured against
VARIANT_MIN_RACES = 4
HALF = 880
PAR_HALF = {"sprint": 46.6, "route": 48.4}   # seconds to the half for the same 80 level


def par_speed(yards: float, surface: str | None) -> float:
    return PAR_6F - PAR_SLOPE * math.log(yards / 1320) + SURFACE_ADJ.get(surface or "D", 0.0)


def _route(yards: float) -> str:
    return "route" if yards >= 1540 else "sprint"


def raw_speed(line: dict) -> float | None:
    """Figure before the track variant; None when the line lacks the inputs."""
    t, yards, beaten = line.get("final_time"), line.get("distance_yards"), line.get("beaten_lengths")
    if not t or not yards or beaten is None or t <= 0 or yards < 600:
        return None
    seconds = t + SEC_PER_LENGTH * max(0.0, float(beaten))
    return BASE + (yards / seconds - par_speed(yards, line.get("surface"))) * POINTS_PER_YPS


def pace(line: dict) -> tuple[float | None, float | None]:
    """(early, late) pace figures at the half mile; None where inputs are missing."""
    fr, calls, yards = line.get("fractions") or [], line.get("call_lengths") or [], line.get("distance_yards")
    if len(fr) < 2 or len(calls) < 2 or not yards or yards <= HALF + 200:
        return None, None
    kind = _route(yards)
    half = fr[1] + SEC_PER_LENGTH * max(0.0, float(calls[1] or 0))
    early = BASE + (PAR_HALF[kind] - half) * (POINTS_PER_YPS * HALF / PAR_HALF[kind] ** 2)
    late = None
    t, beaten = line.get("final_time"), line.get("beaten_lengths")
    if t and beaten is not None:
        finish = t + SEC_PER_LENGTH * max(0.0, float(beaten))
        if finish > half:
            par_total = yards / par_speed(yards, line.get("surface"))
            par_late = (yards - HALF) / (par_total - PAR_HALF[kind])
            late = BASE + ((yards - HALF) / (finish - half) - par_late) * POINTS_PER_YPS
    return early, late


def variants(lines: list[dict]) -> dict[tuple, float]:
    """(track, date, surface) -> variant to subtract, from that day's winners."""
    winners = defaultdict(dict)
    for ln in lines:
        if ln.get("finish") == 1 and ln.get("track") and ln.get("date"):
            fig = raw_speed(ln)
            if fig is not None:
                winners[(ln["track"], ln["date"], ln.get("surface"))][ln.get("race_number")] = fig
    out = {}
    for key, figs in winners.items():
        n = len(figs)
        if n >= VARIANT_MIN_RACES:
            out[key] = 0.5 * (median(figs.values()) - WINNER_LEVEL) * n / (n + 4)
    return out


def annotate(lines_by_horse: dict[str, list[dict]]) -> int:
    """Add ``rm_speed``, ``rm_early`` and ``rm_late`` to every line that allows it (in
    place). Returns how many lines got a speed figure."""
    every = [ln for lines in lines_by_horse.values() for ln in lines]
    var = variants(every)
    n = 0
    for ln in every:
        raw = raw_speed(ln)
        if raw is not None:
            ln["rm_speed"] = round(raw - var.get((ln.get("track"), ln.get("date"), ln.get("surface")), 0.0), 1)
            n += 1
        early, late = pace(ln)
        if early is not None:
            ln["rm_early"] = round(early, 1)
        if late is not None:
            ln["rm_late"] = round(late, 1)
    return n


FEATURES = ["rm_speed_last", "rm_speed_best3", "rm_speed_avg3", "rm_speed_trend",
            "rm_early_avg3", "rm_late_avg3"]


def features(lines: list[dict]) -> tuple[dict, dict]:
    """Model inputs and evidence from a horse's prior lines (newest first)."""
    f = dict.fromkeys(FEATURES, float("nan"))
    figs = [ln["rm_speed"] for ln in lines if ln.get("rm_speed") is not None]
    ev = {}
    if figs:
        f["rm_speed_last"] = figs[0]
        f["rm_speed_best3"] = max(figs[:3])
        f["rm_speed_avg3"] = sum(figs[:3]) / len(figs[:3])
        if len(figs) >= 3:
            f["rm_speed_trend"] = figs[0] - (figs[1] + figs[2]) / 2
        ev["rm_figures"] = figs[:5]
    for key, name in (("rm_early", "rm_early_avg3"), ("rm_late", "rm_late_avg3")):
        vals = [ln[key] for ln in lines if ln.get(key) is not None][:3]
        if vals:
            f[name] = sum(vals) / len(vals)
            ev[key] = [round(v) for v in vals]
    return f, ev
