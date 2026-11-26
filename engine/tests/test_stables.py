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
    info["extmetadata"]["LicenseShortName"]["value"] = "All rights reserved"
    assert stables.image_metadata(info, "Q1") is None
