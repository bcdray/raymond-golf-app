import base64
import json
import os

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Sheet layout (2026 Standings):
#   Row 3 (idx 3): Tournament names
#   Row 4 (idx 4): Column headers — GOLFER, CP, TP repeated per tournament, NAME in col 13
#   Row 6+ (idx 6+): Data rows
#   Col 0: Team number
#   Col 13: Team name
#   Tournament blocks: GOLFER col, CP (current position), TP (total points)
#   GOLFER columns found dynamically from header row

SHEET_NAME = "2026 Standings"
HEADER_ROW = 4       # 0-indexed row with GOLFER/CP/TP headers
TOURNAMENT_ROW = 3   # 0-indexed row with tournament names
DATA_START_ROW = 6   # 0-indexed first data row
NAME_COL_FALLBACK = 14  # Fallback if NAME header not found


def get_client(credentials_file="credentials.json"):
    # Support base64-encoded credentials via env var (for cloud deployment)
    creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "")
    if creds_b64:
        creds_json = json.loads(base64.b64decode(creds_b64))
        creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)
    elif os.path.exists(credentials_file):
        creds = Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
    else:
        # Try GOOGLE_CREDENTIALS as raw JSON
        creds_raw = os.environ.get("GOOGLE_CREDENTIALS", "")
        if creds_raw:
            creds_json = json.loads(creds_raw)
            creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)
        else:
            raise RuntimeError(
                "No credentials found. Set GOOGLE_CREDENTIALS_B64, "
                "GOOGLE_CREDENTIALS, or provide credentials.json file. "
                f"Env vars present: {[k for k in os.environ if 'GOOGLE' in k or 'CRED' in k]}"
            )
    return gspread.authorize(creds)


def load_sheet_data(spreadsheet_id, credentials_file="credentials.json"):
    """Read the golf pool sheet and return structured team data.

    Returns a list of dicts:
      [{
        "team": "STEVE SARTORIUS",
        "picks": [{"week": 1, "tournament": "SONY OPEN", "golfer": "CONNORS", "finish": 24}, ...],
        "total_points": 172
      }, ...]
    """
    client = get_client(credentials_file)
    sheet = client.open_by_key(spreadsheet_id).worksheet(SHEET_NAME)
    rows = sheet.get_all_values()

    if len(rows) <= DATA_START_ROW:
        return []

    header_row = rows[HEADER_ROW]
    tourney_row = rows[TOURNAMENT_ROW]

    # Find NAME column dynamically so it doesn't break when columns shift
    name_col = NAME_COL_FALLBACK
    for j, v in enumerate(header_row):
        if v.strip().upper() == "NAME":
            name_col = j
            break

    # Find tournament blocks: columns where header says "GOLFER"
    golfer_cols = [j for j, v in enumerate(header_row) if v.strip() == "GOLFER"]

    # Map each golfer column to its tournament name
    tournaments = []
    for gc in golfer_cols:
        name = tourney_row[gc].strip() if gc < len(tourney_row) else ""
        if not name:
            # Tournament name may be in a preceding column (merged cells)
            for k in range(gc - 1, -1, -1):
                if k < len(tourney_row) and tourney_row[k].strip():
                    name = tourney_row[k].strip()
                    break
        tournaments.append({"col": gc, "name": name or f"Week {len(tournaments) + 1}"})

    teams = []

    for row in rows[DATA_START_ROW:]:
        if not row or len(row) <= name_col:
            continue

        raw_name = row[name_col].strip() if name_col < len(row) else ""
        if not raw_name:
            continue

        # A trailing * on the name indicates a missed cut
        missed_cuts = raw_name.count("*")
        team_name = raw_name.replace("*", "").strip()

        picks = []
        total_points = 0

        for week_num, tourney in enumerate(tournaments, start=1):
            gc = tourney["col"]
            golfer = row[gc].strip() if gc < len(row) else ""
            cp_str = row[gc + 1].strip() if gc + 1 < len(row) else ""

            if not golfer:
                continue

            finish = None
            if cp_str:
                try:
                    finish = int(cp_str)
                    total_points += finish
                except ValueError:
                    pass

            picks.append({
                "week": week_num,
                "tournament": tourney["name"],
                "golfer": golfer,
                "finish": finish,
            })

        teams.append({
            "team": team_name,
            "picks": picks,
            "total_points": total_points,
            "missed_cuts": missed_cuts,
        })

    # Sort by total points (lowest wins)
    teams.sort(key=lambda t: t["total_points"])
    return teams
