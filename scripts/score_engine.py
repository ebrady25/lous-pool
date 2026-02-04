#!/usr/bin/env python3
"""
BirdieBuddy Pool Scoring Engine
================================
Pulls live/historical scores from DataGolf API, applies Rule 4 replacement
mechanics and missed-cut penalties, and outputs leaderboard JSON.

Usage:
  python score_engine.py                   # Live tournament
  python score_engine.py --historical 4    # Historical event by ID
  python score_engine.py --output out.json # Custom output path
"""

import json
import sys
import os
import argparse
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("DATAGOLF_API_KEY", "576a75cc2c5275542b9b9d98419b")
BASE_URL = "https://feeds.datagolf.com"

# Missed-cut penalty schedule: strokes missed -> penalty added
MC_PENALTY = {1: 5, 2: 6, 3: 7}  # 4+ -> 8
def mc_penalty(strokes_missed):
    return MC_PENALTY.get(strokes_missed, 8) if strokes_missed >= 1 else 0

# Overuse penalty schedule: nth use -> penalty strokes
OVERUSE_PENALTY = {4: 10, 5: 15, 6: 20}

# ---------------------------------------------------------------------------
# DataGolf API
# ---------------------------------------------------------------------------
def fetch_json(url):
    """Fetch JSON from URL with error handling."""
    try:
        req = Request(url, headers={"User-Agent": "BirdieBuddy/1.0"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except URLError as e:
        print(f"API Error: {e}", file=sys.stderr)
        return None

def fetch_live_scores():
    """Fetch current/most-recent tournament from DataGolf."""
    url = f"{BASE_URL}/preds/live-tournament-stats?file_format=json&key={API_KEY}"
    return fetch_json(url)

def fetch_historical(event_id):
    """Fetch historical event by ID."""
    url = f"{BASE_URL}/historical-raw-data/rounds?tour=pga&event_id={event_id}&file_format=json&key={API_KEY}"
    return fetch_json(url)

# ---------------------------------------------------------------------------
# Score Processing
# ---------------------------------------------------------------------------
def get_round_score(player, round_num):
    """Extract score for a given round. Returns None if round not played."""
    rkey = f"round_{round_num}"
    rd = player.get(rkey)
    if rd is None:
        return None
    if isinstance(rd, dict):
        return rd.get("score")
    return rd

def get_round_par(player, round_num):
    """Extract course par for a round."""
    rkey = f"round_{round_num}"
    rd = player.get(rkey)
    if isinstance(rd, dict):
        return rd.get("course_par", 72)
    return 72

def get_r2_teetime(player):
    """Extract R2 tee time for replacement player ordering."""
    r2 = player.get("round_2")
    if isinstance(r2, dict):
        return r2.get("teetime", "99:99")
    return "99:99"

def player_made_cut(player):
    """Check if player made the cut (has R3 data)."""
    r3 = player.get("round_3")
    return r3 is not None

def get_total_thru_2(player):
    """Get R1+R2 total."""
    r1 = get_round_score(player, 1)
    r2 = get_round_score(player, 2)
    if r1 is None or r2 is None:
        return None
    return r1 + r2

def process_tournament(dg_data):
    """
    Process DataGolf tournament data into structured scoring data.
    Returns dict with event info, player scores, cut line, and replacement players.
    """
    scores = dg_data.get("scores") or dg_data.get("data", [])
    event_name = dg_data.get("event_name", "Unknown Event")
    event_completed = dg_data.get("event_completed")
    
    # Determine event status
    # Check what round data is available
    has_r3 = any(player_made_cut(p) for p in scores)
    has_r4 = any(get_round_score(p, 4) is not None for p in scores)
    has_r2 = any(get_round_score(p, 2) is not None for p in scores)
    has_r1 = any(get_round_score(p, 1) is not None for p in scores)
    
    if has_r4 and event_completed:
        status = "complete"
        current_round = 4
    elif has_r4:
        status = "round4"
        current_round = 4
    elif has_r3:
        status = "round3"
        current_round = 3
    elif has_r2:
        status = "round2"
        current_round = 2
    elif has_r1:
        status = "round1"
        current_round = 1
    else:
        status = "pre"
        current_round = 0
    
    # Get course par (from first player's R1)
    course_par = 72
    for p in scores:
        cp = get_round_par(p, 1)
        if cp:
            course_par = cp
            break
    
    # Find cut line (only if R3 data exists)
    cut_line = None
    replacement_players = []
    
    if has_r3:
        made_cut_totals = []
        for p in scores:
            if player_made_cut(p):
                t2 = get_total_thru_2(p)
                if t2 is not None:
                    made_cut_totals.append(t2)
        
        if made_cut_totals:
            cut_line = max(made_cut_totals)
        
        # Find replacement players: made cut, R1+R2 = cut line, sorted by R2 tee time
        if cut_line:
            cut_line_players = []
            for p in scores:
                if player_made_cut(p):
                    t2 = get_total_thru_2(p)
                    if t2 == cut_line:
                        cut_line_players.append({
                            "name": p.get("player_name", "Unknown"),
                            "dg_id": p.get("dg_id"),
                            "r2_teetime": get_r2_teetime(p),
                            "r1": get_round_score(p, 1),
                            "r2": get_round_score(p, 2),
                            "r3": get_round_score(p, 3),
                            "r4": get_round_score(p, 4),
                        })
            
            # Sort by R2 tee time ascending
            cut_line_players.sort(key=lambda x: x["r2_teetime"])
            replacement_players = cut_line_players
    
    # Build player lookup
    players = {}
    for p in scores:
        name = p.get("player_name", "Unknown")
        r1 = get_round_score(p, 1)
        r2 = get_round_score(p, 2)
        r3 = get_round_score(p, 3)
        r4 = get_round_score(p, 4)
        
        made_cut = player_made_cut(p)
        total_thru_2 = get_total_thru_2(p)
        mc_by = (total_thru_2 - cut_line) if (cut_line and not made_cut and total_thru_2) else 0
        
        players[name] = {
            "name": name,
            "dg_id": p.get("dg_id"),
            "fin_text": p.get("fin_text", ""),
            "r1": r1,
            "r2": r2,
            "r3": r3,
            "r4": r4,
            "total": sum(x for x in [r1, r2, r3, r4] if x is not None),
            "thru": p.get("thru", ""),
            "today": p.get("today", ""),
            "made_cut": made_cut,
            "mc_by": mc_by,
            "mc_penalty": mc_penalty(mc_by) if mc_by > 0 else 0,
            "course_par": course_par,
        }
    
    return {
        "event_name": event_name,
        "event_completed": event_completed,
        "status": status,
        "current_round": current_round,
        "course_par": course_par,
        "cut_line": cut_line,
        "replacement_players": replacement_players,
        "players": players,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# Team Scoring
# ---------------------------------------------------------------------------
def normalize_name(name):
    """Normalize player name for matching. Handles 'Last, First' -> 'First Last' etc."""
    name = name.strip()
    if "," in name:
        parts = name.split(",", 1)
        name = f"{parts[1].strip()} {parts[0].strip()}"
    return name.lower()

def build_name_lookup(players_dict):
    """Build flexible name lookup from DataGolf player dict."""
    lookup = {}
    for key, pdata in players_dict.items():
        # Original key (usually "Last, First")
        norm = normalize_name(key)
        lookup[norm] = pdata
        # Also index by "first last" format
        parts = key.split(",")
        if len(parts) == 2:
            last = parts[0].strip().lower()
            first = parts[1].strip().lower()
            lookup[f"{first} {last}"] = pdata
            # Index by full last name (for unique ones)
            lookup[last] = pdata
            # Handle abbreviated first names: "J." -> matches "Justin"
            if "." not in first:
                lookup[f"{first[0]}. {last}"] = pdata
    return lookup

def find_player(name, lookup):
    """Find a player in the lookup by name, trying various normalizations."""
    # Handle WD replacement format: "Max Homa/J. Day" -> try first name
    if "/" in name:
        name = name.split("/")[0].strip()
    
    n = normalize_name(name)
    
    # Exact match
    if n in lookup:
        return lookup[n]
    
    # Try removing middle initials, periods, etc.
    clean = n.replace(".", "").replace("  ", " ").strip()
    if clean in lookup:
        return lookup[clean]
    
    # Try last name only
    parts = n.split()
    if len(parts) >= 2:
        last = parts[-1]
        if last in lookup:
            return lookup[last]
    
    # Fuzzy: check if all parts of the search name appear in a key
    for key, val in lookup.items():
        if all(part in key for part in n.split()):
            return val
        if all(part in n for part in key.split() if len(part) > 2):
            return val
    
    return None

def score_team(team, tournament_data, season_usage=None):
    """
    Score a single team for the current tournament.
    
    team: {
        "owner": str,
        "alias": str,
        "players": ["Player 1", "Player 2", "Player 3", "Player 4"],
        "alternates": ["Alt 1", "Alt 2"]
    }
    
    Returns scored team dict with per-player breakdowns.
    """
    players_dict = tournament_data["players"]
    lookup = build_name_lookup(players_dict)
    replacement_players = tournament_data["replacement_players"]
    cut_line = tournament_data["cut_line"]
    course_par = tournament_data["course_par"]
    status = tournament_data["status"]
    
    team_par = 4 * 4 * course_par  # 4 players * 4 rounds * par
    scored_players = []
    mc_count = 0  # Track MC players for replacement assignment
    
    for i, player_name in enumerate(team["players"][:4]):
        pdata = find_player(player_name, lookup)
        
        if pdata is None:
            # Player not found in tournament data - might be WD before starting
            scored_players.append({
                "name": player_name,
                "slot": i + 1,
                "r1": None, "r2": None, "r3": None, "r4": None,
                "penalty": 0,
                "total": None,
                "status": "not_found",
                "replacement": None,
            })
            continue
        
        r1 = pdata["r1"]
        r2 = pdata["r2"]
        r3 = pdata["r3"]
        r4 = pdata["r4"]
        penalty = 0
        replacement = None
        player_status = "active"
        
        if not pdata["made_cut"] and cut_line is not None:
            # Missed cut - apply Rule 4
            player_status = "mc"
            penalty = pdata["mc_penalty"]
            mc_count += 1
            
            # Assign replacement player (by team listing order)
            rep_idx = mc_count - 1  # 0-indexed
            if rep_idx < len(replacement_players):
                rep = replacement_players[rep_idx]
                r3 = rep["r3"]
                r4 = rep["r4"]
                replacement = rep["name"]
        
        total = sum(x for x in [r1, r2, r3, r4] if x is not None) + penalty
        
        scored_players.append({
            "name": player_name,
            "dg_name": pdata["name"],
            "slot": i + 1,
            "r1": r1,
            "r2": r2,
            "r3": r3,
            "r4": r4,
            "penalty": penalty,
            "total": total,
            "status": player_status,
            "replacement": replacement,
            "fin_text": pdata["fin_text"],
            "today": pdata.get("today", ""),
            "thru": pdata.get("thru", ""),
        })
    
    # Overuse penalties
    overuse_penalty = 0
    if season_usage:
        for p in team["players"][:4]:
            uses = season_usage.get(p, 0) + 1  # +1 for this week
            if uses in OVERUSE_PENALTY:
                overuse_penalty += OVERUSE_PENALTY[uses]
    
    team_total = sum(p["total"] for p in scored_players if p["total"] is not None)
    team_total += overuse_penalty
    
    return {
        "owner": team["owner"],
        "alias": team.get("alias", ""),
        "players": scored_players,
        "team_total": team_total,
        "team_par": team_par,
        "team_to_par": team_total - team_par if team_total else None,
        "overuse_penalty": overuse_penalty,
        "mc_count": mc_count,
    }


def build_leaderboard(rosters, tournament_data, season_data=None):
    """
    Score all teams and build the full leaderboard.
    
    rosters: list of team dicts
    tournament_data: output of process_tournament()
    season_data: optional dict of {"team_name": {"pts": cumulative, "usage": {...}}}
    
    Returns complete leaderboard JSON.
    """
    season_data = season_data or {}
    scored_teams = []
    
    for team in rosters:
        owner = team["owner"]
        usage = season_data.get(owner, {}).get("usage", {})
        scored = score_team(team, tournament_data, season_usage=usage)
        
        # Add season context
        prev_pts = season_data.get(owner, {}).get("pts", 0)
        scored["season_pts"] = prev_pts + (scored["team_total"] or 0)
        scored["prev_pts"] = prev_pts
        
        scored_teams.append(scored)
    
    # Sort by team_to_par (lowest first), then by season total
    scored_teams.sort(key=lambda x: (x["team_to_par"] if x["team_to_par"] is not None else 9999))
    
    # Assign ranks
    for i, team in enumerate(scored_teams):
        team["weekly_rank"] = i + 1
    
    # Season ranks
    season_sorted = sorted(scored_teams, key=lambda x: x["season_pts"])
    for i, team in enumerate(season_sorted):
        team["season_rank"] = i + 1
    
    # Re-sort by weekly rank for output
    scored_teams.sort(key=lambda x: x["weekly_rank"])
    leader_total = scored_teams[0]["team_to_par"] if scored_teams else 0
    
    return {
        "event": tournament_data["event_name"],
        "status": tournament_data["status"],
        "current_round": tournament_data["current_round"],
        "course_par": tournament_data["course_par"],
        "cut_line": tournament_data["cut_line"],
        "replacement_players": [
            {"name": r["name"], "r3": r["r3"], "r4": r["r4"]}
            for r in tournament_data["replacement_players"][:5]
        ],
        "team_par": 4 * 4 * tournament_data["course_par"],
        "updated_at": tournament_data["updated_at"],
        "teams": scored_teams,
        "total_teams": len(scored_teams),
    }


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="BirdieBuddy Pool Scoring Engine")
    parser.add_argument("--historical", type=int, help="Fetch historical event by ID")
    parser.add_argument("--output", default="data/leaderboard.json", help="Output JSON path")
    parser.add_argument("--rosters", default="data/rosters.json", help="Rosters JSON path")
    parser.add_argument("--season", default="data/season.json", help="Season data JSON path")
    args = parser.parse_args()
    
    # Fetch tournament data
    if args.historical:
        print(f"Fetching historical event {args.historical}...")
        dg_data = fetch_historical(args.historical)
    else:
        print("Fetching live tournament data...")
        dg_data = fetch_live_scores()
    
    if not dg_data:
        print("Failed to fetch data", file=sys.stderr)
        sys.exit(1)
    
    print(f"Event: {dg_data.get('event_name', 'Unknown')}")
    
    # Process tournament
    tournament = process_tournament(dg_data)
    print(f"Status: {tournament['status']}, Cut: {tournament['cut_line']}")
    print(f"Replacements: {[r['name'] for r in tournament['replacement_players'][:3]]}")
    
    # Load rosters
    rosters_path = args.rosters
    if os.path.exists(rosters_path):
        with open(rosters_path) as f:
            rosters = json.load(f)
    else:
        print(f"No rosters file at {rosters_path}. Generating tournament-only output.")
        # Output just tournament data
        with open(args.output, "w") as f:
            json.dump(tournament, f, indent=2)
        print(f"Wrote tournament data to {args.output}")
        return
    
    # Load season data
    season_data = {}
    if os.path.exists(args.season):
        with open(args.season) as f:
            season_data = json.load(f)
    
    # Build leaderboard
    leaderboard = build_leaderboard(rosters, tournament, season_data)
    
    # Write output
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(leaderboard, f, indent=2)
    
    print(f"\nLeaderboard: {leaderboard['total_teams']} teams")
    print(f"Leader: {leaderboard['teams'][0]['owner']} ({leaderboard['teams'][0]['team_to_par']:+d})")
    print(f"Written to {args.output}")


if __name__ == "__main__":
    main()
