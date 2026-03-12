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
# FORCED EVENT OVERRIDE - Set to None to use DataGolf's event detection
# Update this when DataGolf is slow to switch events
FORCE_EVENT_NAME = "THE PLAYERS Championship"
FORCE_COURSE_PAR = 72  # TPC Sawgrass
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
    # Build lookup from in-play (has today/thru/R1-R4/round)
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
        
        # Combine all fields - live_stats takes priority except for key in-play fields
        combined = {**ip, **p}
        
        # Preserve key fields from in-play that might get overwritten
        combined["round"] = ip.get("round") or p.get("round", 0)
        combined["today"] = ip.get("today", p.get("total", 0))
        combined["thru"] = ip.get("thru", p.get("thru", 0))
        combined["current_score"] = ip.get("current_score", p.get("total", 0))
        combined["position"] = p.get("position", ip.get("current_pos", ""))
        combined["top_10"] = ip.get("top_10", p.get("top_10"))
        combined["make_cut"] = ip.get("make_cut", p.get("make_cut"))
        
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
    
    # Apply forced event override if set
    if FORCE_EVENT_NAME:
        event_name = FORCE_EVENT_NAME
    
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
    
    # Get course par - use forced override, then lookup table, then API data
    if FORCE_COURSE_PAR:
        course_par = FORCE_COURSE_PAR
    else:
        course_par = COURSE_PAR.get(event_name, 72)
        if course_par == 72:  # Not in lookup, try to get from player data
            for p in scores:
                cp = get_round_par(p, 1)
                if cp and cp != 72:
                    course_par = cp
                    break
    
    # Find cut line
    # Detect cut when R2 is fully complete OR R3 has started (players in round 3)
    cut_line = None
    replacement_players = []
    
    # Check if R2 is fully complete
    players_with_r1 = [p for p in scores if get_round_score(p, 1) is not None]
    players_with_r2 = [p for p in scores if get_round_score(p, 2) is not None]
    
    # R2 is complete if: we have R2 scores AND count matches R1 count (everyone finished R2)
    # Also check no one is mid-round (thru < 18 in current round 2)
    r2_complete = (
        len(players_with_r2) > 0 and 
        len(players_with_r2) >= len(players_with_r1) * 0.95 and  # At least 95% have R2
        current_round >= 2 and
        not any(p.get("thru", 18) < 18 and p.get("round") == 2 for p in scores)
    )
    
    # Cut is made if R3 has started (either has scores OR players are in round 3)
    r3_started = has_r3 or current_playing_round >= 3
    cut_is_made = r3_started or r2_complete
    
    # WD OVERRIDES: Players who withdrew mid-tournament
    # Must be defined OUTSIDE cut_is_made block so WDs before cut are handled
    # Format: {event: {wd_player: {r1, r2, replacement, use_rule4}}}
    # - r1, r2: The WD player's completed round scores (None if WD before starting)
    # - replacement: The alternate who fills remaining rounds
    WD_OVERRIDES = {
        "THE PLAYERS Championship": {
            "Morikawa, Collin": {"r1": None, "r2": None, "thru": 1, "replacements": {
                "Lou Boss": "Hovland, Viktor",
                "Mark Dowling": "Bridgeman, Jacob",
                "Rusty Hurst": "Matsuyama, Hideki",
                "The A-Team": "Scheffler, Scottie",
                "The Wolf Pack": "Matsuyama, Hideki",
                "The Scottish Lion": "Gotterup, Chris",
                "Pat Devine": "Kim, Si Woo",
                "The Hammer": "Berger, Daniel",
                "Mary Beth Scimia": "Theegala, Sahith",
                "Brett Armstrong": "Straka, Sepp",
                "John Stadler": "Theegala, Sahith",
                "B. Reid": "Knapp, Jake",
                "Bob Fabian": "Straka, Sepp",
                "Brian Little": "Scheffler, Scottie",
                "The Roman Goddess": "Berger, Daniel",
                "Coach Len": "Theegala, Sahith",
                "Jack Gawronski": "Scott, Adam",
                "Brian Belcer": "Fowler, Rickie",
                "Buzz Biddle": "Conners, Corey",
                "Kelly Murray": "McIlroy, Rory",
                "Dr. J & Mr. T": "Matsuyama, Hideki",
                "Brendan Ball": "Aberg, Ludvig",
                "The Minister & The Wet Dog": "Fowler, Rickie",
                "Rob Mignoli": "McIlroy, Rory",
                "Joe Kapa": "Hoge, Tom",
                "Joe Mooney": "Straka, Sepp",
                "Zackie Robison": "Aberg, Ludvig",
                "Brandon Ambrose": "McNealy, Maverick",
                "Peter Motrynczuk": "Fleetwood, Tommy",
                "Justin Gentzke": "Young, Cameron",
                "Kevin F'n Cleary": "Bhatia, Akshay",
                "John Cleary": "Scott, Adam",
                "Jim Templeton": "Henley, Russell",
                "Rob Kerr": "Schauffele, Xander",
                "Dino": "Kim, Si Woo",
                "Greg Witter": "Knapp, Jake",
                "Bill Tatu": "Young, Cameron",
                "Walt Lemiski": "Young, Cameron",
                "Jaime Witter": "Scheffler, Scottie",
                "Nick Montaldi": "Fitzpatrick, Matt",
                "Kevin Gallivan": "Hovland, Viktor",
                "Bubs Regan": "Kim, Si Woo",
                "Pete & Linda": "Bhatia, Akshay",
                "Brendan Cohen": "Scheffler, Scottie",
                "Rob Motrynczuk": "Conners, Corey",
                "Gerry Kirchofer": "Schauffele, Xander",
                "Frank Delsignore": "Straka, Sepp",
                "Jason Goss": "Aberg, Ludvig",
                "Dominic Montaldi": "Lee, Min Woo",
                "Billy Coppola": "Young, Cameron",
                "Matt Donohue": "Berger, Daniel",
                "Chris Wysocki": "Kim, Si Woo",
                "Nate Marini": "Scheffler, Scottie",
                "Bill Moore": "McIlroy, Rory",
                "JP Morgan": "Scheffler, Scottie",
                "Will Lawhon": "Berger, Daniel",
                "Tom Stadler": "Young, Cameron",
                "Quentin Bubb": "Kim, Si Woo",
                "Maxwell Smart": "Greyserman, Max",
                "Andy Kapusta": "McIlroy, Rory",
                "Vince Montaldi": "Straka, Sepp",
                "Chuck Allen": "Homa, Max",
                "Ethan Brady": "McIlroy, Rory",
                "Matt White": "Henley, Russell",
                "Eric Southard": "Bhatia, Akshay",
                "Don Gleason": "Theegala, Sahith",
                "Gabe Palen": "Schauffele, Xander",
            }},
        },
    }
    
    # Store WD overrides for use in score_team
    if event_name in WD_OVERRIDES:
        wd_data = WD_OVERRIDES[event_name]
    else:
        wd_data = {}
    
    if cut_is_made:
        made_cut_totals = []
        for p in scores:
            # Player made cut if: has R3 data OR make_cut > 99%
            made = player_made_cut(p) or p.get("make_cut", 0) > 0.99
            if made:
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
            
            # HARDCODED OVERRIDE: When tee times aren't available (all "99:99"),
            # use commissioner-confirmed Rule 4 order for specific tournaments
            RULE4_OVERRIDES = {
                "WM Phoenix Open": ["Kim, S.H.", "Poston, J.T.", "Morikawa, Collin"],
                "Genesis Invitational": ["Stevens, Sam", "Hisatsune, Ryo", "Harman, Brian", "Cantlay, Patrick", "MacIntyre, Robert"],
                "Arnold Palmer Invitational": ["Glover, Lucas", "Pendrith, Taylor"],
            }
            
            
            if event_name in RULE4_OVERRIDES:
                override_order = RULE4_OVERRIDES[event_name]
                # Reorder cut_line_players based on override
                ordered = []
                for name in override_order:
                    for p in cut_line_players:
                        if p["name"] == name:
                            ordered.append(p)
                            break
                # Add any remaining players not in override
                for p in cut_line_players:
                    if p not in ordered:
                        ordered.append(p)
                replacement_players = ordered
    
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
            "round": p.get("round", 0),  # Current round player is in
            "current_score": current_score,
            "made_cut": made_cut,
            "make_cut": p.get("make_cut"),  # Cut probability from in-play
            "top_10": p.get("top_10"),  # Top 10 probability from in-play
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
        "wd_overrides": wd_data if 'wd_data' in dir() else {},
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
    wd_overrides = tournament_data.get("wd_overrides", {})
    
    team_par = 4 * 4 * course_par
    scored_players = []
    mc_count = 0
    wd_count = 0  # Track WD players for Rule 4 assignment
    team_owner = team.get("owner", "")
    
    for i, player_name in enumerate(team["players"][:4]):
        pdata = find_player(player_name, lookup)
        
        # Check if this player slot should use WD override
        wd_info = None
        normalized_player = normalize_name(player_name)
        for wd_player, wd_data in wd_overrides.items():
            normalized_wd = normalize_name(wd_player)
            # Check if names match (handles "Rory McIlroy" vs "McIlroy, Rory")
            if normalized_player == normalized_wd or wd_player in player_name or player_name in wd_player:
                # This roster slot originally had a WD player
                replacements = wd_data.get("replacements", {})
                if team_owner in replacements:
                    wd_info = {
                        "wd_player": wd_player,
                        "r1": wd_data["r1"],
                        "r2": wd_data["r2"],
                        "replacement": replacements[team_owner],
                    }
                    break
        
        # Also check if current player_name is an alternate for a WD player
        if wd_info is None:
            for wd_player, wd_data in wd_overrides.items():
                replacements = wd_data.get("replacements", {})
                if team_owner in replacements:
                    rep_name = replacements[team_owner]
                    # Check if current player is the replacement
                    if rep_name != "RULE4" and (rep_name in player_name or player_name in rep_name or 
                        normalize_name(player_name) == normalize_name(rep_name)):
                        wd_info = {
                            "wd_player": wd_player,
                            "r1": wd_data["r1"],
                            "r2": wd_data["r2"],
                            "replacement": rep_name,
                        }
                        break
        
        if pdata is None and wd_info is None:
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
        
        # Handle WD player replacement
        if wd_info:
            wd_r1 = wd_info["r1"]
            wd_r2 = wd_info["r2"]
            rep_name = wd_info["replacement"]
            wd_player = wd_info["wd_player"]
            
            # Check if this is a pre-tournament WD (r1 is None or partial)
            # In this case, alternate takes ALL rounds
            is_pre_tournament_wd = wd_r1 is None
            
            # Calculate WD player's R1+R2 to par (0 if pre-tournament WD)
            if is_pre_tournament_wd:
                wd_to_par = 0
            else:
                wd_to_par = ((wd_r1 or 0) - course_par) + ((wd_r2 or 0) - course_par)
            
            if rep_name == "RULE4":
                # Use Rule 4 player for R3+R4, no penalty for WD
                wd_count += 1
                rep_idx = wd_count - 1
                if rep_idx < len(replacement_players):
                    rep = replacement_players[rep_idx]
                    rep_data = find_player(rep["name"], lookup)
                    if rep_data:
                        rep_r3 = rep_data.get("r3") or 0
                        rep_r4 = rep_data.get("r4") or 0
                        rep_today = rep_data.get("today") or 0
                        rep_thru = rep_data.get("thru") or 0
                        
                        if rep_r3 and rep_r4:
                            rep_to_par = (rep_r3 - course_par) + (rep_r4 - course_par)
                        elif rep_r3:
                            rep_to_par = (rep_r3 - course_par) + rep_today
                        else:
                            rep_to_par = rep_today
                        
                        current_score = wd_to_par + rep_to_par
                        
                        # Convert WD player name from "Last, First" to "First Last" for display
                        wd_display_name = wd_player
                        if ", " in wd_player:
                            parts = wd_player.split(", ")
                            wd_display_name = f"{parts[1]} {parts[0]}"
                        
                        scored_players.append({
                            "name": wd_display_name,  # Show WD player name (Rory McIlroy)
                            "dg_name": wd_player,
                            "slot": i + 1,
                            "r1": wd_r1, "r2": wd_r2, "r3": rep_r3 or None, "r4": rep_r4 or None,
                            "penalty": 0,  # No penalty for WD with Rule 4
                            "total": wd_r1 + wd_r2 + (rep_r3 or 0) + (rep_r4 or 0),
                            "status": "wd_rule4",
                            "replacement": rep["name"],
                            "position": "WD",
                            "thru": rep_thru,
                            "today": rep_today,
                            "current_score": current_score,
                        })
                        continue
            elif rep_name.startswith("RULE4_MC_"):
                # WD player's alternate also missed cut - Rule 4 + MC penalty
                # Format: "RULE4_MC_7" means +7 penalty (MC by 3 strokes)
                penalty = int(rep_name.split("_")[-1])
                wd_count += 1
                rep_idx = wd_count - 1
                if rep_idx < len(replacement_players):
                    rep = replacement_players[rep_idx]
                    rep_data = find_player(rep["name"], lookup)
                    if rep_data:
                        rep_r3 = rep_data.get("r3") or 0
                        rep_r4 = rep_data.get("r4") or 0
                        rep_today = rep_data.get("today") or 0
                        rep_thru = rep_data.get("thru") or 0
                        
                        if rep_r3 and rep_r4:
                            rep_to_par = (rep_r3 - course_par) + (rep_r4 - course_par)
                        elif rep_r3:
                            rep_to_par = (rep_r3 - course_par) + rep_today
                        else:
                            rep_to_par = rep_today
                        
                        current_score = wd_to_par + rep_to_par + penalty
                        
                        # Convert WD player name from "Last, First" to "First Last" for display
                        wd_display_name = wd_player
                        if ", " in wd_player:
                            parts = wd_player.split(", ")
                            wd_display_name = f"{parts[1]} {parts[0]}"
                        
                        scored_players.append({
                            "name": wd_display_name,
                            "dg_name": wd_player,
                            "slot": i + 1,
                            "r1": wd_r1, "r2": wd_r2, "r3": rep_r3 or None, "r4": rep_r4 or None,
                            "penalty": penalty,  # MC penalty from alternate
                            "total": (wd_r1 or 0) + (wd_r2 or 0) + (rep_r3 or 0) + (rep_r4 or 0) + penalty,
                            "status": "wd_alt_mc",
                            "replacement": rep["name"],
                            "position": "WD",
                            "thru": rep_thru,
                            "today": rep_today,
                            "current_score": current_score,
                        })
                        continue
            else:
                # Use alternate for remaining rounds
                # For pre-tournament WD: alternate takes ALL 4 rounds
                # For mid-tournament WD: alternate takes R3+R4 only
                rep_data = find_player(rep_name, lookup)
                if rep_data:
                    # For pre-tournament WD, use alternate's R1+R2 as well
                    if is_pre_tournament_wd:
                        rep_r1 = rep_data.get("r1") or 0
                        rep_r2 = rep_data.get("r2") or 0
                        rep_r3 = rep_data.get("r3") or 0
                        rep_r4 = rep_data.get("r4") or 0
                        rep_today = rep_data.get("today") or 0
                        rep_thru = rep_data.get("thru") or 0
                        
                        # Calculate to-par based on what's available
                        if rep_r1 and rep_r2 and rep_r3 and rep_r4:
                            rep_to_par = (rep_r1 - course_par) + (rep_r2 - course_par) + (rep_r3 - course_par) + (rep_r4 - course_par)
                        elif rep_r1 and rep_r2 and rep_r3:
                            rep_to_par = (rep_r1 - course_par) + (rep_r2 - course_par) + (rep_r3 - course_par) + rep_today
                        elif rep_r1 and rep_r2:
                            rep_to_par = (rep_r1 - course_par) + (rep_r2 - course_par) + rep_today
                        elif rep_r1:
                            rep_to_par = (rep_r1 - course_par) + rep_today
                        else:
                            rep_to_par = rep_today
                        
                        current_score = rep_to_par  # Alternate's full score
                        
                        # Convert WD player name from "Last, First" to "First Last" for display
                        wd_display_name = wd_player
                        if ", " in wd_player:
                            parts = wd_player.split(", ")
                            wd_display_name = f"{parts[1]} {parts[0]}"
                        
                        scored_players.append({
                            "name": wd_display_name,  # Show WD player name (Collin Morikawa)
                            "dg_name": wd_player,
                            "slot": i + 1,
                            "r1": rep_r1 or None, "r2": rep_r2 or None, "r3": rep_r3 or None, "r4": rep_r4 or None,
                            "penalty": 0,  # No penalty for pre-tournament WD
                            "total": (rep_r1 or 0) + (rep_r2 or 0) + (rep_r3 or 0) + (rep_r4 or 0),
                            "status": "wd_alt",
                            "replacement": rep_name,  # Show alternate name
                            "position": "WD",
                            "thru": rep_thru,
                            "today": rep_today,
                            "current_score": current_score,
                        })
                        continue
                    else:
                        # Mid-tournament WD: alternate takes R3+R4 only
                        rep_r3 = rep_data.get("r3") or 0
                        rep_r4 = rep_data.get("r4") or 0
                        rep_today = rep_data.get("today") or 0
                        rep_thru = rep_data.get("thru") or 0
                        
                        if rep_r3 and rep_r4:
                            rep_to_par = (rep_r3 - course_par) + (rep_r4 - course_par)
                        elif rep_r3:
                            rep_to_par = (rep_r3 - course_par) + rep_today
                        else:
                            rep_to_par = rep_today
                        
                        current_score = wd_to_par + rep_to_par
                        
                        # Convert WD player name from "Last, First" to "First Last" for display
                        wd_display_name = wd_player
                        if ", " in wd_player:
                            parts = wd_player.split(", ")
                            wd_display_name = f"{parts[1]} {parts[0]}"
                        
                        scored_players.append({
                            "name": wd_display_name,  # Show WD player name (Rory McIlroy)
                            "dg_name": wd_player,
                            "slot": i + 1,
                            "r1": wd_r1, "r2": wd_r2, "r3": rep_r3 or None, "r4": rep_r4 or None,
                            "penalty": 0,  # No penalty for WD with alternate
                            "total": (wd_r1 or 0) + (wd_r2 or 0) + (rep_r3 or 0) + (rep_r4 or 0),
                            "status": "wd_alt",
                            "replacement": rep_name,  # Show alternate name (Henley, Russell)
                            "position": "WD",
                            "thru": rep_thru,
                            "today": rep_today,
                            "current_score": current_score,
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
            
            # MC player's current_score should be their actual tournament score (R1+R2 to par)
            # The replacement's score is only used in team total calculation, NOT for individual display
            mc_r1r2_to_par = (pdata["r1"] - course_par) + (pdata["r2"] - course_par) if pdata["r1"] and pdata["r2"] else 0
            pdata["current_score"] = mc_r1r2_to_par
            pdata["today"] = 0
            pdata["thru"] = 18  # They finished R2
            
            rep_idx = mc_count - 1
            if rep_idx < len(replacement_players):
                rep = replacement_players[rep_idx]
                r3 = rep["r3"]
                r4 = rep["r4"]
                replacement = rep["name"]
                # Note: We store the replacement name but do NOT modify current_score
                # The replacement's contribution is calculated in team total only
        
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
            "top_10": pdata.get("top_10"),  # Top 10 probability for elevated events
        })
    
    # Overuse penalties
    overuse_penalty = 0
    if season_usage:
        for p in team["players"][:4]:
            uses = season_usage.get(p, 0) + 1
            if uses in OVERUSE_PENALTY:
                overuse_penalty += OVERUSE_PENALTY[uses]
    
    # Team total calculation
    # During live play, use current_score (to-par) from DataGolf for active players
    # For MC players, calculate from stored R1-R4 + penalty (current_score doesn't include Rule 4)
    
    # Check if any player has started (thru > 0 or has round scores)
    any_started = any(
        (p.get("thru") or 0) > 0 or 
        (p.get("r1") is not None and p.get("r1") > 0)
        for p in scored_players
    )
    
    if any_started:
        # Live scoring - use current_score for active players, calculate for MC
        team_total = 0
        for p in scored_players:
            if p.get("status") == "mc":
                # MC player - calculate from their R1+R2 + replacement's R3/R4 + penalty
                r1 = p.get("r1") or 0
                r2 = p.get("r2") or 0
                pen = p.get("penalty") or 0
                
                # Get replacement player's data
                rep_name = p.get("replacement")
                rep_r3 = 0
                rep_r4 = 0
                rep_today = 0
                
                if rep_name:
                    # Look up replacement in players_dict
                    rep_data = None
                    for key, val in players_dict.items():
                        if rep_name in key or key in rep_name:
                            rep_data = val
                            break
                    
                    if rep_data:
                        rep_r3 = rep_data.get("r3") or 0
                        rep_r4 = rep_data.get("r4") or 0
                        # Always get today score for live R3 or R4 tracking
                        rep_today = rep_data.get("today") or 0
                
                # Calculate to-par
                # R1 + R2 to par (MC player)
                mc_to_par = (r1 - course_par) + (r2 - course_par) if r1 and r2 else 0
                
                # R3 + R4 to par (replacement) - if rounds complete, use actual; otherwise use today
                if rep_r3 and rep_r4:
                    rep_to_par = (rep_r3 - course_par) + (rep_r4 - course_par)
                elif rep_r3:
                    rep_to_par = (rep_r3 - course_par) + rep_today
                else:
                    rep_to_par = rep_today  # R3 in progress
                
                # Total = MC's R1+R2 + replacement's R3/R4 + penalty
                team_total += mc_to_par + rep_to_par + pen
            elif p.get("status") in ["wd_alt", "wd_rule4", "wd_alt_mc"]:
                # WD player - current_score already calculated correctly (WD R1+R2 + rep R3+R4 + any penalty)
                team_total += p.get("current_score") or 0
            else:
                # Active player - use current_score (already to-par)
                team_total += p.get("current_score") or 0
    else:
        # No one started yet - team is at even par (0)
        team_total = 0
    
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
        
        # Add investigation tags for specific teams (joke/tracking)
        INVESTIGATION_TAGS = {
            # Add any teams under investigation here
        }
        if owner in INVESTIGATION_TAGS:
            scored["investigation"] = INVESTIGATION_TAGS[owner]
        
        # Get previous season total (strokes)
        prev_total = season_teams.get(owner, {}).get("season_total", 0)
        prev_to_par = season_teams.get(owner, {}).get("season_to_par", 0)
        
        # Season points calculation
        # During live play, use to-par for accurate tracking
        # Only convert to strokes when we have complete round data
        course_par = tournament_data.get("course_par", 72)
        team_par = 4 * 4 * course_par  # 4 players x 4 rounds x course par
        
        # Check if team has any actual scoring data
        # (not just thru > 0, but actual current_score from players)
        has_live_scores = scored["team_to_par"] is not None and scored["team_to_par"] != 0
        
        # Also check if any player has started with actual score data
        any_player_with_score = any(
            p.get("current_score") is not None and p.get("current_score") != 0
            for p in scored.get("players", [])
        )
        
        # For teams that have started and have actual scores, update season live
        if has_live_scores or any_player_with_score:
            # Live season calculation - both to-par AND strokes update live
            # season_to_par = prev_to_par + current_week_to_par
            scored["season_to_par"] = prev_to_par + (scored["team_to_par"] or 0)
            # season_pts = prev_pts + estimated_week_strokes
            # estimated_week_strokes = team_par + team_to_par (e.g., 1152 + (-8) = 1144)
            week_strokes_estimate = team_par + (scored["team_to_par"] or 0)
            scored["season_pts"] = prev_total + week_strokes_estimate
            scored["week_strokes"] = week_strokes_estimate
        else:
            # No scores yet - show projected season totals (assuming even par for the week)
            # This prevents teams that haven't started from appearing ahead of those who have
            scored["season_pts"] = prev_total + team_par  # Assume even par until they start
            scored["season_to_par"] = prev_to_par  # To-par stays the same until they have scores
            scored["week_strokes"] = None
        
        scored["prev_pts"] = prev_total
        scored["prev_to_par"] = prev_to_par
        
        scored_teams.append(scored)
    
    # NOTE: We intentionally do NOT add teams from season_data that aren't in current rosters.
    # This was causing bugs where old teams (like Jeff W Chamberlain) would reappear.
    # Only teams in the current week's rosters.json should be included.
    
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
            "top_10": p.get("top_10"),  # Top 10 probability from in-play endpoint
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
            rosters_raw = json.load(f)
        # Support both dict {owner: {starters, alternates}} and list [{owner, players, alternates}]
        if isinstance(rosters_raw, dict):
            rosters = []
            for owner, data in rosters_raw.items():
                rosters.append({
                    "owner": owner,
                    "players": data.get("starters", []),
                    "alternates": data.get("alternates", [])
                })
        else:
            rosters = rosters_raw
    else:
        print(f"No rosters file at {rosters_path}. Generating tournament-only output.")
        with open(args.output, "w") as f:
            json.dump(tournament, f, indent=2)
        print(f"Wrote tournament data to {args.output}")
        return
    
    # Load previous season data from season.json
    season_data = {}
    season_json_path = args.season  # Use the --season argument (default: data/season.json)
    if os.path.exists(season_json_path):
        try:
            with open(season_json_path) as f:
                season_file = json.load(f)
                # season.json has structure: {"cumulative_par": X, "teams": {owner: {season_total, season_to_par, ...}}}
                season_data = season_file
        except:
            pass  # If can't read, start fresh
    
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




