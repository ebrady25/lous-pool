---
name: pool-scoring
description: Golf pool scoring calculator and leaderboard updater for Ethan's 100-person competitive pool. Use when processing weekly tournament results, calculating team scores, applying Rule 4 missed-cut penalties, updating the BirdieBuddy leaderboard, or analyzing pool standings. Triggers on requests involving pool scoring, missed cut calculations, Rule 4 replacements, weekly results processing, leaderboard updates, season standings, or pool strategy related to scoring mechanics.
---

# Pool Scoring System

## Quick Reference

### GitHub Repository
- **Repo**: https://github.com/ebrady25/lous-pool
- **Live Site**: https://ebrady25.github.io/lous-pool/
- **PAT**: See memory for current token

### DataGolf API
- **Key**: `576a75cc2c5275542b9b9d98419b`
- **Subscription**: $50/month

## Pool Structure
- 100 participants, 15-week season, $1,500 prize pool
- 6 majors at $150 (Players, Masters, PGA, US Open, Open Championship) + 9 regular at $75
- 4 starters + 2 alternates per week, listed in rank order (tiebreaker)
- 3-use max per player per season. Overuse penalties: 4th +10, 5th +15, 6th +20
- Lowest cumulative strokes wins season. Lowest weekly score wins that week's prize

## Live Leaderboard Architecture

```
DataGolf API (live-tournament-stats + in-play)
    ↓ (server-side fetch every 5 min)
score_engine.py (GitHub Actions)
    ↓ (writes + deploys)
leaderboard.json (GitHub Pages)
    ↓ (browser fetch with cache-busting)
index.html (React-ish frontend)
```

### Key Files
| File | Location | Purpose |
|------|----------|---------|
| `score_engine.py` | `scripts/` | Fetches DataGolf, scores teams, outputs JSON |
| `index.html` | `public/` | Frontend leaderboard |
| `leaderboard.json` | `public/data/` | Current tournament state + live_players |
| `rosters.json` | `data/` | All 100 teams for current week |
| `season.json` | `data/` | Cumulative season standings |
| `update-leaderboard.yml` | `.github/workflows/` | Auto-update every 5 min during tournaments |

### Critical Implementation Details

**CORS Issue**: Frontend cannot call DataGolf directly (blocked by CORS). All DataGolf calls happen server-side in GitHub Actions. Frontend only reads from `leaderboard.json`.

**Live Scoring**: During active rounds, use `current_score` (to-par), NOT `total` (strokes). The `R1`/`R2`/`R3`/`R4` fields are null until rounds complete.

**Cache Busting**: Fetch calls use `cache: 'no-store'` and `?t=Date.now()` to prevent stale data.

**Auto-Deploy**: The update-leaderboard workflow includes Pages deployment steps so changes go live immediately.

## Automated Round-Based Display

The Live PGA Leaderboard automatically shows different columns based on the current round:

### R1 & R2 (Before Cut)
| # | Player | Total | R1/R2 | MC% |
|---|--------|-------|-------|-----|

- **Total**: Overall tournament score (to-par) with green highlight
- **R1/R2**: Current round score with holes thru, e.g., "-2 (8)"
- **MC%**: Make Cut probability from DataGolf
  - 🟢 75%+ = green
  - 🟢 50-75% = light green
  - 🟡 30-50% = gold
  - 🔴 <30% = red

### R3 & R4 (After Cut)
| # | Player | Total | R3/R4 | T10% |
|---|--------|-------|-------|------|

- **Total**: Overall tournament score (to-par) with green highlight
- **R3/R4**: Current round score with holes thru
- **T10%**: Top 10 finish probability from DataGolf
  - 🟢 50%+ = green
  - 🟢 25-50% = light green
  - 🟡 10-25% = gold
  - ⚪ <10% = gray

## Scoring: Relative to Par

All scores display relative to par during live play, not raw strokes.

**Team par per week** = 4 players × 4 rounds × course par per round.
Example: Phoenix Open (par 71) → team par = 4 × 4 × 71 = 1136.

**Season par** = sum of all completed weeks' team pars.

## Rule 4: Missed Cut Mechanics (AUTOMATED)

The system automatically detects and processes missed cuts:

### Automated Cut Detection
Cut is detected when EITHER:
1. R3 data exists (players on course in R3), OR
2. DataGolf `make_cut` probabilities are definitive (>99% made OR <1% missed)

This allows MC marking to happen as soon as R2 ends, before R3 starts.

### Automated Rule 4 Player Selection
Rule 4 replacement players are automatically selected using these criteria:
1. **Made the cut** (make_cut > 99%)
2. **R1+R2 total equals the cut line exactly** (lowest qualifiers)
3. **Sorted by earliest R2 (Friday) tee time** (first to finish = Rule 4 #1)

The system uses the `field-updates` endpoint to get R2 tee times for proper ordering.

### 1. R3/R4 Replacement
MC players keep their actual R1+R2 scores. R3 and R4 are replaced by scores from Rule 4 players.

**Multiple MC players on one team:** Each gets a DIFFERENT replacement player, assigned by **team listing order**:
- 1st MC player listed → Rule 4 #1's R3/R4
- 2nd MC player listed → Rule 4 #2's R3/R4
- 3rd MC player listed → Rule 4 #3's R3/R4

### 2. Stroke Penalty
```
MC by 1 stroke  → +5 penalty
MC by 2 strokes → +6 penalty
MC by 3 strokes → +7 penalty
MC by 4+ strokes → +8 penalty
```

Penalties are automatically calculated from `cut_line - player_total_thru_2`.

## Round Detection Logic

The `score_engine.py` determines the current round by checking:

```python
# From DataGolf in-play endpoint
current_playing_round = max(p.get("round", 0) for p in scores)

# Round status determination (in priority order)
if has_r4 and event_completed: status = "complete"
elif has_r4 or current_playing_round == 4: status = "round4"
elif has_r3 or current_playing_round == 3: status = "round3"
elif has_r2 or current_playing_round == 2: status = "round2"
elif has_r1 or current_playing_round == 1: status = "round1"
```

**Important**: The `round` field from in-play data must be preserved during data merge (not overwritten by live_stats).

## Manual Workflow Triggers

**Trigger leaderboard update:**
```bash
curl -X POST -H "Authorization: token $GH_TOKEN" \
  "https://api.github.com/repos/ebrady25/lous-pool/actions/workflows/update-leaderboard.yml/dispatches" \
  -d '{"ref":"main"}'
```

**Push file to GitHub:**
```bash
CONTENT=$(base64 -w 0 file.py)
SHA=$(curl -s -H "Authorization: token $GH_TOKEN" \
  "https://api.github.com/repos/ebrady25/lous-pool/contents/path/file.py" | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('sha',''))")
curl -X PUT -H "Authorization: token $GH_TOKEN" \
  "https://api.github.com/repos/ebrady25/lous-pool/contents/path/file.py" \
  -d "{\"message\":\"commit msg\",\"content\":\"$CONTENT\",\"sha\":\"$SHA\"}"
```

## Troubleshooting

**Leaderboard shows stale data:**
1. Check GitHub Actions ran: https://github.com/ebrady25/lous-pool/actions
2. Manually trigger update-leaderboard workflow
3. Hard refresh browser (Ctrl+Shift+R)

**Scores showing strokes instead of to-par:**
- Ensure score_engine.py uses `current_score` not `total` during live play
- Check status is one of: round1_live, round1, round2, round3, round4

**CORS errors:**
- Frontend should ONLY fetch from leaderboard.json
- Never call DataGolf directly from browser

**Round not detected correctly:**
- Check that `round` field is preserved in `merge_player_data()` function
- The in-play endpoint provides the `round` field, must not be overwritten

**MC players not marked:**
- Check `make_cut` probabilities from DataGolf (should be 0% or 100% after R2)
- Verify cut_line is being calculated
- Check cut detection logic triggers on definitive make_cut values

**Rule 4 players incorrect:**
- Verify R2 tee times are being fetched from field-updates endpoint
- Check players are sorted by `r2_teetime` ascending (earliest first)

## 2026 Season Tracking

| Week | Event | Par | Team Par | Season Par |
|------|-------|-----|----------|------------|
| 1 | Farmers Insurance Open | 72 | 1152 | 1152 |
| 2 | WM Phoenix Open | 71 | 1136 | 2288 |

## Course Par Lookup

The score_engine includes a `COURSE_PAR` lookup table for accurate par values:

```python
COURSE_PAR = {
    "WM Phoenix Open": 71,  # TPC Scottsdale
    "Farmers Insurance Open": 72,
    "The American Express": 72,
    # Add new courses as needed
}
```

Always verify course par when setting up a new tournament week.
