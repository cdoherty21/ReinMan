"""Past-performance reader. Fixture lines are excerpts of real uploaded programs
(pdftotext -layout output), trimmed to a few horses."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np

from reinman import ingest, past_performances as ppr
from reinman.features import FEATURES, HistoryBuilder, featurize_race
from reinman.util import to_iso

PROGRAM = """\
                       Owner: Thomas Example                                                     51.7 Life: 14 1 2 1 $39,701
                 4-1   Silks: Red, White Sleeves                                                 2026: 2 0 0 0   $1,096
              Clm Prc  Trainer: D Bireta ( 76-3-8-4 ) 19.74%
              $5,000 Ragtime Prankster 51µ (L)                                       124
                       B.g.4 Maclean's Music - Silly Sister by Broken Vow - Bred in New York
  26Oct26 8 FL   ft       6f :22¿¹ :47¶¾ 1:14^½ 3g        Clm 5000nw2/L            52 57-53 4 4 5¹ 2«¬ 2«¬ 2«¬ Berrios J C 124 bL 3.57                   Liv Hs Rizz121«¬ Rgtime Prnkster124¶£ Luceo Non Uro114º¡ 4w trn, caught wire 6
  27May26 4 FL   ft       5K :23^¿ :47¶^ 1:06¶¾ 3g        Clm 11000 (11-9)nw2/L 25 50-57 6 2 2¶ 2¶ 2¹¡ 4»¡* Berrios J C 124 bL 11.23                     Rockstr Csnov121¹¡ On Morrp124¡ Wll Suited121¶¡ drftd in,bthrd foe1/8,DQ5th 6
Workout(s): 20 May 26 FL 5F ft 1:03.30b 9/13

      2
                       Owner: Dana Saul                                                          51.7 Life: 14 1 2 1 $39,701
                 2-1   Silks: Black, White Horse Emblem                                         2026: 2 0 0 0   $1,096
         Clm Prc       Trainer: Owner( 112-26-16-17 ) 52.68%
   White $5,000 Rocked Out 42µ (L)                                                              124
                       B.g.4 Outwork - Stonesintheroad by Bustin Stones - Bred in New York
  26Oct26 8 FL    ft       6f :22¿¹ :47¶¾ 1:14^½ 3g     Clm 5000nw2/L            44 77-39 6 1 1¶ 1«¬ 1«¬ 4¼¢ Worrie A S 124 bL *2.34            Liv Hs Rizz121«¬ Rgtime Prnkster124¶£ Luceo Non Uro114º¡ wdrftd in bthrd fo1/2 6
  17Sep25 5 FL    ft       6f :22¾¿ :46¹½ 1:11½» 3g     Clm 5000nw2/L               80-63 3 5 2«¬ 2¶¡ 2¼ 3¶^¡ Gutierrez R 121 bfL 5.70          Solo's Fury124¾¡ Nine N Four121¸ Rocked Out121¶¡ 5w1/2,3p trn,wkn 10
"""

POCKET = """\
            Glen Gaddy                                        Thomas Iannotti IV (26-2-3-4)          $5,000        ROWSIE EXPRESS                 2026: 9 2 0 2              $32,260 2025: 11 2 2 4 $104,670

   1 ROWSIE EXPRESS 46
                   Black, Red Square Framed "G", Red Stripes on Black Sleeves, Black Cap             Andy          31Oct26 Prx 7f ft 1:25º¸ SOC 5000/10000       48 8 4º¡   6¹¢ 6¶º 6¶º£ 19.10 Hernandez A      three wide, faltered
  4-1       6y.o. Ch.m MD by Golden Lad - Krista's Persona by Noonmark (MD)                                        18May26 Prx 1ÇÀ ft 1:45º½ Clm 5000nw2/6mx-c 42 5 1«¬     3¶ 2¡ 1¸       .80 Silvera R led, faded, came again
            Michael Moriarty                                     Ronald J. Dandy (29-2-6-5)          $5,000        COMPASS RISES                  2026: 7 1 2 2              $25,440 2025: 14 3 2 4 $55,271

   2 COMPASS RISES 55
                       Purple, Purple "DM" on Pink Shamrock, Pink Sleeves, Pink Cap                Silvestre       05Nov26 Prx 1m ft 1:41¸¼ Clm 5000nw1/6mx      55 2 3¡    4¹ 3º 2¸£ 2.50     Martinez F inside, gradual gain
"""


def _card(track, names, day="2026-11-24"):
    post = to_iso(datetime.now(timezone.utc) + timedelta(hours=3))
    return {"meet": {"meet_id": f"{track}-{day}", "track_id": track, "track_name": track, "race_date": day},
            "races": [{"race_number": 1, "post_time": post, "surface": "D", "distance_yards": 1320,
                       "is_cancelled": False, "changes": [],
                       "runners": [{"program_number": str(i + 1), "post_position": i + 1, "horse_id": f"{track}{i}",
                                    "horse_name": n, "ml_odds": 3.0 + i, "live_odds": None, "scratched": False}
                                   for i, n in enumerate(names)]}]}


def test_program_blocks_go_to_the_right_horse(db):
    # Ragtime Prankster also appears in Rocked Out's lines as a top finisher; the
    # reader must not hand Rocked Out's races to him.
    ingest.store_card(db, _card("FL", ["Ragtime Prankster", "Rocked Out", "Im Gunna"]), "test")
    out = ppr.apply(db, PROGRAM, ["FL-20261124-1"])
    assert out == {"horses": 2, "lines": 4}
    rag = json.loads(db.scalar("SELECT extra FROM runners WHERE program_number='1'"))["past_performances"]
    rocked = json.loads(db.scalar("SELECT extra FROM runners WHERE program_number='2'"))["past_performances"]
    assert [p["date"] for p in rag] == ["2026-10-26", "2026-05-27"]
    assert rag[0]["finish"] == 2 and rag[0]["speed_fig"] == 52 and rag[0]["field_size"] == 6
    assert rag[1]["trouble"] is True and rag[1]["distance_yards"] == 1210
    assert [(p["date"], p["finish"], p["favorite"]) for p in rocked] == [("2026-10-26", 4, True), ("2025-09-17", 3, False)]
    assert rocked[1]["speed_fig"] is None                      # blank figure stays blank
    assert db.scalar("SELECT extra FROM runners WHERE program_number='3'") is None
    # applying the same document again adds nothing
    assert ppr.apply(db, PROGRAM, ["FL-20261124-1"])["lines"] == 0


def test_equibase_pp_page(db):
    ingest.store_card(db, _card("PRX", ["Rowsie Express", "Compass Rises"]), "test")
    assert ppr.apply(db, POCKET, ["PRX-20261124-1"]) == {"horses": 2, "lines": 3}
    rows = {r["program_number"]: json.loads(r["extra"])["past_performances"]
            for r in db.query("SELECT program_number, extra FROM runners")}
    assert [(p["date"], p["call_positions"], p["odds"]) for p in rows["1"]] == [
        ("2026-10-31", [4, 6, 6, 6], 19.1), ("2026-05-18", [1, 3, 2, 1], 0.8)]
    assert rows["2"][0]["speed_fig"] == 55 and rows["2"][0]["comment"] == "inside, gradual gain"


def test_features_switch_on(db):
    ingest.store_card(db, _card("FL", ["Ragtime Prankster", "Rocked Out", "Im Gunna"]), "test")
    ppr.apply(db, PROGRAM, ["FL-20261124-1"])
    race = db.one("SELECT * FROM races")
    runners = db.query("SELECT * FROM runners ORDER BY program_number")
    X, _ = featurize_race(HistoryBuilder(), race, runners)
    col = {f: X[:, FEATURES.index(f)] for f in ("pp_last_speed", "pp_best_speed3", "pp_early_pos", "pp_last_trouble")}
    assert col["pp_last_speed"][0] == 52 and col["pp_last_speed"][1] == 44
    assert col["pp_best_speed3"][0] == 52
    assert np.isnan(col["pp_last_speed"][2])                   # no document -> unavailable, not guessed
    assert col["pp_last_trouble"][1] == 1.0                    # Rocked Out was bothered last out
    assert col["pp_early_pos"][1] < col["pp_early_pos"][0]     # Rocked Out runs closer to the pace


def test_imported_form_survives_sparse_card_refresh(db):
    card = _card("FL", ["Ragtime Prankster", "Rocked Out", "Im Gunna"])
    ingest.store_card(db, card, "test")
    ppr.apply(db, PROGRAM, ["FL-20261124-1"])
    card["races"][0]["runners"][0]["extra"] = {"workouts": [{"date": "2026-11-20"}]}
    ingest.store_card(db, card, "test")
    race = db.one("SELECT * FROM races")
    runners = db.query("SELECT * FROM runners ORDER BY program_number")
    X, evidence = featurize_race(HistoryBuilder(), race, runners)
    assert X[0, FEATURES.index("h_no_history")] == 0
    assert X[0, FEATURES.index("h_last_itm")] == 1
    assert evidence[0]["additional_pp_starts"] == 2
    assert len(json.loads(runners[0]["extra"])["past_performances"]) == 2
    assert X[2, FEATURES.index("h_no_history")] == 1
