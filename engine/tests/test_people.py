from reinman import features, ingest
from reinman.cli import canonicalize_people
from reinman.util import jockey_id, person_key


def test_one_key_however_a_source_writes_the_name():
    same = ["Hernandez, Andy", "Andy Hernandez", "A. Hernandez", "Hernandez A", "HERNANDEZ, A.", "Andy  Hernandez Jr."]
    assert {person_key(n) for n in same} == {"HERNANDEZ A"}
    assert person_key("Berrios J C") == person_key("Juan Carlos Berrios") == "BERRIOS J"
    assert person_key("Soto- Martinez, Elliott") == person_key("Elliott Soto-Martinez") == "MARTINEZ E"
    assert person_key("De La Cruz, Fernando") == person_key("Fernando De La Cruz") == "DE LA CRUZ F"
    assert person_key("Linder, Jr., Louis") == person_key("Louis Linder") == "LINDER L"
    assert person_key("Gonzalez, Melvis") != person_key("Gonzalez, Silvestre")
    assert person_key(None) == "" and jockey_id("") is None


def _card(jockey, odds=None):
    return {"meet": {"meet_id": "PRX-2026-11-25", "track_id": "PRX", "race_date": "2026-11-25"},
            "races": [{"race_number": 1, "post_time": "2026-11-25T17:05:00Z", "is_cancelled": False, "changes": [],
                       "runners": [{"program_number": "1", "horse_id": "name:A", "horse_name": "A",
                                    "jockey_id": f"jn:{jockey.upper()}", "jockey_name": jockey,
                                    "trainer_name": "Guerrero, J.", "scratched": False},
                                   {"program_number": "2", "horse_id": "name:B", "horse_name": "B",
                                    "jockey_name": "Chuan, Martin", "scratched": False}]}]}


def test_a_respelled_jockey_is_not_a_change_but_a_new_one_is(db):
    assert ingest.store_card(db, _card("Hernandez, Andy"), "a") == {"PRX-20261125-1": ["initial"]}
    assert ingest.store_card(db, _card("Andy Hernandez"), "b") == {}
    assert db.scalar("SELECT jockey_id FROM runners WHERE program_number='1'") == "jn:HERNANDEZ A"
    assert db.scalar("SELECT trainer_id FROM runners WHERE program_number='1'") == "tn:GUERRERO J"
    assert ingest.store_card(db, _card("Ocasio, Luis"), "b") == {"PRX-20261125-1": ["jockey"]}


def test_repair_rewrites_old_ids_and_features_match_old_rows(db):
    ingest.store_card(db, _card("Hernandez, Andy"), "a")
    db.execute("UPDATE runners SET jockey_id='jn:HERNANDEZ ANDY', trainer_id='tn:GUERRERO J' WHERE program_number='1'")
    runners = db.query("SELECT * FROM runners")
    h = features.HistoryBuilder()
    race = db.one("SELECT * FROM races")
    h.add_race(race, runners, {"1": 1, "2": 2})
    assert ("j", "jn:HERNANDEZ A") in h.cnt and ("j", "jn:HERNANDEZ ANDY") not in h.cnt
    out = canonicalize_people(db)
    assert out["jockey_names_updated"] == 1 and out["trainer_names_updated"] == 0
    assert db.scalar("SELECT jockey_id FROM runners WHERE program_number='1'") == "jn:HERNANDEZ A"
    assert canonicalize_people(db)["jockey_names_updated"] == 0
