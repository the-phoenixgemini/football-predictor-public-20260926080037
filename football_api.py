import os

import json
import time
import urllib.request
import urllib.parse
import urllib.error

from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

API_BASE = "https://v3.football.api-sports.io"

CACHE = ROOT / "data" / "api_cache"

CACHE.mkdir(parents=True, exist_ok=True)

LEAGUES = {
    "E0": {
        "id": 39,
        "name": "Premier League"
    },
    "SP1": {
        "id": 140,
        "name": "La Liga"
    },
    "D1": {
        "id": 78,
        "name": "Bundesliga"
    },
    "I1": {
        "id": 135,
        "name": "Serie A"
    },
    "F1": {
        "id": 61,
        "name": "Ligue 1"
    }
}

CACHE_SECONDS = 1800


def api_request(endpoint, params):

    key_path = ROOT / "api_key.txt"

    if not key_path.exists() and not os.environ.get("API_FOOTBALL_KEY"):
        raise ValueError(
            "API key not configured."
        )

    key = os.environ.get("API_FOOTBALL_KEY") or key_path.read_text(
        encoding="utf-8-sig"
    ).strip()

    if not key:
        raise ValueError(
            "API key file is empty."
        )

    query = urllib.parse.urlencode(params)

    url = (
        API_BASE
        + "/"
        + endpoint
        + "?"
        + query
    )

    request = urllib.request.Request(
        url,
        headers={
            "x-apisports-key": key
        }
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=25
        ) as response:

            data = json.load(response)

    except urllib.error.HTTPError as error:

        raise ValueError(
            "API-Football HTTP error: "
            + str(error.code)
        )

    except urllib.error.URLError as error:

        raise ValueError(
            "API connection failed: "
            + str(error.reason)
        )

    errors = data.get("errors")

    if errors:

        raise ValueError(
            "API-Football: "
            + json.dumps(errors)
        )

    paging = data.get("paging") or {}

    if paging.get("total", 1) > paging.get("current", 1):

        raise ValueError(
            "API returned multiple pages. "
            "Complete fixture retrieval is required."
        )

    return data


def current_season(now):

    # European football seasons usually begin
    # around July/August.

    if now.month >= 7:
        return now.year

    return now.year - 1


def get_fixtures(league_code):

    if league_code not in LEAGUES:

        raise ValueError(
            "Unsupported league."
        )

    now = datetime.now(timezone.utc)

    season = current_season(now)

    start = now.date()

    end = start + timedelta(days=7)

    league = LEAGUES[league_code]

    cache_file = CACHE / (
        "fixtures_"
        + league_code
        + "_"
        + start.isoformat()
        + ".json"
    )

    # Avoid repeatedly spending API requests.

    if cache_file.exists():

        age = (
            time.time()
            - cache_file.stat().st_mtime
        )

        if age < CACHE_SECONDS:

            try:
                cached = json.loads(
                    cache_file.read_text(
                        encoding="utf-8"
                    )
                )

                cached["cached"] = True

                return cached

            except (
                OSError,
                ValueError,
                KeyError
            ):
                pass

    data = api_request(
        "fixtures",
        {
            "league": league["id"],
            "season": season,
            "from": start.isoformat(),
            "to": end.isoformat()
        }
    )

    fixtures = []

    for item in data.get("response", []):

        fixture = item.get("fixture") or {}

        teams = item.get("teams") or {}

        home = teams.get("home") or {}

        away = teams.get("away") or {}

        status = fixture.get("status") or {}

        if status.get("short") not in (
            "NS",
            "TBD",
            "PST"
        ):
            continue

        fixtures.append({
            "id": fixture.get("id"),
            "date": fixture.get("date"),
            "home": home.get("name"),
            "away": away.get("name"),
            "home_id": home.get("id"),
            "away_id": away.get("id"),
            "status": status.get("short")
        })

    fixtures.sort(
        key=lambda item:
        item.get("date") or ""
    )

    result = {
        "league": league["name"],
        "league_code": league_code,
        "season": season,
        "fixtures": fixtures,
        "count": len(fixtures),
        "cached": False,
        "retrieved_at": now.isoformat()
    }

    temporary = cache_file.with_suffix(
        ".tmp"
    )

    temporary.write_text(
        json.dumps(result),
        encoding="utf-8"
    )

    temporary.replace(cache_file)

    return result

