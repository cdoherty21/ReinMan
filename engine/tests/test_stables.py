from reinman import stables


def test_identity_requires_sire_and_foaling_year():
    horse = {"labels": {"en": {"value": "Example"}}, "claims": {"P569": [
        {"mainsnak": {"datavalue": {"value": {"time": "+2020-01-01T00:00:00Z"}}}}]}}
    sire = {"labels": {"en": {"value": "Sire"}}}
    assert stables.matches(horse, "Example", 2020, "Sire", sire)
    assert not stables.matches(horse, "Example", 2021, "Sire", sire)
    assert not stables.matches(horse, "Example", 2020, "Other", sire)


def test_image_requires_license_and_attribution():
    info = {"thumburl": "https://upload.wikimedia.org/thumb.jpg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Example.jpg",
            "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "Artist": {"value": "Photographer"}}}
    assert stables.image_metadata(info, "Q1")["creator"] == "Photographer"
    info["thumburl"] = "https://thumb.wikimedia.org/wikipedia/commons/thumb/example.jpg"
    assert stables.image_metadata(info, "Q1")["thumbnail"] == info["thumburl"]
    info["thumburl"] = "https://thumb.wikimedia.org.evil.example/test.jpg"
    assert stables.image_metadata(info, "Q1") is None
    info["thumburl"] = "https://upload.wikimedia.org/thumb.jpg"
    info["extmetadata"]["LicenseShortName"]["value"] = "All rights reserved"
    assert stables.image_metadata(info, "Q1") is None


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


def test_photo_catalog_requires_corresponding_identity():
    item = {"name":{"value":"Example"}, "sireLabel":{"value":"Sire"}, "birth":{"value":"2020-01-01"},
            "horse":{"value":"https://www.wikidata.org/entity/Q1"}, "image":{"value":"https://commons.wikimedia.org/wiki/Special:FilePath/Example.jpg"}}
    row = {"horse_name":"Example","sire":"Sire","year":2020,"race_date":"2024-01-01"}
    assert stables.catalog_identity(row,[item]) == item
    assert stables.catalog_identity({**row,"sire":"Other"},[item]) is None
    assert stables.catalog_identity({**row,"year":2021},[item]) is None
    assert stables.catalog_identity({**row,"race_date":"1900-01-01"},[item],True) is None
    assert stables.catalog_identity(row,[item,{**item,"horse":{"value":"https://www.wikidata.org/entity/Q2"}}]) is None


def test_catalog_import_is_idempotent(db, monkeypatch):
    db.execute("INSERT INTO races(race_id,track_id,race_date,race_number,source) VALUES ('r','T','2024-01-01',1,'test')")
    db.execute("INSERT INTO runners(race_id,program_number,horse_id,horse_name,sire,extra) VALUES ('r','1','horse','Example','Sire',?)",
               ['{"horse_identity":{"age_sex":"4/G"}}'])
    item = {"name":{"value":"Example"},"sireLabel":{"value":"Sire"},"birth":{"value":"2020-01-01"},
            "horse":{"value":"https://www.wikidata.org/entity/Q1"},"image":{"value":"https://commons.wikimedia.org/wiki/Special:FilePath/Example.jpg"}}
    monkeypatch.setattr(stables,"photo_catalog",lambda:[item])
    info={"thumburl":"https://upload.wikimedia.org/thumb.jpg","descriptionurl":"https://commons.wikimedia.org/wiki/File:Example.jpg",
          "extmetadata":{"LicenseShortName":{"value":"CC BY-SA 4.0"},"Artist":{"value":"Creator"}}}
    monkeypatch.setattr(stables,"request",lambda *a,**k:{"query":{"pages":{"1":{"imageinfo":[info]}}}})
    assert stables.refresh_photos(db)["photographs"]==1
    before=db.rows_written
    assert stables.refresh_photos(db)["photographs"]==0
    assert db.rows_written==before
