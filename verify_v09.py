import ast
from pathlib import Path

for path in ("app.py","football_api.py"):
    ast.parse(Path(path).read_text(encoding="utf-8-sig"),filename=path)

import app
from football_api import FD_CODES
assert set(FD_CODES)=={"E0","SP1","D1","I1","F1"}
app.initialize();app.import_data()
assert app.status()["matches"]>=3500
assert len(app.status()["leagues"])>=4
print("PASS Python parse, supported leagues, imported historical records")
