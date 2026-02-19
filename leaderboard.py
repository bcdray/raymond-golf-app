import requests


ESPN_GOLF_API = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"


def fetch_leaderboard():
    """Fetch live PGA Tour leaderboard from ESPN's API.

    Returns a dict keyed by lowercase golfer name:
      {
        "scottie scheffler": {
          "name": "Scottie Scheffler",
          "position": "1",
          "score": "-12",
          "today": "-4",
          "thru": "F",
          "status": "active"
        },
        ...
      }
    """
    try:
        resp = requests.get(ESPN_GOLF_API, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return _parse_espn_response(data)
    except Exception as e:
        print(f"ESPN API error: {e}")
        return {}


def _parse_espn_response(data):
    """Parse the ESPN golf scoreboard JSON into our leaderboard dict."""
    leaderboard = {}

    events = data.get("events", [])
    if not events:
        return leaderboard

    event = events[0]
    event_name = event.get("name", "Unknown Tournament")
    competitions = event.get("competitions", [])
    if not competitions:
        return leaderboard

    competition = competitions[0]
    comp_status = competition.get("status", {}).get("type", {}).get("state", "")
    competitors = competition.get("competitors", [])

    for player in competitors:
        athlete = player.get("athlete", {})
        name = athlete.get("displayName", "Unknown")

        # Position: use order field (1-indexed rank in the field)
        position = str(player.get("order", ""))

        # Score relative to par — top-level "score" field (e.g. "-5", "E", "+2")
        score_val = player.get("score", "")
        # score can be a string or dict depending on tournament state
        if isinstance(score_val, dict):
            score = score_val.get("displayValue", "")
        else:
            score = str(score_val) if score_val else ""

        # Try statistics array for additional detail
        today = ""
        thru = ""
        for stat in player.get("statistics", []):
            stat_name = stat.get("name", "")
            if stat_name == "scoreToPar" and not score:
                score = stat.get("displayValue", "")
            elif stat_name == "currentRoundScore":
                today = stat.get("displayValue", "")
            elif stat_name == "thru":
                thru = stat.get("displayValue", "")

        # Linescores can also provide round details
        linescores = player.get("linescores", [])
        if linescores and not today:
            latest = linescores[-1]
            today = str(latest.get("value", "")) if "value" in latest else ""

        status = comp_status or "unknown"

        leaderboard[name.lower()] = {
            "name": name,
            "position": position,
            "score": score,
            "today": today,
            "thru": thru,
            "status": status,
            "event": event_name,
        }

    return leaderboard


def get_tournament_name():
    """Return the name of the current tournament."""
    try:
        resp = requests.get(ESPN_GOLF_API, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        events = data.get("events", [])
        if events:
            return events[0].get("name", "Unknown Tournament")
    except Exception:
        pass
    return "Unknown Tournament"
