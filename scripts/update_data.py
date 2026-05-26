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
from datetime import datetime, date, timedelta
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
    # ── May 22 ──────────────────────────────────────────────────
    "boosters":      ["i love boosters"],
    "ladiesfirst":   ["ladies first"],
    "passenger":     ["passenger"],
    "mando":         ["star wars: the mandalorian", "the mandalorian & grogu", "mandalorian and grogu",
                      "mandalorian & grogu", "mandalorian grogu"],
    # ── May 29 ──────────────────────────────────────────────────
    "back":          ["the backrooms", "backrooms"],
    "breadwinner":   ["the breadwinner", "breadwinner"],
    "pressure":      ["pressure"],
    "tuner":         ["tuner"],
    # ── June 5 ──────────────────────────────────────────────────
    "motu":          ["masters of the universe"],
    "powerballad":   ["power ballad"],
    "scarymovie":    ["scary movie"],
    # ── June 12 ─────────────────────────────────────────────────
    "disc":          ["disclosure day"],
    "furious":       ["the furious"],
    # ── June 19 ─────────────────────────────────────────────────
    "robinhood":     ["the death of robin hood", "death of robin hood"],
    "leviticus":     ["leviticus"],
    "toy5":          ["toy story 5"],
    # ── June 26 ─────────────────────────────────────────────────
    "invite":        ["the invite"],
    "jackass":       ["jackass: best and last", "jackass best and last", "jackass"],
    "littlebrother": ["little brother"],
    "supergirl":     ["supergirl: woman of tomorrow", "supergirl woman of tomorrow", "supergirl"],
    # ── July 1 ──────────────────────────────────────────────────
    "minions":       ["minions & monsters", "minions and monsters"],
    # ── July 3 ──────────────────────────────────────────────────
    "youngwash":     ["young washington"],
    # ── July 10 ─────────────────────────────────────────────────
    "evildead":      ["evil dead burn", "evil dead: burn"],
    "gaildaughtry":  ["gail daughtry and the celebrity sex pass", "gail daughtry"],
    "moana":         ["moana"],
    # ── July 17 ─────────────────────────────────────────────────
    "odyssey":       ["the odyssey", "odyssey"],
    # ── July 24 ─────────────────────────────────────────────────
    "hours72":       ["72 hours"],
    "dink":          ["the dink"],
    # ── July 31 ─────────────────────────────────────────────────
    "iwantyoursex":  ["i want your sex"],
    "spiderman":     ["spider-man: brand new day", "spider-man brand new day",
                      "spiderman brand new day", "spider man brand new day"],
    # ── August 7 ────────────────────────────────────────────────
    "icecream":      ["ice cream man"],
    "supertroopers": ["super troopers 3", "super troopers3"],
    "campmiasma":    ["teenage sex and death at camp miasma", "camp miasma"],
    # ── August 14 ───────────────────────────────────────────────
    "endofstreet":   ["the end of oak street", "end of oak street",
                      "the end of the street", "end of the street"],
    "pawpatrol":     ["paw patrol: the dino movie", "paw patrol the dino movie", "paw patrol"],
    # ── August 21 ───────────────────────────────────────────────
    "insidious":     ["insidious: out of the further", "insidious out of the further", "insidious"],
    "spaweekend":    ["spa weekend"],
    # ── August 28 ───────────────────────────────────────────────
    "coyote":        ["coyote vs. acme", "coyote vs acme", "coyote versus acme"],
    "dogstars":      ["the dog stars", "dog stars"],
    "findingemily":  ["finding emily"],
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

    # BOM table structure (as of 2026):
    #   [0] Rank  [1] Title  [2-4] Weekly stats  [5] Total Gross  [6] Theaters  ...
    # Selector: the yearly chart table uses class "mojo-body-table"
    rows = (
        soup.select("table.mojo-body-table tr")
        or soup.select("table.a-bordered tr")
        or soup.select("table tr")
    )

    print(f"  →  {len(rows)} rows found in BOM table")

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue

        title_text = cells[1].get_text(strip=True).lower()

        # Total domestic gross is at index 5; scan forward if it's a dash
        gross = 0
        for idx in [5, 3, 2]:
            if idx >= len(cells):
                continue
            raw = cells[idx].get_text(strip=True).replace("$", "").replace(",", "")
            try:
                val = int(raw)
                if val > 0:
                    gross = val
                    break
            except ValueError:
                continue

        if gross == 0:
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
    # Direct ID match (e.g. picks.csv uses short IDs like "mando", "toy5")
    clean = title_lower.strip()
    if clean in MOVIE_ALIASES:
        return clean
    # Alias substring match
    for movie_id, aliases in MOVIE_ALIASES.items():
        for alias in aliases:
            if alias in clean or clean in alias:
                return movie_id
    # Fuzzy fallback
    best, best_id = 0.0, None
    for movie_id, aliases in MOVIE_ALIASES.items():
        for alias in aliases:
            score = SequenceMatcher(None, clean, alias).ratio()
            if score > best:
                best, best_id = score, movie_id
    return best_id if best > 0.82 else None


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

    # JSON-LD is the most reliable — Metacritic embeds it in every page
    match = re.search(r'"ratingValue"\s*:\s*"?(\d+)"?', r.text)
    if match:
        score = int(match.group(1))
        if 0 < score <= 100:
            return score

    # CSS selector fallbacks — Metacritic occasionally redesigns their page
    soup = BeautifulSoup(r.text, "html.parser")
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
    """
    Mirror the JS scoreMovie() logic exactly.
    Metacritic is only counted after a film's release date (matches effectiveMeta in JS).
    """
    total = 0
    today_str = date.today().isoformat()   # "YYYY-MM-DD"
    movie_map = {m["id"]: m for m in movies}
    for mid in p["picks"]:
        m = movie_map.get(mid)
        if not m: continue
        total += _bo_points(m["gross"])
        total += _milestone_bonus(m["gross"])
        # Gate metacritic by release date (mirrors JS effectiveMeta)
        meta_score = m.get("metacritic") if m.get("releaseDate", "9999") <= today_str else None
        total += _metacritic_bonus(meta_score)
        total += _uniqueness_bonus(mid, all_participants)
        if mid in p.get("theaterBonuses", []):
            total += 50
    return total


# ─────────────────────────────────────────────────────────────────
#  WEEKLY SNAPSHOT  (Friday-anchored competition weeks)
# ─────────────────────────────────────────────────────────────────
def _friday_week_start(d):
    """
    Return the most recent Friday on or before date d.
    Competition weeks run Friday–Thursday to align with release dates.
    BOM weekend data doesn't land until Sunday/Monday, so the pipeline
    overwrites the same week's snapshot each daily run — by Mon/Tue it's accurate.
    weekday(): Mon=0 Tue=1 Wed=2 Thu=3 Fri=4 Sat=5 Sun=6
    """
    days_since_friday = (d.weekday() - 4) % 7
    return d - timedelta(days=days_since_friday)


def update_snapshot(data):
    """
    Upsert the snapshot for the current competition week (Friday-anchored).
    One snapshot per Friday-start week; updated every daily pipeline run.
    The week label is the Friday date (e.g. "May 22", "Jun 5").
    """
    today      = date.today()
    week_fri   = _friday_week_start(today)          # the Friday that opened this week
    week_key   = week_fri.isoformat()               # "2026-05-22" — stable ID for this week
    label      = week_fri.strftime("%-m/%-d")       # "5/22", "5/29" — concise chart label
    scores     = {
        p["id"]: _score_participant(p, data["movies"], data["participants"])
        for p in data["participants"]
    }

    snapshots = data.setdefault("weeklySnapshots", [])

    # Update in place if we already have a snapshot for this Friday-week
    for i, snap in enumerate(snapshots):
        if snap.get("weekStart") == week_key:
            snapshots[i].update({
                "scores":    scores,
                "label":     label,
                "lastUpdated": today.isoformat(),
            })
            print(f"  ✓  Updated week of {label} snapshot (last updated {today})")
            return

    # New week — append
    snapshots.append({
        "week":        len(snapshots) + 1,
        "weekStart":   week_key,          # Friday date — stable key
        "label":       label,             # display label for chart axis
        "lastUpdated": today.isoformat(),
        "scores":      scores,
    })
    print(f"  ✓  Added snapshot for week of {label} (#{len(snapshots)})")


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
    today_str      = date.today().isoformat()
    lock_date_str  = data["meta"].get("metacriticLockDate", "September 8, 2026")
    # Parse the lock date — stored as human-readable in meta
    try:
        lock_date = datetime.strptime(lock_date_str, "%B %d, %Y").date()
    except ValueError:
        lock_date = date(2026, 9, 8)
    scores_locked_globally = date.today() >= lock_date

    for movie in data["movies"]:
        # Skip movies that haven't released yet
        if movie.get("releaseDate", "9999") > today_str:
            print(f"  —  {movie['title'][:40]:40s}  not released yet")
            continue
        # If the global lock date has passed AND we already have a score, freeze it
        if movie.get("metacriticLocked"):
            print(f"  🔒  {movie['title'][:40]:40s}  {movie.get('metacritic', '—')}  (locked)")
            continue
        score = fetch_metacritic_score(movie.get("metacriticSlug"))
        if score is not None:
            print(f"  ✓  {movie['title'][:40]:40s}  {score}")
            movie["metacritic"] = score
            # Lock permanently only on/after the official lock date
            if scores_locked_globally:
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
