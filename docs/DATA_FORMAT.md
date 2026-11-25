# Canonical Import Format

`reinman import file.json` loads any data you are licensed to use.

```json
{
  "source": "my_licensed_pp_files",
  "capabilities": {"speed_figures": true, "workouts": true},
  "cards": [{
    "meet": {"meet_id": "KEE-2026-10-10", "track_id": "KEE", "track_name": "Keeneland",
             "race_date": "2026-10-10", "country": "USA"},
    "races": [{
      "race_number": 5, "post_time": "2026-10-10T19:42:00.000Z", "surface": "T",
      "distance_yards": 1870, "distance_desc": "1 1/16 Miles", "race_type": "AOC",
      "race_class": "Allowance Optional Claiming", "purse": 120000, "max_claim": 80000,
      "track_condition": "Firm", "temp_f": 68, "precip_pct": 10, "is_cancelled": false,
      "pools": [{"wager_type": "EXACTA"}, {"wager_type": "PICK4", "race_list": "5-6-7-8"}],
      "changes": [{"type": "Scratch", "text": "#7 scratched"}],
      "runners": [{
        "program_number": "1", "post_position": 1, "horse_id": "reg:12345678",
        "horse_name": "Example Horse", "jockey_id": "j:abc", "jockey_name": "A. Rider",
        "trainer_id": "t:xyz", "trainer_name": "B. Trainer", "sire": "Sire Name",
        "dam": "Dam Name", "dam_sire": "Damsire", "weight": 122, "equipment": "B",
        "medication": "L", "claiming": 80000, "ml_odds": 3.0, "live_odds": null,
        "scratched": false,
        "extra": {
          "past_performances": [{"date": "2026-09-12", "track": "KEE", "distance_yards": 1760,
            "surface": "T", "finish": 2, "field_size": 9, "beaten_lengths": 0.5,
            "speed_fig": 88, "call_positions": [4, 3, 2], "trouble": "bumped start"}],
          "workouts": [{"date": "2026-10-02", "distance_yards": 880, "time": 48.2, "rank": 3, "of": 41}]
        }
      }]
    }]
  }],
  "results": [{"meet": {"meet_id": "KEE-2026-10-10", "track_id": "KEE", "race_date": "2026-10-10"},
    "races": [{"race_number": 5, "track_condition": "Firm",
      "finishers": [{"program_number": "1", "horse_name": "Example Horse", "finish_position": 1,
                     "win_payoff": 8.4, "place_payoff": 4.2, "show_payoff": 3.0}],
      "payoffs": [{"wager_type": "EXACTA", "base_amount": 2, "winning_numbers": "1-4", "payoff_amount": 41.2}],
      "scratches": ["Scratched Horse"]}]}]
}
```

Rules: odds are decimal *profit* odds (5/2 → 2.5); times are UTC ISO-8601; surfaces
`D`/`T`/`S`; distances in yards. Every field in `extra` is optional; absent fields are
reported as unavailable, never imputed.


### Research ZIPs
Upload the JSON member of a Rein Man research ZIP to Documents. Race entries,
pedigree, published recent Beyer figures and season trainer/jockey records are
recognized. Conditional or incomplete fields enrich matching existing cards,
but only complete verified fields can create a race. Quarter-horse cards are
excluded. Published snapshots are timestamped at import and usable for modeling
only if received before the verified post time. No undated figure is assigned
a fabricated past-performance date.

### Racing intelligence
The existing monitor/daily jobs check syndicated racing-news feeds every three
hours, storing headlines, publication times and first-seen times with source
links. Full entity name matches appear as context on upcoming race analysis and
are never treated as confirmed scratches, medical facts or numerical signals.
Performance includes recent headlines and trainer rankings from complete official
fields. The optional REINMAN_NEWS_FEEDS repository variable accepts up to four
comma-separated HTTPS RSS URLs. A refused or unavailable feed is logged and
does not prevent race processing.

Travel is the estimated great-circle distance between the last known race venue
and the current venue. It is not the horse's actual shipping route. Maternal
lineage, connection changes, recent starts, workout recency and source-specific
Beyer summaries feed the existing weekly walk-forward learning gate. Previous
models continue to use their original feature columns until a new model qualifies.
