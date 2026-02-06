#!/usr/bin/env python3
"""
BirdieBuddy Pool Scoring Engine v2.0
====================================
Pulls live/historical scores from DataGolf API, applies Rule 4 replacement
mechanics and missed-cut penalties, and outputs leaderboard JSON.

FIXES in v2.0:
- Uses in-play endpoint for live scoring (has today/thru/position)
- Properly handles live_stats format from live-tournament-stats
- Combines both endpoints for complete picture
- Handles in-progress rounds (R1 not complete yet)

Usage:
  python score_engine.py                   # Live tournament
  python score_engine.py --historical 4    # Historical event by ID
  python score_engine.py --output out.json # Custom output path
"""

import json
import sys
import os
import argparse
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("DATAGOLF_API_KEY", "576a75cc2c5275542b9b9d98419b")
BASE_URL = "https://feeds.datagolf.com"

# Course par lookup by event name (DataGolf doesn't always provide this)
COURSE_PAR = {
    "WM Phoenix Open": 71,  # TPC Scottsdale
    "Farmers Insurance Open": 72,  # Torrey Pines
    "The American Express": 72,
    "AT&T Pebble Beach Pro-Am": 72,
    "Genesis Invitational": 71,  # Riviera
    "The Players Championship": 72,  # TPC Sawgrass
    "Arnold Palmer Invitational": 72,  # Bay Hill
    "THE PLAYERS": 72,
    "Masters Tournament": 72,  # Augusta
    "PGA Championship": 72,
    "U.S. Open": 70,  # Varies but often 70
    "The Open Championship": 72,  # Varies
}

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
        req = Request(url, headers={"User-Agent": "BirdieBuddy/2.0"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except URLError as e:
        print(f"API Error: {e}", file=sys.stderr)
        return None

def fetch_live_stats():
    """Fetch live-tournament-stats (has positions, SG data)."""
    url = f"{BASE_URL}/preds/live-tournament-stats?file_format=json&key={API_KEY}"
    return fetch_json(url)

def fetch_inplay():
    """Fetch in-play data (has today, thru, R1-R4, current_score)."""
    url = f"{BASE_URL}/preds/in-play?file_format=json&key={API_KEY}"
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
    # Try various key formats
    for key in [f"round_{round_num}", f"R{round_num}", f"r{round_num}"]:
        rd = player.get(key)
        if rd is not None:
            if isinstance(rd, dict):
                return rd.get("score")
            return rd
    return None

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
    return player.get("r2_teetime", "99:99")

def get_r3_teetime(player):
    """Extract R3 tee time for Rule 4 replacement player ordering.
    Rule 4 players are ordered by earliest Saturday (R3) tee time."""
    r3 = player.get("round_3")
    if isinstance(r3, dict):
        return r3.get("teetime", "99:99")
    return player.get("r3_teetime", "99:99")

def player_made_cut(player):
    """Check if player made the cut (has R3 data or make_cut flag)."""
    # Check make_cut field from in-play endpoint
    mc = player.get("make_cut")
    if mc is not None:
        return mc > 0.99  # >99% make cut means made it
    # Fallback: check R3 data
    r3 = player.get("round_3") or player.get("R3")
    return r3 is not None

def get_total_thru_2(player):
    """Get R1+R2 total."""
    r1 = get_round_score(player, 1)
    r2 = get_round_score(player, 2)
    if r1 is None or r2 is None:
        return None
    return r1 + r2

def merge_player_data(live_stats, inplay_data):
    """
    Merge data from live-tournament-stats and in-play endpoints.
    Returns combined player data with all available fields.
    """
    # Build lookup from in-play (has today/thru/R1-R4)
    inplay_lookup = {}
    for p in inplay_data:
        dg_id = p.get("dg_id")
        name = p.get("player_name", "")
        if dg_id:
            inplay_lookup[dg_id] = p
        if name:
            inplay_lookup[name.lower()] = p
    
    # Merge with live_stats (has positions/SG data)
    merged = []
    for p in live_stats:
        dg_id = p.get("dg_id")
        name = p.get("player_name", "")
        
        # Find matching in-play data
        ip = inplay_lookup.get(dg_id) or inplay_lookup.get(name.lower(), {})
        
        # Combine all fields
        combined = {**ip, **p}  # live_stats takes priority for overlapping keys
        
        # Add computed fields
        combined["today"] = ip.get("today", p.get("total", 0))
        combined["thru"] = ip.get("thru", p.get("thru", 0))
        combined["current_score"] = ip.get("current_score", p.get("total", 0))
        combined["position"] = p.get("position", ip.get("current_pos", ""))
        
        merged.append(combined)
    
    return merged

def process_tournament(live_data, inplay_data=None):
    """
    Process DataGolf tournament data into structured scoring data.
    Returns dict with event info, player scores, cut line, and replacement players.
    
    live_data: from fetch_live_stats()
    inplay_data: from fetch_inplay() (optional but recommended for live scoring)
    """
    # Get event info
    event_name = live_data.get("event_name", "Unknown Event")
    event_completed = live_data.get("event_completed")
    last_updated = live_data.get("last_updated", "")
    
    # Get scores - handle different response formats
    live_stats = live_data.get("live_stats") or live_data.get("scores") or live_data.get("data", [])
    inplay_scores = []
    if inplay_data:
        inplay_scores = inplay_data.get("data") or inplay_data.get("scores", [])
    
    # Merge data sources
    if inplay_scores:
        scores = merge_player_data(live_stats, inplay_scores)
    else:
        scores = live_stats
    
    if not scores:
        return {
            "event_name": event_name,
            "event_completed": False,
            "status": "pre",
            "current_round": 0,
            "course_par": 72,
            "cut_line": None,
            "replacement_players": [],
            "players": {},
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }
    
    # Determine event status
    # Check for in-progress or completed rounds
    has_thru = any(p.get("thru", 0) for p in scores)
    has_r1 = any(get_round_score(p, 1) is not None for p in scores)
    has_r2 = any(get_round_score(p, 2) is not None for p in scores)
    has_r3 = any(get_round_score(p, 3) is not None for p in scores)
    has_r4 = any(get_round_score(p, 4) is not None for p in scores)
    
    # Check what round players are currently playing (from in-play endpoint)
    current_playing_round = max((p.get("round", 0) or 0 for p in scores), default=0)
    
    # Determine current round and status
    if has_r4 and event_completed:
        status = "complete"
        current_round = 4
    elif has_r4 or current_playing_round == 4:
        status = "round4"
        current_round = 4
    elif has_r3 or current_playing_round == 3:
        status = "round3"
        current_round = 3
    elif has_r2 or current_playing_round == 2:
        status = "round2"
        current_round = 2
    elif has_r1 or current_playing_round == 1:
        status = "round1"
        current_round = 1
    elif has_thru:
        # Round 1 in progress (no R1 complete yet but players on course)
        status = "round1_live"
        current_round = 1
    else:
        status = "pre"
        current_round = 0
    
    # Get course par - use lookup table first, then try API data
    course_par = COURSE_PAR.get(event_name, 72)
    if course_par == 72:  # Not in lookup, try to get from player data
        for p in scores:
            cp = get_round_par(p, 1)
            if cp and cp != 72:
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
        
        # Find Rule 4 replacement players: 
        # - Made the cut
        # - R1+R2 total equals the cut line exactly
        # - Sorted by earliest R2 (Friday) tee time
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
            
            # Sort by R2 tee time (earliest first) - this is the Rule 4 order
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
        
        # For live scoring, use current_score or total
        today = p.get("today", 0) or 0
        thru = p.get("thru", 0) or 0
        current_score = p.get("current_score", p.get("total", 0)) or 0
        position = p.get("position", p.get("current_pos", ""))
        
        made_cut = player_made_cut(p)
        total_thru_2 = get_total_thru_2(p)
        mc_by = (total_thru_2 - cut_line) if (cut_line and not made_cut and total_thru_2) else 0
        
        # Calculate total strokes
        if r1 is not None:
            total = sum(x for x in [r1, r2, r3, r4] if x is not None)
        else:
            # Use live scoring (relative to par -> strokes)
            total = current_score + (course_par * thru // 18) if thru else None
        
        players[name] = {
            "name": name,
            "dg_id": p.get("dg_id"),
            "position": position,
            "fin_text": p.get("fin_text", ""),
            "r1": r1,
            "r2": r2,
            "r3": r3,
            "r4": r4,
            "total": total,
            "thru": thru,
            "today": today,
            "current_score": current_score,
            "made_cut": made_cut,
            "make_cut": p.get("make_cut"),  # Cut probability from in-play
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
        "last_updated": last_updated,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
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
        norm = normalize_name(key)
        lookup[norm] = pdata
        parts = key.split(",")
        if len(parts) == 2:
            last = parts[0].strip().lower()
            first = parts[1].strip().lower()
            lookup[f"{first} {last}"] = pdata
            lookup[last] = pdata
            if "." not in first:
                lookup[f"{first[0]}. {last}"] = pdata
    return lookup

def find_player(name, lookup):
    """Find a player in the lookup by name, trying various normalizations."""
    if "/" in name:
        name = name.split("/")[0].strip()
    
    n = normalize_name(name)
    
    if n in lookup:
        return lookup[n]
    
    clean = n.replace(".", "").replace("  ", " ").strip()
    if clean in lookup:
        return lookup[clean]
    
    parts = n.split()
    if len(parts) >= 2:
        last = parts[-1]
        if last in lookup:
            return lookup[last]
    
    for key, val in lookup.items():
        if all(part in key for part in n.split()):
            return val
        if all(part in n for part in key.split() if len(part) > 2):
            return val
    
    return None

def score_team(team, tournament_data, season_usage=None):
    """Score a single team for the current tournament."""
    players_dict = tournament_data["players"]
    lookup = build_name_lookup(players_dict)
    replacement_players = tournament_data["replacement_players"]
    cut_line = tournament_data["cut_line"]
    course_par = tournament_data["course_par"]
    status = tournament_data["status"]
    
    team_par = 4 * 4 * course_par
    scored_players = []
    mc_count = 0
    
    for i, player_name in enumerate(team["players"][:4]):
        pdata = find_player(player_name, lookup)
        
        if pdata is None:
            scored_players.append({
                "name": player_name,
                "slot": i + 1,
                "r1": None, "r2": None, "r3": None, "r4": None,
                "penalty": 0,
                "total": None,
                "status": "not_found",
                "replacement": None,
                "position": "",
                "thru": 0,
                "today": 0,
                "current_score": 0,
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
            player_status = "mc"
            penalty = pdata["mc_penalty"]
            mc_count += 1
            
            rep_idx = mc_count - 1
            if rep_idx < len(replacement_players):
                rep = replacement_players[rep_idx]
                r3 = rep["r3"]
                r4 = rep["r4"]
                replacement = rep["name"]
        
        # Calculate total - use current_score (to par) for live scoring
        if r1 is not None:
            # Round complete - use actual strokes
            total = sum(x for x in [r1, r2, r3, r4] if x is not None) + penalty
        else:
            # Live scoring - use current_score (to par)
            total = pdata.get("current_score", 0) or 0
        
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
            "position": pdata.get("position", ""),
            "fin_text": pdata.get("fin_text", ""),
            "today": pdata.get("today", 0),
            "thru": pdata.get("thru", 0),
            "current_score": pdata.get("current_score", 0),
            "make_cut": pdata.get("make_cut"),  # Cut probability for danger alerts
        })
    
    # Overuse penalties
    overuse_penalty = 0
    if season_usage:
        for p in team["players"][:4]:
            uses = season_usage.get(p, 0) + 1
            if uses in OVERUSE_PENALTY:
                overuse_penalty += OVERUSE_PENALTY[uses]
    
    # Team total - ALWAYS use current_score (to-par) during live tournament
    # current_score is the score relative to par, which is what we want for pool scoring
    if status in ["round1_live", "round1", "round2", "round3", "round4"]:
        # Live scoring - sum current_score (relative to par)
        team_total = sum(p.get("current_score", 0) or 0 for p in scored_players)
    else:
        # Tournament complete - use final strokes if available
        team_total = sum(p["total"] for p in scored_players if p["total"] is not None)
    
    team_total += overuse_penalty
    
    return {
        "owner": team["owner"],
        "alias": team.get("alias", ""),
        "players": scored_players,
        "team_total": team_total,
        "team_par": team_par,
        "team_to_par": team_total,  # During live, team_total IS to_par
        "overuse_penalty": overuse_penalty,
        "mc_count": mc_count,
    }


def build_leaderboard(rosters, tournament_data, season_data=None):
    """Score all teams and build the full leaderboard."""
    season_data = season_data or {}
    scored_teams = []
    rostered_owners = set()
    
    # Handle new season.json structure with "teams" key
    season_teams = season_data.get("teams", season_data)  # Fallback to old format
    cumulative_par = season_data.get("cumulative_par", 0)
    
    for team in rosters:
        owner = team["owner"]
        rostered_owners.add(owner)
        usage = season_teams.get(owner, {}).get("usage", {})
        scored = score_team(team, tournament_data, season_usage=usage)
        
        # Get previous season total (strokes)
        prev_total = season_teams.get(owner, {}).get("season_total", 0)
        prev_to_par = season_teams.get(owner, {}).get("season_to_par", 0)
        
        # Season points = previous strokes + current week strokes
        # During live play, team_total is to-par, so we need to convert
        course_par = tournament_data.get("course_par", 72)
        team_par = 4 * 4 * course_par  # 4 players x 4 rounds x course par
        
        if scored["team_total"] is not None:
            # For weekly total in strokes: team_par + to_par_score
            week_strokes = team_par + scored["team_to_par"]
            scored["season_pts"] = prev_total + week_strokes
            scored["season_to_par"] = prev_to_par + scored["team_to_par"]
            scored["week_strokes"] = week_strokes
        else:
            scored["season_pts"] = prev_total
            scored["season_to_par"] = prev_to_par
            scored["week_strokes"] = None
        scored["prev_pts"] = prev_total
        scored["prev_to_par"] = prev_to_par
        
        scored_teams.append(scored)
    
    # Include ALL teams from season_data that aren't in current rosters
    for owner, sdata in season_teams.items():
        if owner not in rostered_owners and isinstance(sdata, dict):
            prev_total = sdata.get("season_total", 0)
            prev_to_par = sdata.get("season_to_par", 0)
            alias = sdata.get("alias", "")
            scored_teams.append({
                "owner": owner,
                "alias": alias,
                "players": [],
                "team_total": None,
                "team_par": 4 * 4 * tournament_data["course_par"],
                "team_to_par": None,
                "week_strokes": None,
                "overuse_penalty": 0,
                "mc_count": 0,
                "season_pts": prev_total,
                "season_to_par": prev_to_par,
                "prev_pts": prev_total,
                "prev_to_par": prev_to_par,
            })
    
    # Sort by team_to_par (lowest first)
    scored_teams.sort(key=lambda x: (x["team_to_par"] if x["team_to_par"] is not None else 9999))
    
    for i, team in enumerate(scored_teams):
        team["weekly_rank"] = i + 1
    
    season_sorted = sorted(scored_teams, key=lambda x: x["season_pts"])
    for i, team in enumerate(season_sorted):
        team["season_rank"] = i + 1
    
    scored_teams.sort(key=lambda x: x["weekly_rank"])
    
    # Build live tournament leaderboard (all players sorted by score)
    live_players = []
    for name, p in tournament_data["players"].items():
        live_players.append({
            "name": name,
            "position": p.get("position", ""),
            "thru": p.get("thru", 0),
            "today": p.get("today", 0),
            "current_score": p.get("current_score", 0),
            "total": p.get("total"),
            "r1": p.get("r1"),
            "r2": p.get("r2"),
            "r3": p.get("r3"),
            "r4": p.get("r4"),
            "fin_text": p.get("fin_text", ""),
            "made_cut": p.get("made_cut", True),
            "make_cut": p.get("make_cut"),  # Cut probability from in-play endpoint
        })
    
    # Sort by current_score (to par), then by thru (more holes = higher priority), then name
    # Use 999 only for None, not for 0 (even par)
    def sort_key(x):
        score = x.get("current_score")
        if score is None:
            score = 999
        thru = x.get("thru") or 0
        # Negative thru puts players with more holes completed first among same score
        return (score, -thru, x.get("name", ""))
    
    live_players.sort(key=sort_key)
    
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
        "last_updated": tournament_data.get("last_updated", ""),
        "updated_at": tournament_data["updated_at"],
        "teams": scored_teams,
        "total_teams": len(scored_teams),
        "live_players": live_players,
    }


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="BirdieBuddy Pool Scoring Engine v2")
    parser.add_argument("--historical", type=int, help="Fetch historical event by ID")
    parser.add_argument("--output", default="data/leaderboard.json", help="Output JSON path")
    parser.add_argument("--rosters", default="data/rosters.json", help="Rosters JSON path")
    parser.add_argument("--season", default="data/season.json", help="Season data JSON path")
    args = parser.parse_args()
    
    if args.historical:
        print(f"Fetching historical event {args.historical}...")
        dg_data = fetch_historical(args.historical)
        inplay_data = None
    else:
        print("Fetching live tournament data...")
        dg_data = fetch_live_stats()
        inplay_data = fetch_inplay()
    
    if not dg_data:
        print("Failed to fetch data", file=sys.stderr)
        sys.exit(1)
    
    print(f"Event: {dg_data.get('event_name', 'Unknown')}")
    print(f"Last updated: {dg_data.get('last_updated', 'N/A')}")
    
    tournament = process_tournament(dg_data, inplay_data)
    print(f"Status: {tournament['status']}, Round: {tournament['current_round']}")
    print(f"Players loaded: {len(tournament['players'])}")
    
    # Sample player check
    sample_players = list(tournament['players'].values())[:3]
    for p in sample_players:
        print(f"  {p['name']}: pos={p.get('position')}, thru={p.get('thru')}, today={p.get('today')}")
    
    # Load rosters
    rosters_path = args.rosters
    if os.path.exists(rosters_path):
        with open(rosters_path) as f:
            rosters = json.load(f)
    else:
        print(f"No rosters file at {rosters_path}. Generating tournament-only output.")
        with open(args.output, "w") as f:
            json.dump(tournament, f, indent=2)
        print(f"Wrote tournament data to {args.output}")
        return
    
    season_data = {}
    if os.path.exists(args.season):
        with open(args.season) as f:
            season_data = json.load(f)
    
    leaderboard = build_leaderboard(rosters, tournament, season_data)
    
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(leaderboard, f, indent=2)
    
    print(f"\nLeaderboard: {leaderboard['total_teams']} teams")
    leader = leaderboard['teams'][0]
    leader_score = leader.get('team_to_par')
    if leader_score is not None:
        print(f"Leader: {leader['owner']} ({leader_score:+d})")
    else:
        print(f"Leader: {leader['owner']} (pre-tournament)")
    print(f"Written to {args.output}")


if __name__ == "__main__":
    main()




