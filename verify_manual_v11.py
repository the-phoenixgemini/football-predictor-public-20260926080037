"""Offline manual-pairing integration checks; no football API key needed."""
import ast
from pathlib import Path
for p in ("app.py","football_api.py"):
    ast.parse(Path(p).read_text(encoding="utf-8-sig"),filename=p)
import app
app.initialize()
app.import_data()
s=app.status()
assert s["matches"]>=3500
assert len(s["leagues"])>=4
e=next(l for l in s["leagues"] if l["name"]=="E0")
home,away="Arsenal","Chelsea"
assert home in e["teams"] and away in e["teams"]
# Reject API fixture dependency: user enters the fixture.
app.get_scheduled_fixtures=lambda *a,**k:(_ for _ in ()).throw(AssertionError("Fixture lookup was called"))
x=app.manual_report({"league":"E0","home":home,"away":away})
assert x["fixture"]["manual"] is True
assert x["source"]=="User-entered bookmaker pairing"
assert abs(sum(x["full_time"].values())-1)<1e-8
assert abs(sum(x["btts"].values())-1)<1e-8
assert all(abs(d["over"]+d["under"]-1)<1e-8 for d in x["goals"].values())
assert abs(sum(x["double_chance"].values())-2)<1e-8
assert x["team_latest"]["home"] and x["team_latest"]["away"]
for bad in [
    {"league":"E0","home":"Arsenal","away":"Arsenal"},
    {"league":"F1","home":"Arsenal","away":"Chelsea"},
    {"league":"E0","home":"Imaginary City","away":"Chelsea"},
    {"league":"E0","home":"","away":"Chelsea"}
]:
    try:app.manual_report(bad)
    except ValueError:pass
    else:raise AssertionError("Invalid pairing accepted: "+str(bad))
page=Path("web/index.html").read_text(encoding="utf-8")
assert "/api/manual-report" in page and "fair odds" in page.lower()
assert "Load scheduled fixtures" not in page
assert all('value="'+t+'"' in page for t in ("midnight","emerald","arctic","royal","sunset","rose","slate"))
print("PASS: manual pairing, scores, probabilities, fair odds display, data guards; ",s["matches"],"matches")
