from reinman import stables


def test_identity_requires_sire_and_foaling_year():
    horse = {"labels": {"en": {"value": "Example"}}, "claims": {"P569": [
        {"mainsnak": {"datavalue": {"value": {"time": "+2020-01-01T00:00:00Z"}}}}]}}
    sire = {"labels": {"en": {"value": "Sire"}}}
    assert stables.matches(horse, "Example", 2020, "Sire", sire)
    assert not stables.matches(horse, "Example", 2021, "Sire", sire)
    assert not stables.matches(horse, "Example", 2020, "Other", sire)


def test_enrichment_stops_after_source_failure(db, monkeypatch):
    db.execute("INSERT INTO races(race_id,track_id,race_date,race_number,source) VALUES ('r','T','2026-11-26',1,'test')")
    db.execute("INSERT INTO runners(race_id,program_number,horse_id,horse_name,sire,extra) VALUES ('r','1','horse','Horse','Sire',?)",
               ['{"horse_identity":{"age_sex":"4/G"}}'])
    before = db.rows_written
    def unavailable(*args):
        raise OSError("temporarily unavailable")
    monkeypatch.setattr(stables, "enrich", unavailable)
    assert stables.refresh(db)["attempted"] == 0
    assert db.rows_written == before
