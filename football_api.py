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



# The free API-Football plan may reject the current season. If it does,
# use an explicitly labelled, unofficial public schedule; never invent fixtures.
SCOREBOARD_LEAGUES={"E0":"eng.1","SP1":"esp.1","D1":"ger.1","I1":"ita.1","F1":"fra.1"}

def public_scoreboard(code):
    today=datetime.now(timezone.utc).date()
    window=today.strftime("%Y%m%d")+"-"+(today+timedelta(days=14)).strftime("%Y%m%d")
    url="https://site.api.espn.com/apis/site/v2/sports/soccer/"+SCOREBOARD_LEAGUES[code]+"/scoreboard?"+urllib.parse.urlencode({"dates":window,"limit":"250"})
    request=urllib.request.Request(url,headers={"User-Agent":"FootballPredictor/0.8","Accept":"application/json"})
    with urllib.request.urlopen(request,timeout=18) as response:
        payload=json.load(response)
    fixtures=[]
    for event in payload.get("events",[]):
        competition=(event.get("competitions") or [{}])[0]
        sides={x.get("homeAway"):x.get("team",{}) for x in competition.get("competitors",[])}
        home,away=sides.get("home",{}),sides.get("away",{})
        date=event.get("date")
        if ((event.get("status") or {}).get("type") or {}).get("state")!="pre" or not date:
            continue
        if not home.get("displayName") or not away.get("displayName"):
            continue
        try: future=datetime.fromisoformat(date.replace("Z","+00:00"))>datetime.now(timezone.utc)
        except (TypeError,ValueError):future=False
        if future:
            fixtures.append({"id":"espn:"+str(event["id"]),"date":date,
                             "home":home["displayName"],"away":away["displayName"]})
    return sorted(fixtures,key=lambda x:x["date"])


# Official football-data.org integration. Configure FOOTBALL_DATA_ORG_KEY
# in the server environment (Render), never in the public Git repository.
FD_CODES={"E0":"PL","SP1":"PD","D1":"BL1","I1":"SA","F1":"FL1"}

def football_data_request(code, params):
    key=os.environ.get("FOOTBALL_DATA_ORG_KEY","").strip()
    if not key:raise ValueError("FOOTBALL_DATA_ORG_KEY is not configured.")
    url="https://api.football-data.org/v4/competitions/"+FD_CODES[code]+"/matches"
    url+="?"+urllib.parse.urlencode(params)
    request=urllib.request.Request(url,headers={
        "X-Auth-Token":key,"Accept":"application/json",
        "User-Agent":"FootballPredictor/0.9"})
    try:
        with urllib.request.urlopen(request,timeout=24) as response:
            return json.load(response).get("matches",[])
    except urllib.error.HTTPError as exc:
        raise ValueError("football-data.org HTTP "+str(exc.code)+
                         " (check token and competition permissions)") from exc
    except urllib.error.URLError as exc:
        raise ValueError("football-data.org connection failed: "+str(exc.reason)) from exc

def official_scheduled_fixtures(code):
    now=datetime.now(timezone.utc)
    items=football_data_request(code,{
        "dateFrom":now.date().isoformat(),
        "dateTo":(now.date()+timedelta(days=30)).isoformat()
    })
    out=[]
    for item in items:
        if item.get("status") not in ("SCHEDULED","TIMED"):
            continue
        date=item.get("utcDate","")
        try: valid=datetime.fromisoformat(date.replace("Z","+00:00"))>now
        except (ValueError,TypeError):valid=False
        home=item.get("homeTeam") or {}
        away=item.get("awayTeam") or {}
        if not valid or not home.get("name") or not away.get("name"):continue
        out.append({"id":"fd:"+str(item["id"]),"date":date,
                    "home":home["name"],"away":away["name"],
                    "status":item["status"]})
    return sorted(out,key=lambda f:f["date"])

def official_recent_results(code):
    """Finished games from the current European season (when token is set)."""
    if not os.environ.get("FOOTBALL_DATA_ORG_KEY","").strip():return []
    now=datetime.now(timezone.utc)
    season=current_season(now)
    cache=CACHE/("fd_results_"+code+"_"+str(season)+".json")
    if cache.exists() and time.time()-cache.stat().st_mtime<3600:
        try:return json.loads(cache.read_text(encoding="utf-8"))
        except (ValueError,OSError):pass
    rows=football_data_request(code,{
        "dateFrom":str(season)+"-07-01",
        "dateTo":now.date().isoformat(),
        "status":"FINISHED"
    })
    result=[]
    for item in rows:
        h=item.get("homeTeam") or {};a=item.get("awayTeam") or {}
        score=item.get("score") or {}
        ft=score.get("fullTime") or {};ht=score.get("halfTime") or {}
        if not item.get("utcDate") or not h.get("name") or not a.get("name"):continue
        hg,ag=ft.get("home"),ft.get("away")
        if type(hg)!=int or type(ag)!=int or hg<0 or ag<0:continue
        result.append({"date":item["utcDate"][:10],"home":h["name"],
                       "away":a["name"],"hg":hg,"ag":ag,
                       "hthg":ht.get("home"),"htag":ht.get("away")})
    cache.write_text(json.dumps(result),encoding="utf-8")
    return result


def get_scheduled_fixtures(code):
    if code not in LEAGUES:raise ValueError("Unsupported competition.")
    cache=CACHE/("schedule_"+code+".json")
    if cache.exists() and time.time()-cache.stat().st_mtime<1200:
        try:
            result=json.loads(cache.read_text(encoding="utf-8"))
            result["cached"]=True
            return result
        except (OSError,ValueError):pass
    errors=[]
    if os.environ.get("FOOTBALL_DATA_ORG_KEY","").strip():
        try:
            items=official_scheduled_fixtures(code)
            result={"league":LEAGUES[code]["name"],"fixtures":items,
                    "source":"football-data.org (official free fixtures)",
                    "errors":[],"cached":False}
            # An empty list can be genuine (international break).
            cache.write_text(json.dumps(result),encoding="utf-8")
            return result
        except Exception as exc:
            errors.append("football-data.org: "+str(exc)[:160])
    try:
        result=get_fixtures(code)
        items=result["fixtures"]
        if items:
            result["source"]="API-Football"
            result["errors"]=[]
            cache.write_text(json.dumps(result),encoding="utf-8")
            return result
    except Exception as exc:
        errors.append("API-Football: "+str(exc)[:140])
    try:
        items=public_scoreboard(code)
        source="ESPN public scoreboard (unofficial)"
    except Exception as exc:
        errors.append("Alternate scoreboard: "+str(exc)[:140])
        items=[];source="Unavailable"
    result={"league":LEAGUES[code]["name"],"fixtures":items,
            "source":source,"errors":errors,"cached":False}
    if items:cache.write_text(json.dumps(result),encoding="utf-8")
    return result
