# Weekly Workflow Guide

## Automated Live Scoring (During Tournament)

The leaderboard at https://ebrady25.github.io/lous-pool/ updates automatically:

1. **GitHub Actions** runs `score_engine.py` every 5 minutes during tournament hours
2. **score_engine.py** fetches live data from DataGolf (both endpoints)
3. **leaderboard.json** is updated with all 100 teams and 120+ players
4. **GitHub Pages** auto-deploys with fresh data
5. **Frontend** reads from leaderboard.json with cache-busting

### Update Schedule (UTC)
- Every 5 min from 11:00-23:00 on Thu, Fri, Sat, Sun
- Every 5 min from 00:00-02:00 on Fri, Sat, Sun, Mon

That's roughly **6am - 6pm ET** on tournament days.

## Automated Features by Round

### Round 1 & 2 (Thursday/Friday)
The Live PGA Leaderboard automatically displays:
| # | Player | Total | R1/R2 | MC% |
|---|--------|-------|-------|-----|

- **Total**: Overall tournament score (to-par) with green highlight
- **R1/R2**: Current round score with holes thru
- **MC%**: Make Cut probability (color-coded: green/gold/red)

### After R2 Completes (Friday Evening)
**Automated actions:**
1. ✅ Cut line detected using DataGolf `make_cut` probabilities (no need to wait for R3)
2. ✅ MC players marked with red "MC" tag
3. ✅ MC penalties calculated based on strokes missed
4. ✅ Rule 4 replacement players auto-selected (cut line players, sorted by R2 tee time)
5. ✅ MC players assigned Rule 4 replacements based on team listing order

### Round 3 & 4 (Saturday/Sunday)
The Live PGA Leaderboard automatically switches to:
| # | Player | Total | R3/R4 | T10% |
|---|--------|-------|-------|------|

- **Total**: Overall tournament score (to-par) with green highlight
- **R3/R4**: Current round score with holes thru
- **T10%**: Top 10 finish probability (color-coded)

MC players show their replacement player's R3/R4 scores automatically.

## Pre-Tournament Setup (Before Thursday)

### 1. Get Weekly Entries
Download the entries Excel file from commissioner.

### 2. Parse and Upload Rosters
```python
import pandas as pd
import json

# Parse the Excel file (4 rows per team format)
df = pd.read_excel('Week_X_Entries.xlsx')

# Convert to rosters.json format
rosters = []
for team in parsed_teams:
    rosters.append({
        "owner": team_name,
        "players": [player1, player2, player3, player4],
        "alternates": [alt1, alt2]
    })

# Save and push to GitHub
with open('rosters.json', 'w') as f:
    json.dump(rosters, f, indent=2)
```

### 3. Update CONFIG in index.html
```javascript
SEASON: {
  week: 3,
  event: "Genesis Invitational",
  course_par: 71,
}
```

### 4. Update COURSE_PAR in score_engine.py (if new course)
```python
COURSE_PAR = {
    "WM Phoenix Open": 71,
    "Farmers Insurance Open": 72,
    "Genesis Invitational": 71,  # Add new course
    # ...
}
```

### 5. Verify Your Lineup
Check that your team is correct in rosters.json.

## During Tournament (Thu-Sun)

The leaderboard auto-updates every 5 minutes. To manually trigger:

**Option 1: GitHub UI**
1. Go to https://github.com/ebrady25/lous-pool/actions
2. Click "Update Leaderboard" workflow
3. Click "Run workflow"

**Option 2: API**
```bash
curl -X POST -H "Authorization: token $GH_TOKEN" \
  "https://api.github.com/repos/ebrady25/lous-pool/actions/workflows/update-leaderboard.yml/dispatches" \
  -d '{"ref":"main"}'
```

## Automated Rule 4 System

### How It Works
After R2 completes, the system automatically:

1. **Detects cut line** using DataGolf `make_cut` probabilities
   - Players with >99% = definitely made cut
   - Players with <1% = definitely missed cut
   - Cut line = highest R1+R2 total among players who made cut

2. **Selects Rule 4 players** (lowest qualifiers):
   - Must have made the cut
   - R1+R2 total equals cut line exactly
   - Sorted by earliest R2 tee time (first off = Rule 4 #1)

3. **Assigns replacements to MC teams**:
   - 1st MC player on team roster → Rule 4 #1's R3/R4 scores
   - 2nd MC player on team roster → Rule 4 #2's R3/R4 scores
   - 3rd MC player on team roster → Rule 4 #3's R3/R4 scores

### Verifying Rule 4 Players
After R2 completes, check `leaderboard.json`:
```json
{
  "cut_line": 141,
  "replacement_players": [
    {"name": "S.H. Kim", "r2_teetime": "2026-02-06 07:31"},
    {"name": "J.T. Poston", "r2_teetime": "2026-02-06 07:53"},
    {"name": "Collin Morikawa", "r2_teetime": "2026-02-06 07:53"}
  ]
}
```

Compare against commissioner's announcement. If discrepancy, check:
- R2 tee times are correct in field-updates endpoint
- Cut line calculation is accurate
- All cut line players are included

## Post-Tournament (Sunday Night)

### 1. Verify Final Results
- Wait for DataGolf to show `event_completed: true`
- Compare against commissioner's final spreadsheet
- Verify MC penalties and Rule 4 replacements

### 2. Update Season Data
Update `data/season.json` with final standings:
```json
{
  "Ethan Brady": {
    "pts": 2288,
    "usage": {
      "Scottie Scheffler": 1,
      "Ben Griffin": 1
    }
  }
}
```

### 3. Analyze Ownership
```python
ownership = {}
for team in rosters:
    for player in team['players']:
        ownership[player] = ownership.get(player, 0) + 1

# Sort by ownership
sorted_own = sorted(ownership.items(), key=lambda x: -x[1])
```

## Troubleshooting

### Leaderboard Not Updating
1. Check workflow runs: https://github.com/ebrady25/lous-pool/actions
2. Manually trigger workflow
3. Check for errors in workflow logs

### Stale Data in Browser
1. Hard refresh: Ctrl+Shift+R (Windows) or Cmd+Shift+R (Mac)
2. Open in incognito
3. Check Network tab in DevTools for leaderboard.json request

### Round Not Detected Correctly
- The `round` field from DataGolf in-play endpoint determines current round
- Check that `merge_player_data()` preserves the `round` field
- Manually trigger workflow to re-fetch data

### MC Players Not Marked
- Cut detection requires definitive `make_cut` values (>99% or <1%)
- Usually available immediately after R2 completes
- Check `cut_line` value in leaderboard.json

### Rule 4 Players Incorrect
- Check R2 tee times in DataGolf field-updates endpoint
- Verify cut line is correct
- Confirm sorting is by `r2_teetime` ascending

### Scores Showing Wrong Values
- During live: Should show to-par (e.g., -3, +1, E)
- If showing strokes (e.g., 68, 72): score_engine.py bug
- Check that `current_score` is being used, not `total`

### Player Not Found
- Check name format matches DataGolf ("Last, First")
- Check for special characters, suffixes (Jr., III)
- Update name normalization in score_engine.py if needed

## File Locations

| File | Location | Purpose |
|------|----------|---------|
| score_engine.py | scripts/ | Core scoring logic |
| index.html | public/ | Frontend UI |
| leaderboard.json | public/data/ | Live tournament state |
| rosters.json | data/ | Weekly team rosters |
| season.json | data/ | Season standings |
| update-leaderboard.yml | .github/workflows/ | Auto-update config |

## Season Par Tracking

| Week | Event | Par/Rd | Team Par | Cumulative |
|------|-------|--------|----------|------------|
| 1 | Farmers Insurance Open | 72 | 1152 | 1152 |
| 2 | WM Phoenix Open | 71 | 1136 | 2288 |
