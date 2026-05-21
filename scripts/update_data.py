#!/usr/bin/env python3
"""
Insights Summer Movie Challenge — Daily Data Updater
=====================================================
Fetches box office data from Box Office Mojo and Metacritic scores,
reads participant picks and theater bonuses from CSV files,
then writes an updated data.json for the dashboard.

Run manually:  python scripts/update_data.py
Run by pipeline: automatically on schedule
"""

import json
import re
import csv
import os
from datetime import datetime, date
from difflib import SequenceMatcher

import requests
from bs4 import BeautifulSoup


# ─────────────────────────────────────────────────────────────────
#  PATHS  (all relative to repo root)
# ─────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JSON    = os.path.join(BASE_DIR, "data.json")
PICKS_CSV    = os.path.join(BASE_DIR, "picks.csv")
THEATER_CSV  = os.path.join(BASE_DIR, "theater-bonuses.csv")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

BOM_URL = "https://www.boxofficemojo.com/year/2026/"


# ─────────────────────────────────────────────────────────────────
#  TITLE MATCHING  —  add aliases if BOM uses a different title
# ─────────────────────────────────────────────────────────────────
MOVIE_ALIASES = {
    "mi8":          ["mission: impossible", "final reckoning", "mission impossible"],
    "lilo":         ["lilo & stitch", "lilo and stitch"],
    "karate":       ["karate kid: legends", "karate kid legends"],
    "httyd":        ["how to train your dragon"],
    "materialists": ["materialists"],
    "28years":      ["28 years later"],
    "f1":           ["f1"],
    "m3gan2":       ["m3gan 2", "m3gan2", "megan 2.0", "m3gan 2.0"],
    "jurassic":     ["jurassic world: rebirth", "jurassic world rebirth"],
    "superman":     ["superman"],
    "smurfs":       ["the smurfs", "smurfs"],
    "ff":           ["fantastic four: first steps", "fantastic four first steps", "the fantastic four"],
    "nakedgun":     ["the naked gun", "naked gun"],
    "freakier":     ["freakier friday"],
}


# ─────────────────────────────────────────────────────────────────
#  BOX OFFICE MOJO
# ─────────────────────────────────────────────────────────────────
def fetch_box_office():
    """Return {movie_id: gross_int} for all matched competition movies."""
    print("\n[1/4] Fetching Box Office Mojo...")
    try:
        r = requests.get(BOM_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"  ⚠  BOM fetch failed: {e}")
        return {}

    soup    = BeautifulSoup(r.text, "html.parser")
    results = {}

    # BOM table rows  ─ try multiple selectors for resilience
    rows = (
        soup.select("table.imdb-scroll-table tr")
        or soup.select("table tr")
    )

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        # Title is usually the second cell; gross is usually the third
        title_text = cells[1].get_text(strip=True).lower()
        gross_text = cells[2].get_text(strip=True).replace("$", "").replace(",", "")

        try:
            gross = int(gross_text)
        except ValueError:
            continue

        movie_id = _match_title(title_text)
        if movie_id and (movie_id not in results or gross > results[movie_id]):
            results[movie_id] = gross
            print(f"  ✓  {movie_id:12s}  ${gross:>14,.0f}  ← {title_text[:50]}")

    if not results:
        print("  —  No competition movies found yet (they may not have released)")

    return results


def _match_title(title_lower):
    """Return movie_id for a title string, or None if no match."""
    for movie_id, aliases in MOVIE_ALIASES.items():
        for alias in aliases:
            if alias in title_lower or title_lower in alias:
                return movie_id
    # Fuzzy fallback
    best, best_id = 0.0, None
    for movie_id, aliases in MOVIE_ALIASES.items():
        for alias in aliases:
            score = SequenceMatcher(None, title_lower, alias).ratio()
            if score > best:
                best, best_id = score, movie_id
    return best_id if best > 0.65 else None


# ─────────────────────────────────────────────────────────────────
#  METACRITIC
# ─────────────────────────────────────────────────────────────────
def fetch_metacritic_score(slug):
    """Return integer Metacritic score for a slug, or None."""
    if not slug:
        return None
    url = f"https://www.metacritic.com/movie/{slug}/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 404:
            print(f"  ⚠  404 for slug '{slug}' — check metacriticSlug in data.json")
            return None
        r.raise_for_status()
    except Exception as e:
        print(f"  ⚠  Metacritic fetch failed ({slug}): {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    # Try several selectors — Metacritic occasionally redesigns their page
    for selector in [
        "div[data-v-4cdca868] span",
        ".c-siteReviewScore span",
        "span.metascore_w",
        "[class*='metascore']",
        "[class*='score_favorable']",
        "[class*='score_mixed']",
        "[class*='score_unfavorable']",
    ]:
        el = soup.select_one(selector)
        if el:
            txt = el.get_text(strip=True)
            if txt.isdigit() and 0 < int(txt) <= 100:
                return int(txt)

    # JSON-LD fallback
    match = re.search(r'"ratingValue"\s*:\s*"?(\d+)"?', r.text)
    if match:
        score = int(match.group(1))
        if 0 < score <= 100:
            return score

    return None


# ─────────────────────────────────────────────────────────────────
#  CSV READERS
# ─────────────────────────────────────────────────────────────────
def read_picks():
    """
    Parse picks.csv → list of participant dicts.
    Returns None if picks.csv doesn't exist (keep existing participants).
    Skips comment lines starting with #.
    """
    if not os.path.exists(PICKS_CSV):
        print("  —  picks.csv not found, keeping existing participants")
        return None

    participants = []
    with open(PICKS_CSV, newline="", encoding="utf-8") as f:
        # Strip comment lines before passing to DictReader
        clean_lines = [l for l in f if not l.strip().startswith("#")]

    reader = csv.DictReader(clean_lines)
    for row in reader:
        name = row.get("participant_name", "").strip()
        if not name:
            continue

        picks = []
        for i in range(1, 9):
            raw = row.get(f"movie_{i}", "").strip()
            if not raw:
                continue
            matched = _match_title(raw.lower())
            if matched:
                picks.append(matched)
            else:
                print(f"  ⚠  Could not match movie '{raw}' for {name} — check spelling")

        if len(picks) != 8:
            print(f"  ⚠  {name} has {len(picks)} picks (expected 8) — check picks.csv")

        pid = name.lower().replace(" ", "_")
        participants.append({
            "id":            pid,
            "name":          name,
            "picks":         picks,
            "theaterBonuses": [],
        })

    print(f"  ✓  Loaded {len(participants)} participants from picks.csv")
    return participants


def read_theater_bonuses():
    """Parse theater-bonuses.csv → {participant_id: [movie_id, ...]}"""
    bonuses = {}
    if not os.path.exists(THEATER_CSV):
        return bonuses

    with open(THEATER_CSV, newline="", encoding="utf-8") as f:
        clean_lines = [l for l in f if not l.strip().startswith("#")]

    reader = csv.DictReader(clean_lines)
    for row in reader:
        name     = row.get("participant_name", "").strip()
        movie_id = row.get("movie_id", "").strip().lower()
        if not name or not movie_id:
            continue
        pid = name.lower().replace(" ", "_")
        bonuses.setdefault(pid, [])
        if movie_id not in bonuses[pid]:
            bonuses[pid].append(movie_id)

    total = sum(len(v) for v in bonuses.values())
    print(f"  ✓  Loaded {total} theater sighting(s) from theater-bonuses.csv")
    return bonuses


# ─────────────────────────────────────────────────────────────────
#  SCORING  (mirrors the dashboard JS — keep in sync)
# ─────────────────────────────────────────────────────────────────
def _bo_points(gross):       return gross // 1_000_000
def _milestone_bonus(gross):
    return sum(25 for m in [50, 100, 150, 200] if gross >= m * 1_000_000)
def _metacritic_bonus(score):
    if score is None: return 0
    if score >= 90: return 100
    if score >= 80: return 50
    if score >= 70: return 40
    if score >= 60: return 25
    if score >= 50: return 20
    if score >= 40: return 10
    if score >= 20: return 0
    return -5
def _uniqueness_bonus(movie_id, participants):
    c = sum(1 for p in participants if movie_id in p["picks"])
    return 50 if c == 1 else 25 if c <= 3 else 0

def _score_participant(p, movies, all_participants):
    total = 0
    movie_map = {m["id"]: m for m in movies}
    for mid in p["picks"]:
        m = movie_map.get(mid)
        if not m: continue
        total += _bo_points(m["gross"])
        total += _milestone_bonus(m["gross"])
        total += _metacritic_bonus(m.get("metacritic"))
        total += _uniqueness_bonus(mid, all_participants)
        if mid in p.get("theaterBonuses", []):
            total += 50
    return total


# ─────────────────────────────────────────────────────────────────
#  WEEKLY SNAPSHOT
# ─────────────────────────────────────────────────────────────────
def update_snapshot(data):
    """
    Upsert the current week's snapshot.
    One snapshot per calendar week (Mon–Sun); updates in place if same week,
    appends if new week.
    """
    today     = date.today()
    week_num  = today.isocalendar()[1]
    year      = today.year
    label     = today.strftime("%b ") + str(today.day)   # e.g. "Jun 2"
    scores    = {
        p["id"]: _score_participant(p, data["movies"], data["participants"])
        for p in data["participants"]
    }

    snapshots = data.setdefault("weeklySnapshots", [])
    for i, snap in enumerate(snapshots):
        try:
            snap_date = datetime.strptime(snap.get("date", "2000-01-01"), "%Y-%m-%d").date()
        except ValueError:
            continue
        if snap_date.isocalendar()[1] == week_num and snap_date.year == year:
            snapshots[i].update({"scores": scores, "label": label, "date": today.isoformat()})
            print(f"  ✓  Updated existing week {week_num} snapshot ({label})")
            return

    snapshots.append({
        "week":   len(snapshots) + 1,
        "label":  label,
        "date":   today.isoformat(),
        "scores": scores,
    })
    print(f"  ✓  Added new snapshot: week {len(snapshots)} ({label})")


# ─────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────
def main():
    print("=" * 55)
    print(f"  Summer Movie Challenge — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    with open(DATA_JSON, encoding="utf-8") as f:
        data = json.load(f)

    # ── 1. Box office ───────────────────────────────────────────
    bom = fetch_box_office()
    for movie in data["movies"]:
        mid     = movie["id"]
        new_gross = bom.get(mid)
        if new_gross is not None and new_gross != movie["gross"]:
            print(f"  ↑  {mid}: ${movie['gross']:,} → ${new_gross:,}")
            movie["gross"] = new_gross

    # ── 2. Metacritic ───────────────────────────────────────────
    print("\n[2/4] Fetching Metacritic scores...")
    for movie in data["movies"]:
        if movie.get("metacriticLocked"):
            continue
        score = fetch_metacritic_score(movie.get("metacriticSlug"))
        if score is not None:
            print(f"  ✓  {movie['title'][:40]:40s}  {score}")
            movie["metacritic"]       = score
            movie["metacriticLocked"] = True
        else:
            print(f"  —  {movie['title'][:40]:40s}  not available yet")

    # ── 3. Participants ─────────────────────────────────────────
    print("\n[3/4] Updating participants...")
    theater_bonuses = read_theater_bonuses()
    new_participants = read_picks()

    if new_participants is not None:
        for p in new_participants:
            p["theaterBonuses"] = theater_bonuses.get(p["id"], [])
        data["participants"] = new_participants
    else:
        for p in data["participants"]:
            p["theaterBonuses"] = theater_bonuses.get(p["id"], [])

    # ── 4. Snapshot + metadata ──────────────────────────────────
    print("\n[4/4] Updating snapshot & metadata...")
    if data["participants"]:
        update_snapshot(data)
    else:
        print("  —  No participants yet, skipping snapshot")

    data["meta"]["lastUpdated"] = datetime.now().strftime("%B %-d, %Y")

    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"\n{'=' * 55}")
    print(f"  ✅  data.json updated successfully")
    print(f"{'=' * 55}\n")


if __name__ == "__main__":
    main()
