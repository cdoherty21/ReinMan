"""Document scanning. The program text below is a hand-written TEST fixture."""
import json
from datetime import datetime, timedelta, timezone

from reinman import documents, ingest
from reinman.util import to_iso

PROGRAM = """FINGER LAKES  Race 3   November 26, 2026
1  Morning Thunder (L)   Jockey: A. Rider   Trainer: B. Trainer
   Workouts: Nov15 FL 4f ft :49.20 B 12/31   Nov8 FL 3f ft :36.40 H 2/9
   Last out 3rd, wide trip
2  Silver Comet   Jockey: C. Rider
   Workouts: Nov18 FL 5f gd 1:01.40 B 4/11
3  Quiet Harbor  SCRATCHED - vet
4  Lucky Seven Stars
   Bumped at the start last time.
$2 Exacta (1-2) Paid $45.60
"""


def _card():
    post = to_iso(datetime.now(timezone.utc) + timedelta(hours=3))
    names = ["Morning Thunder", "Silver Comet", "Quiet Harbor", "Lucky Seven Stars", "Ab"]
    return {"meet": {"meet_id": "FL-2026-11-26", "track_id": "FL", "track_name": "Finger Lakes",
                     "race_date": "2026-11-26"},
            "races": [{"race_number": 3, "post_time": post, "surface": "D", "distance_yards": 1320,
                       "is_cancelled": False, "changes": [],
                       "runners": [{"program_number": str(i + 1), "post_position": i + 1,
                                    "horse_id": f"h{i}", "horse_name": n, "ml_odds": 3.0 + i,
                                    "live_odds": None, "scratched": False} for i, n in enumerate(names)]}]}


def _upload(db, text, **kw):
    doc_id = db.insert("documents", {"filename": kw.get("filename", "program.pdf"), "chars": len(text),
                                     "race_date": kw.get("race_date"), "track_id": kw.get("track_id"),
                                     "research_only": kw.get("research_only", 0)})
    for i in range(0, len(text), 50):
        db.insert("document_chunks", {"document_id": doc_id, "seq": i // 50, "text": text[i:i + 50]})
    return doc_id


def test_program_is_linked_and_mined(db):
    ingest.store_card(db, _card(), "test")
    doc_id = _upload(db, PROGRAM, race_date="2026-11-26")
    assert documents.process_pending(db) == {"processed": 1, "errors": 0}
    doc = db.one("SELECT * FROM documents WHERE id=?", [doc_id])
    s = json.loads(doc["summary"])
    assert doc["status"] == "processed" and s["races_linked"] == ["FL-20261126-3"]
    assert s["horses_matched"] == 4          # "Ab" is too short to match safely
    assert s["workouts"] == 3 and s["workouts_applied_to_cards"] == 3
    assert s["payouts"] == 1 and s["scratch_mentions"] == 1
    extra = json.loads(db.scalar("SELECT extra FROM runners WHERE race_id='FL-20261126-3' AND program_number='1'"))
    w = sorted(extra["workouts"], key=lambda x: x["date"])
    assert [(x["date"], x["distance_yards"], x["rank"], x["of"]) for x in w] == [
        ("2026-11-08", 660, 2, 9), ("2026-11-15", 880, 12, 31)]
    passage = db.scalar("SELECT text FROM document_notes WHERE kind='passage' AND program_number='4'")
    assert "Bumped at the start" in passage and "Silver Comet" not in passage
    pay = json.loads(db.scalar("SELECT data FROM document_notes WHERE kind='payout'"))
    assert pay["wager_type"] == "EXACTA" and pay["payoff_amount"] == 45.6 and pay["winning_numbers"] == "1-2"
    # re-running does not reprocess or duplicate
    assert documents.process_pending(db) == {"processed": 0, "errors": 0}


def test_legacy_research_flag_does_not_exclude_workouts(db):
    ingest.store_card(db, _card(), "test")
    _upload(db, PROGRAM, race_date="2026-11-26", research_only=1)
    documents.process_pending(db)
    assert len(json.loads(db.scalar("SELECT extra FROM runners WHERE program_number='1'"))["workouts"]) == 2
    assert db.scalar("SELECT COUNT(*) FROM document_notes WHERE kind='workout'") == 3


def test_unrelated_text_links_nothing(db):
    ingest.store_card(db, _card(), "test")
    _upload(db, "Morning thunder is expected over the lake. Nothing else here.")
    documents.process_pending(db)
    s = json.loads(db.scalar("SELECT summary FROM documents"))
    assert s["races_linked"] == [] and s["horses_matched"] == 0


def test_canonical_json_upload_imports_cards(db):
    _upload(db, json.dumps({"cards": [_card()], "results": []}), filename="cards.json")
    documents.process_pending(db)
    assert db.scalar("SELECT COUNT(*) FROM runners") == 5
    assert json.loads(db.scalar("SELECT summary FROM documents"))["canonical_import"] is True


def test_dates_found_in_text():
    assert documents.dates_in("Card for 11/26/2026 and November 27, 2026 (2026-11-28)") == {
        "2026-11-26", "2026-11-27", "2026-11-28"}

def test_pocket_pp_waits_for_the_exact_card(db):
    doc_id = _upload(db, "FL-11/26/2026-3\n" + PROGRAM)
    assert documents.process_pending(db) == {"processed": 0, "errors": 0}
    assert db.scalar("SELECT status FROM documents WHERE id=?", [doc_id]) == "waiting"
    wrong = _card()
    wrong["races"][0]["race_number"] = 4
    ingest.store_card(db, wrong, "test")
    assert documents.process_pending(db) == {"processed": 0, "errors": 0}
    ingest.store_card(db, _card(), "test")
    assert documents.process_pending(db) == {"processed": 1, "errors": 0}
    summary = json.loads(db.scalar("SELECT summary FROM documents WHERE id=?", [doc_id]))
    assert summary["races_linked"] == ["FL-20261126-3"]
