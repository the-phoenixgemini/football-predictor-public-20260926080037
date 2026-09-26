import os

from football_api import get_fixtures
import csv
import json
import math
import sqlite3
import threading
import webbrowser

from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "football.db"
IMPORT = ROOT / "data" / "import"
WEB = ROOT / "web" / "index.html"

PORT = int(os.environ.get("PORT", "8765"))

DB.parent.mkdir(parents=True, exist_ok=True)
IMPORT.mkdir(parents=True, exist_ok=True)


def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def initialize():
    with connect() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league TEXT NOT NULL,
            date TEXT NOT NULL,
            home TEXT NOT NULL,
            away TEXT NOT NULL,
            hg INTEGER NOT NULL,
            ag INTEGER NOT NULL,
            hthg INTEGER,
            htag INTEGER,
            UNIQUE(league,date,home,away)
        )
        """)

        c.execute("""
        CREATE INDEX IF NOT EXISTS idx_match_league_date
        ON matches(league,date)
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created TEXT NOT NULL,
            league TEXT NOT NULL,
            home TEXT NOT NULL,
            away TEXT NOT NULL,
            market TEXT NOT NULL,
            probability REAL NOT NULL,
            fair_odds REAL NOT NULL,
            bookmaker_odds REAL
        )
        """)


def date_value(value):
    value = str(value or "").strip()

    formats = [
        "%d/%m/%Y",
        "%d/%m/%y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%Y/%m/%d",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def field(row, *names):
    for name in names:
        v = row.get(name)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def number(value):
    try:
        x = int(float(value))
        return x if x >= 0 else None
    except (ValueError, TypeError):
        return None


def import_data():
    imported = 0
    skipped = 0
    files = 0

    with connect() as c:
        for path in sorted(IMPORT.glob("*.csv")):
            files += 1

            try:
                with path.open(
                    "r",
                    encoding="utf-8-sig",
                    errors="replace",
                    newline=""
                ) as f:

                    reader = csv.DictReader(f)

                    for raw in reader:
                        row = {
                            str(k or "").strip(): v
                            for k, v in raw.items()
                        }

                        league = field(
                            row, "Div", "League", "league"
                        )

                        date = date_value(
                            field(row, "Date", "date")
                        )

                        home = field(
                            row, "HomeTeam", "home", "Home"
                        )

                        away = field(
                            row, "AwayTeam", "away", "Away"
                        )

                        hg = number(
                            field(row, "FTHG", "hg", "HomeGoals")
                        )

                        ag = number(
                            field(row, "FTAG", "ag", "AwayGoals")
                        )

                        hthg = number(
                            field(row, "HTHG", "hthg")
                        )

                        htag = number(
                            field(row, "HTAG", "htag")
                        )

                        if not all([league, date, home, away]):
                            skipped += 1
                            continue

                        if hg is None or ag is None:
                            skipped += 1
                            continue

                        cur = c.execute("""
                            INSERT OR IGNORE INTO matches
                            (league,date,home,away,hg,ag,hthg,htag)
                            VALUES(?,?,?,?,?,?,?,?)
                        """, (
                            league, date, home, away,
                            hg, ag, hthg, htag
                        ))

                        if cur.rowcount:
                            imported += 1
                        else:
                            skipped += 1

            except (OSError, csv.Error, UnicodeError):
                skipped += 1

    return {
        "files": files,
        "imported": imported,
        "skipped": skipped,
    }


def fetch_matches(league):
    with connect() as c:
        rows = c.execute("""
            SELECT * FROM matches
            WHERE league=?
            ORDER BY date,id
        """, (league,)).fetchall()

    return [dict(r) for r in rows]


def status():
    with connect() as c:
        total = c.execute(
            "SELECT COUNT(*) FROM matches"
        ).fetchone()[0]

        leagues = c.execute("""
            SELECT league,
                   COUNT(*) AS total,
                   MAX(date) AS latest
            FROM matches
            GROUP BY league
            ORDER BY league
        """).fetchall()

        output = []

        for l in leagues:
            teams = c.execute("""
                SELECT home AS name
                FROM matches WHERE league=?
                UNION
                SELECT away AS name
                FROM matches WHERE league=?
                ORDER BY name
            """, (l["league"], l["league"])).fetchall()

            output.append({
                "name": l["league"],
                "matches": l["total"],
                "latest": l["latest"],
                "teams": [r["name"] for r in teams],
            })

    return {
        "version": "0.3-data",
        "public_preview": bool(os.environ.get("PUBLIC_PREVIEW")),
        "matches": total,
        "leagues": output,
    }


def poisson(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def weighted_average(values, baseline):
    if not values:
        return baseline

    # Newer matches receive greater weight.
    weights = [
        0.94 ** (len(values) - 1 - i)
        for i in range(len(values))
    ]

    total_weight = sum(weights)

    # Six equivalent matches of league-average
    # smoothing help stabilize small samples.
    return (
        sum(v * w for v, w in zip(values, weights))
        + 6.0 * baseline
    ) / (total_weight + 6.0)


def make_model(rows, home, away):
    if home == away:
        raise ValueError(
            "Home and away teams must be different."
        )

    if len(rows) < 40:
        raise ValueError(
            "At least 40 historical league matches are required."
        )

    # Use the latest two years available in this
    # historical training snapshot.
    latest = datetime.strptime(
        rows[-1]["date"], "%Y-%m-%d"
    )

    cutoff = (
        latest - timedelta(days=760)
    ).strftime("%Y-%m-%d")

    recent = [
        r for r in rows
        if r["date"] >= cutoff
    ]

    if len(recent) < 40:
        raise ValueError(
            "Insufficient recent league history."
        )

    home_games = [
        r for r in recent
        if r["home"] == home
    ][-20:]

    away_games = [
        r for r in recent
        if r["away"] == away
    ][-20:]

    if len(home_games) < 5:
        raise ValueError(
            "The home team needs at least "
            "five historical home matches."
        )

    if len(away_games) < 5:
        raise ValueError(
            "The away team needs at least "
            "five historical away matches."
        )

    avg_h = sum(
        r["hg"] for r in recent
    ) / len(recent)

    avg_a = sum(
        r["ag"] for r in recent
    ) / len(recent)

    if avg_h <= 0 or avg_a <= 0:
        raise ValueError(
            "The historical goal data is invalid."
        )

    home_scored = weighted_average(
        [r["hg"] for r in home_games],
        avg_h
    )

    home_conceded = weighted_average(
        [r["ag"] for r in home_games],
        avg_a
    )

    away_scored = weighted_average(
        [r["ag"] for r in away_games],
        avg_a
    )

    away_conceded = weighted_average(
        [r["hg"] for r in away_games],
        avg_h
    )

    expected_h = (
        home_scored * away_conceded / avg_h
    )

    expected_a = (
        away_scored * home_conceded / avg_a
    )

    expected_h = max(
        0.15, min(4.5, expected_h)
    )

    expected_a = max(
        0.15, min(4.5, expected_a)
    )

    # Full score probability distribution.
    matrix = []

    for h in range(13):
        line = []

        for a in range(13):
            p = (
                poisson(h, expected_h)
                * poisson(a, expected_a)
            )

            line.append(p)

        matrix.append(line)

    total = sum(
        sum(line) for line in matrix
    )

    matrix = [
        [p / total for p in line]
        for line in matrix
    ]

    home_win = 0.0
    draw = 0.0
    away_win = 0.0
    btts = 0.0

    overs = {
        0.5: 0.0,
        1.5: 0.0,
        2.5: 0.0,
        3.5: 0.0,
        4.5: 0.0,
    }

    for h in range(13):
        for a in range(13):
            p = matrix[h][a]

            if h > a:
                home_win += p
            elif h == a:
                draw += p
            else:
                away_win += p

            if h > 0 and a > 0:
                btts += p

            for line in overs:
                if h + a > line:
                    overs[line] += p

    markets = {
        "home": home_win,
        "draw": draw,
        "away": away_win,
        "1x": home_win + draw,
        "x2": away_win + draw,
        "12": home_win + away_win,
        "btts_yes": btts,
        "btts_no": 1.0 - btts,
    }

    for line, p in overs.items():
        key = str(line).replace(".", "_")

        markets["over_" + key] = p
        markets["under_" + key] = 1.0 - p

    return {
        "home": home,
        "away": away,
        "expected_home_goals": expected_h,
        "expected_away_goals": expected_a,
        "historical_matches": len(recent),
        "home_sample": len(home_games),
        "away_sample": len(away_games),
        "latest_data": rows[-1]["date"],
        "markets": markets,
    }


def predict(payload):
    league = str(
        payload.get("league", "")
    ).strip()

    home = str(
        payload.get("home", "")
    ).strip()

    away = str(
        payload.get("away", "")
    ).strip()

    market = str(
        payload.get("market", "")
    ).strip()

    odds_input = payload.get("odds")

    rows = fetch_matches(league)

    if not rows:
        raise ValueError(
            "No historical matches for this league."
        )

    available_teams = set()

    for r in rows:
        available_teams.add(r["home"])
        available_teams.add(r["away"])

    if home not in available_teams:
        raise ValueError(
            "Home team is not in the selected league."
        )

    if away not in available_teams:
        raise ValueError(
            "Away team is not in the selected league."
        )

    model = make_model(rows, home, away)

    if market not in model["markets"]:
        raise ValueError(
            "This betting market is not supported yet."
        )

    p = model["markets"][market]

    fair = 1.0 / p if p > 0 else None

    odds = None
    implied = None
    difference = None
    expected_return = None

    if odds_input not in (None, ""):
        try:
            odds = float(odds_input)
        except (ValueError, TypeError):
            raise ValueError(
                "Bookmaker odds must be a number."
            )

        if not math.isfinite(odds) or odds <= 1:
            raise ValueError(
                "Decimal odds must be greater than 1."
            )

        implied = 1.0 / odds

        difference = (
            p - implied
        ) * 100.0

        expected_return = (
            p * odds - 1.0
        ) * 100.0

    with connect() as c:
        c.execute("""
            INSERT INTO predictions
            (created,league,home,away,market,
             probability,fair_odds,bookmaker_odds)
            VALUES(?,?,?,?,?,?,?,?)
        """, (
            datetime.now().isoformat(),
            league,
            home,
            away,
            market,
            p,
            fair,
            odds,
        ))

    return {
        "league": league,
        "home": home,
        "away": away,
        "market": market,
        "probability": p,
        "fair_odds": fair,
        "bookmaker_odds": odds,
        "implied_probability": implied,
        "difference_points": difference,
        "expected_return_percent": expected_return,
        "model": model,
    }


def backtest(league):
    rows = fetch_matches(league)

    evaluated = 0
    correct = 0
    brier_total = 0.0
    error_count = 0

    # Chronological replay.
    # Each prediction uses earlier matches only.
    start = max(40, len(rows) - 150)

    for i in range(start, len(rows)):
        current = rows[i]
        previous = rows[:i]

        try:
            result = make_model(
                previous,
                current["home"],
                current["away"]
            )
        except ValueError:
            error_count += 1
            continue

        p = result["markets"]

        actual = (
            "home" if current["hg"] > current["ag"]
            else "away" if current["hg"] < current["ag"]
            else "draw"
        )

        pick = max(
            ("home", "draw", "away"),
            key=lambda key: p[key]
        )

        correct += int(pick == actual)

        brier_total += sum(
            (
                p[key] - int(key == actual)
            ) ** 2
            for key in ("home", "draw", "away")
        )

        evaluated += 1

    if not evaluated:
        raise ValueError(
            "Not enough eligible historical matches "
            "for this backtest."
        )

    return {
        "league": league,
        "evaluated": evaluated,
        "accuracy": 100 * correct / evaluated,
        "brier_score": brier_total / evaluated,
        "skipped": error_count,
        "method": "Chronological historical replay",
    }


class Handler(BaseHTTPRequestHandler):

    def send_json(self, obj, code=200):
        content = json.dumps(
            obj, allow_nan=False
        ).encode("utf-8")

        self.send_response(code)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(content))
        )

        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        route = urlparse(self.path).path

        try:

            if route in (
                "/manifest.json",
                "/service-worker.js",
                "/icon-192.png",
                "/icon-512.png"
            ):

                path = (
                    ROOT
                    / "web"
                    / route.lstrip("/")
                )

                content = path.read_bytes()

                mime = (
                    "application/manifest+json"
                    if route.endswith(".json")
                    else "application/javascript"
                    if route.endswith(".js")
                    else "image/png"
                )

                self.send_response(200)

                self.send_header(
                    "Content-Type",
                    mime
                )

                self.send_header(
                    "Cache-Control",
                    "no-cache"
                )

                self.send_header(
                    "Content-Length",
                    str(len(content))
                )

                self.end_headers()

                self.wfile.write(content)

                return


            if route == "/api/fixtures":

                from urllib.parse import parse_qs

                query = parse_qs(
                    urlparse(self.path).query
                )

                league = query.get(
                    "league", [""]
                )[0]

                return self.send_json(
                    get_fixtures(league)
                )

            if route == "/api/status":
                return self.send_json(status())

            if route == "/":
                content = WEB.read_bytes()

                self.send_response(200)

                self.send_header(
                    "Content-Type",
                    "text/html; charset=utf-8"
                )

                self.send_header(
                    "Content-Length",
                    str(len(content))
                )

                self.end_headers()

                self.wfile.write(content)
                return

            self.send_json(
                {"error": "Not found"},
                404
            )

        except Exception as exc:
            self.send_json(
                {"error": str(exc)},
                500
            )

    def do_POST(self):
        route = urlparse(self.path).path

        try:
            size = int(
                self.headers.get(
                    "Content-Length", "0"
                )
            )

            if size > 16384:
                return self.send_json(
                    {"error": "Request too large"},
                    413
                )

            raw = self.rfile.read(size)

            payload = (
                json.loads(raw)
                if raw else {}
            )

            if route == "/api/import" and not os.environ.get("PUBLIC_PREVIEW"):
                return self.send_json(
                    import_data()
                )

            if route == "/api/predict":
                return self.send_json(
                    predict(payload)
                )

            if route == "/api/backtest" and not os.environ.get("PUBLIC_PREVIEW"):
                league = str(
                    payload.get("league", "")
                ).strip()

                return self.send_json(
                    backtest(league)
                )

            self.send_json(
                {"error": "Not found"},
                404
            )

        except ValueError as exc:
            self.send_json(
                {"error": str(exc)},
                400
            )

        except Exception as exc:
            self.send_json(
                {"error": str(exc)},
                500
            )


def main():
    initialize()
    imported = import_data()
    print('Historical snapshot:', imported, flush=True)

    url = f"http://127.0.0.1:{PORT}"

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        Handler
    )

    print()
    print("FOOTBALL PREDICTOR v0.3-data")
    print("Application:", url)
    print("Database:", DB)
    print("Import folder:", IMPORT)
    print()
    print("Press CTRL+C to stop.")
    print()

    if not os.environ.get("RENDER"): threading.Timer(
        1.0,
        lambda: webbrowser.open(url)
    ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

