from reinman.sources.formfav import model_features, snapshot


def test_formfav_form_features():
    record = snapshot({"last20Starts": "321X21", "stats": {
        "overall": {"starts": 10, "wins": 3, "places": 6},
        "track": {"starts": 0, "wins": 0, "places": 0},
        "distance": {"starts": 2, "wins": 3},
    }}, "2026-11-26", "2026-11-26T12:00:00Z")
    features = model_features(record)
    assert features["formfav_career_win_rate"] == 0.3
    assert features["formfav_career_place_rate"] == 0.6
    assert features["formfav_track_starts_log"] == 0
    assert "formfav_track_win_rate" not in features
    assert "formfav_distance_win_rate" not in features
    assert record["last20Starts"] == "321X21"
