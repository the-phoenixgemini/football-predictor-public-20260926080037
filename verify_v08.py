import app
from datetime import datetime, timedelta, timezone

app.initialize()
result=app.import_data()
status=app.status()
assert status["matches"]>=3500, status
assert len(status["leagues"])>=4, status
rows=app.fetch_matches("E0")
assert len(rows)>=380
clubs={r["home"] for r in rows}
recent_date=rows[-1]["date"]
recent=[r for r in rows if r["date"]>=(datetime.strptime(recent_date,"%Y-%m-%d")-timedelta(days=760)).strftime("%Y-%m-%d")]
home=next(n for n in clubs if sum(r["home"]==n for r in recent)>=5)
away=next(n for n in clubs if n!=home and sum(r["away"]==n for r in recent)>=5)
fixture={"id":"offline-test","home":home,"away":away,
         "date":(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}
old=app.get_scheduled_fixtures
app.get_scheduled_fixtures=lambda league:{"fixtures":[fixture],"source":"offline test"}
try:
    p=app.fixture_report("E0","offline-test")
    assert abs(sum(p["full_time"].values())-1)<1e-8
    assert all(abs(v["over"]+v["under"]-1)<1e-8 for v in p["goals"].values())
    assert abs(sum(p["btts"].values())-1)<1e-8
    try:
        app.fixture_report("E0","invented")
    except ValueError:
        pass
    else:raise AssertionError("Unknown fixture was accepted.")
finally:
    app.get_scheduled_fixtures=old
from pathlib import Path
page=Path("web/index.html").read_text(encoding="utf-8")
assert all(s in page for s in ("midnight","emerald","arctic","royal","sunset","rose","slate"))
print("PASS:",status["matches"],"historical matches; fixture guards, probabilities, skins")
