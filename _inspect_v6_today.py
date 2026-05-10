import json, sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8")
db = sqlite3.connect(r"C:\Users\sji48\ksat_gang\stock_data.db")
cands = json.loads(open(r"C:\Users\sji48\ksat_gang\candidates_v4.json", encoding="utf-8").read())["tradable_candidates"]
n_pass30 = n_pass50 = n_ma224 = n_ma112 = 0
for c in cands:
    t, name, close = c["ticker"], c["name"], c["close"]
    r = db.execute(
        "SELECT MAX(고가), MIN(저가) FROM (SELECT 고가, 저가 FROM daily_data WHERE 종목코드=? ORDER BY 날짜 DESC LIMIT 60)",
        (t,),
    ).fetchone()
    h, low = r[0], r[1]
    box = (h - low) / low * 100 if low else 0
    ma_row = db.execute(
        "SELECT ma112, ma224 FROM daily_indicators WHERE 종목코드=? AND 날짜=20260424",
        (t,),
    ).fetchone()
    ma112 = ma_row[0] if ma_row and ma_row[0] else None
    ma224 = ma_row[1] if ma_row and ma_row[1] else None
    ok112 = "Y" if ma112 and close > ma112 else "N"
    ok224 = "Y" if ma224 and close > ma224 else "N"
    ok30 = "Y" if box <= 30 else "N"
    ok50 = "Y" if box <= 50 else "N"
    if ok112 == "Y": n_ma112 += 1
    if ok224 == "Y": n_ma224 += 1
    if ok30 == "Y":  n_pass30 += 1
    if ok50 == "Y":  n_pass50 += 1
    print(f"{name:<14} close={close:>7,} ma112={ma112 or 0:>9,.0f} ma224={ma224 or 0:>9,.0f} "
          f"ma112OK={ok112} ma224OK={ok224} box={box:>6.1f}% box30={ok30} box50={ok50}")
print(f"\n[요약] ma112 통과 {n_ma112}/17, ma224 통과 {n_ma224}/17, "
      f"box≤30% {n_pass30}/17, box≤50% {n_pass50}/17")
