"""Import a single-race OffTrackBetting.com result page saved by the operator."""
from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from ..util import to_float
from ..wagertypes import normalize_wager_type

_MONEY = r"(?:\d[\d,]*(?:\.\d+)?|\.\d+)"
_PAYOUT = re.compile(
    rf"(?:\$(?P<dollars>{_MONEY})|(?P<cents>\d+)\s*CENT)\s+"
    r"(?P<wager>[A-Za-z][A-Za-z 0-9-]*?)\s*\((?P<selection>[\dA-Z/ ,–-]+)\)"
    rf"\s*(?:(?P<correct>\d+\s+Correct)\s*)?(?:Paid\s*)?:\s*\$(?P<payout>{_MONEY})",
    re.IGNORECASE,
)
_PROGRAM = re.compile(r"\d{1,2}[A-Z]?")


def read_result(path: str, race_number: int) -> dict:
    soup = BeautifulSoup(Path(path).read_bytes(), "html.parser")
    matches = []
    for table in soup.find_all("table"):
        rows = [[" ".join(cell.stripped_strings) for cell in row.find_all(["td", "th"], recursive=False)]
                for row in table.find_all("tr")]
        for index, row in enumerate(rows):
            names = [name.lower() for name in row]
            program = next((label for label in ("program", "post", "#") if label in names), None)
            if program and {"horse", "win", "place", "show"}.issubset(names):
                matches.append((names, program, rows[index + 1:]))
                break
    if len(matches) != 1:
        raise ValueError("Expected one OTB finish table. Save the page after the official result is visible.")

    names, program, rows = matches[0]
    finishers = []
    for row in rows:
        if len(row) != len(names):
            continue
        item = dict(zip(names, row))
        number = item[program].upper().lstrip("#").strip()
        if not _PROGRAM.fullmatch(number) or not item["horse"]:
            continue
        finishers.append({
            "program_number": number, "horse_name": item["horse"], "finish_position": len(finishers) + 1,
            **{f"{market}_payoff": to_float(item[market]) for market in ("win", "place", "show")},
        })
    winners = [f for f in finishers if (f["win_payoff"] or 0) > 0]
    if len(winners) != 1 or winners[0] != finishers[0]:
        raise ValueError("Expected one paid winner in the first row of the OTB finish table.")

    payoffs = []
    seen = set()
    for match in _PAYOUT.finditer(soup.get_text(" ", strip=True)):
        name = match["wager"].strip()
        if "FUTURE" in name.upper():
            continue
        base = to_float(match["dollars"]) if match["dollars"] else int(match["cents"]) / 100
        payout = to_float(match["payout"])
        selection = re.sub(r"\s*[,–-]\s*", "-", match["selection"].upper().strip())
        wager_type = normalize_wager_type(name)
        if match["correct"] and wager_type.startswith("PICK"):
            # A consolation is not a full payout for grading that wager.
            if int(match["correct"].split()[0]) != int(wager_type[-1]):
                wager_type = "OTHER"
        key = (wager_type, selection, base, payout)
        if key in seen:
            continue
        seen.add(key)
        payoffs.append({"wager_type": wager_type, "wager_name": name,
                        "base_amount": base, "winning_numbers": selection, "payoff_amount": payout})
    return {"race_number": race_number, "finishers": finishers, "payoffs": payoffs}
