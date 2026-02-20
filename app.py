import logging
import os
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

    # Build a last-name lookup for matching sheet names (e.g. "MATSUYAMA")
    # to ESPN full names (e.g. "Hideki Matsuyama")
    lastname_lookup = {}
    for full_name, data in leaderboard.items():
        last = full_name.split()[-1] if full_name else ""
        if last:
            lastname_lookup[last] = full_name

    # Enrich current week picks with live data
    for team in teams:
        for pick in team["picks"]:
            golfer_key = pick["golfer"].lower()
            # Try exact full-name match first, then fall back to last-name match
            matched_key = golfer_key
            if golfer_key not in leaderboard and golfer_key in lastname_lookup:
                matched_key = lastname_lookup[golfer_key]
            if matched_key in leaderboard:
                live = leaderboard[matched_key]
                pick["live_position"] = live["position"]
                pick["live_score"] = live["score"]
                pick["live_today"] = live["today"]
                pick["live_thru"] = live["thru"]
                pick["live_status"] = live["status"]
                tournament = live.get("event", tournament)

    # Recalculate points: for the latest week with no finish recorded,
    # use live position if available
    for team in teams:
        total = 0
        for pick in team["picks"]:
            if pick["finish"] is not None:
                total += pick["finish"]
            elif "live_position" in pick:
                # Try to parse live position as a number for running total
                try:
                    pos = int(pick["live_position"].replace("T", ""))
                    total += pos
                except (ValueError, AttributeError):
                    pass
        team["total_points"] = total

    # Calculate rank without live data (completed weeks only)
    base_totals = []
    for team in teams:
        base = sum(p["finish"] for p in team["picks"] if p["finish"] is not None)
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
