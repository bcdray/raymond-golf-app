import logging
import os
import unicodedata
from flask import Flask, jsonify, render_template
from sheets import load_sheet_data
from leaderboard import fetch_leaderboard, get_tournament_name

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

SPREADSHEET_ID = os.environ.get("GOLF_SHEET_ID", "")
CREDENTIALS_FILE = os.environ.get("GOLF_CREDENTIALS", "credentials.json")


@app.route("/")
def index():
    tournament = get_tournament_name()
    return render_template("index.html", tournament=tournament)


@app.route("/api/standings")
def api_standings():
    """Return combined standings: sheet data enriched with live leaderboard."""
    if not SPREADSHEET_ID:
        return jsonify({"error": "GOLF_SHEET_ID env var not set"}), 500

    try:
        teams = load_sheet_data(SPREADSHEET_ID, CREDENTIALS_FILE)
    except Exception as e:
        app.logger.error(f"Sheet error: {e}", exc_info=True)
        return jsonify({"error": f"Sheet error: {e}"}), 500

    leaderboard = fetch_leaderboard()
    tournament = ""

    def normalize(name):
        """Strip accents and lowercase for fuzzy matching."""
        n = unicodedata.normalize("NFD", name)
        return "".join(c for c in n if unicodedata.category(c) != "Mn").lower()

    # Build lookups for matching sheet names to ESPN full names
    lastname_lookup = {}
    normalized_lookup = {}  # normalized full name -> original key
    normalized_last_lookup = {}  # normalized last name -> original key
    for full_name, data in leaderboard.items():
        parts = full_name.split()
        last = parts[-1] if parts else ""
        if last:
            lastname_lookup[last] = full_name
            normalized_last_lookup[normalize(last)] = full_name
        normalized_lookup[normalize(full_name)] = full_name

    def match_golfer(pick_name):
        """Match a sheet golfer name to an ESPN leaderboard entry."""
        key = pick_name.lower()
        # Exact full-name match
        if key in leaderboard:
            return key
        # Last-name match (e.g. "MATSUYAMA")
        if key in lastname_lookup:
            return lastname_lookup[key]
        # Normalized match — strips accents (e.g. "hojgaard" matches "højgaard")
        norm = normalize(pick_name)
        if norm in normalized_lookup:
            return normalized_lookup[norm]
        if norm in normalized_last_lookup:
            return normalized_last_lookup[norm]
        # Initial + last name (e.g. "N hojgaard" or "J Smith")
        pick_parts = pick_name.split()
        if len(pick_parts) >= 2 and len(pick_parts[0]) <= 2:
            # Multi-initial match (e.g. "M W Lee" -> "Min Woo Lee", "S W Kim" -> "Si Woo Kim")
            all_initials = all(len(p.rstrip(".")) == 1 for p in pick_parts[:-1])
            if all_initials and len(pick_parts) >= 3:
                initials = [p.rstrip(".").lower() for p in pick_parts[:-1]]
                pick_last = normalize(pick_parts[-1])
                for full_name, orig_key in normalized_lookup.items():
                    espn_parts = full_name.split()
                    if len(espn_parts) == len(pick_parts):
                        espn_initials = [p[0] for p in espn_parts[:-1]]
                        espn_last = normalize(espn_parts[-1])
                        if initials == espn_initials and pick_last == espn_last:
                            return orig_key
            # Single initial + last name
            initial = pick_parts[0].rstrip(".").lower()
            pick_last = normalize(" ".join(pick_parts[1:]))
            for full_name, orig_key in normalized_lookup.items():
                espn_parts = full_name.split()
                if len(espn_parts) >= 2:
                    espn_initial = espn_parts[0][0]
                    espn_last = " ".join(espn_parts[1:])
                    if espn_initial == initial and espn_last == pick_last:
                        return orig_key
        # Partial last name match (e.g. "Neergaard" matches "Rasmus Neergaard-Petersen")
        for full_name, orig_key in normalized_lookup.items():
            if norm in full_name or full_name.endswith(norm):
                return orig_key
        return None

    # Find the current (latest) week
    max_week = 0
    for team in teams:
        for pick in team["picks"]:
            if pick["week"] > max_week:
                max_week = pick["week"]

    # If a team has no pick for the current week, treat as missed cut (70th place)
    MC_PENALTY = 70
    for team in teams:
        has_current_week = any(p["week"] == max_week for p in team["picks"])
        if not has_current_week and max_week > 0:
            team["picks"].append({
                "week": max_week,
                "tournament": tournament or "Current",
                "golfer": "NO PICK",
                "finish": None,
                "no_pick": True,
            })

    # Enrich current week picks with live data
    for team in teams:
        for pick in team["picks"]:
            matched_key = match_golfer(pick["golfer"])
            if matched_key and matched_key in leaderboard:
                live = leaderboard[matched_key]
                pick["live_position"] = live["position"]
                pick["live_score"] = live["score"]
                pick["live_today"] = live["today"]
                pick["live_thru"] = live["thru"]
                pick["live_status"] = live["status"]
                tournament = live.get("event", tournament)

    # Recalculate points: for the latest week with no finish recorded,
    # use live position if available; no-pick = 70 penalty
    for team in teams:
        total = 0
        for pick in team["picks"]:
            if pick.get("no_pick"):
                total += MC_PENALTY
                pick["live_position"] = str(MC_PENALTY)
                pick["live_score"] = "MC"
            elif pick["finish"] is not None:
                total += pick["finish"]
            elif "live_position" in pick:
                # Try to parse live position as a number for running total
                try:
                    pos = int(pick["live_position"].replace("T", ""))
                    total += pos
                except (ValueError, AttributeError):
                    pass
        team["total_points"] = total

    # Calculate rank based on completed weeks only (no current week data at all)
    # This is the "entering the week" rank to compare against
    base_totals = []
    for team in teams:
        base = sum(p["finish"] for p in team["picks"]
                   if p["finish"] is not None and p["week"] != max_week)
        base_totals.append({"team": team["team"], "base_points": base})
    base_totals.sort(key=lambda t: t["base_points"])
    base_rank = {t["team"]: i + 1 for i, t in enumerate(base_totals)}

    # Re-sort after recalculation (with live data)
    teams.sort(key=lambda t: t["total_points"])

    # Add rank_change: positive = moving up, negative = moving down
    for i, team in enumerate(teams):
        current_rank = i + 1
        previous_rank = base_rank.get(team["team"], current_rank)
        team["rank_change"] = previous_rank - current_rank

    return jsonify({
        "tournament": tournament,
        "teams": teams,
    })


@app.route("/prd")
def prd():
    return render_template("prd.html")


if __name__ == "__main__":
    app.run(debug=True, port=5001)
