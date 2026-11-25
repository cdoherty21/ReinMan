import json
from reinman import documents, historical
from reinman.util import norm_name

CSV = """Track,RaceDate,RaceNumber,HorseName,WinningPostPosition,WinnersTrainer,WinnersJockey,WinnersSire,Surface,FieldSize
KEE,2024-04-26,1,Test Horse,3,Smith John,Jones Jane,Test Sire,Turf,10
KEE,2024-04-26,2,Another Horse,1,Smith John,Jones Jane,Test Sire,Dirt,8
KEE,invalid,3,Invalid,4,Smith John,Jones Jane,Test Sire,Turf,6
"""


def test_rescan_winner_archive(db):
    doc = db.insert("documents", {"filename": "keeneland_races_2024.csv", "chars": len(CSV),
                                   "status": "processed", "summary": json.dumps({"message": "No race card could be identified."})})
    db.insert("document_chunks", {"document_id": doc, "seq": 0, "text": CSV})
    assert documents.ready_count(db) == 1
    assert documents.process_pending(db) == {"processed": 1, "errors": 0}
    summary = json.loads(db.scalar("SELECT summary FROM documents WHERE id=?", [doc]))
    assert summary["winner_records"] == 2 and summary["rejected_rows"] == 1
    assert documents.ready_count(db) == 0
    assert db.scalar("SELECT COUNT(*) FROM races") == 0
    assert db.scalar("SELECT COUNT(*) FROM historical_winners") == 2
    documents.process_queue(db, rescan=True)
    assert db.scalar("SELECT COUNT(*) FROM historical_winners") == 2
    h = historical.load(db)
    assert h.count("KEE", "horse", norm_name("Test Horse"), "2024-04-26") == 0
    assert h.count("KEE", "horse", norm_name("Test Horse"), "2024-04-27") == 1
    assert h.count("KEE", "sire", norm_name("Test Sire"), "2024-04-27", "T") == 1


def test_winner_archive_features_use_prior_dates(db):
    import math
    from reinman.features import FEATURES, HistoryBuilder, featurize_race
    import csv
    import io
    historical.apply(db, [list(csv.reader(io.StringIO(CSV)))],
                     {"id": 1, "filename": "keeneland.csv"})
    h = HistoryBuilder(winner_archive=historical.load(db))
    race = {"race_date": "2024-04-27", "track_id": "KEE", "surface": "T"}
    runners = [{"horse_id": "test", "horse_name": "Test Horse", "scratched": False,
                "trainer_name": "Smith John", "jockey_name": "Jones Jane", "sire": "Test Sire"}]
    X, evidence = featurize_race(h, race, runners)
    assert X[0, FEATURES.index("archive_horse_wins_log")] == math.log(2)
    assert evidence[0]["archive_wins"]["trainer"] == 2
    assert X[0, FEATURES.index("h_no_history")] == 1
    race["race_date"] = "2024-04-26"
    X, evidence = featurize_race(h, race, runners)
    assert math.isnan(X[0, FEATURES.index("archive_horse_wins_log")])
    assert "archive_wins" not in evidence[0]
