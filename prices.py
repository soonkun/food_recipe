"""농산물 가격 수집. 출처마다 함수 하나, 결과는 data/recipes.sqlite의 price 표.

  python3 prices.py seoul [--since 2025-01-01]
      서울시 열린데이터 "생필품 농수축산물 가격"(ListNecessariesPricesService, 전통시장·대형마트 주 1회 조사).
      최신순으로 1000건씩 받아 since 날짜 전까지. 이미 받은 행(P_SEQ)은 건너뛴다. 키: data/.env의 SEOUL_API_KEY.
"""
import argparse, datetime as dt, json, os, sqlite3, sys, time
from urllib.request import urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "recipes.sqlite")
SCHEMA = """
CREATE TABLE IF NOT EXISTS price(
  source TEXT NOT NULL, seq TEXT NOT NULL, date TEXT NOT NULL, item TEXT NOT NULL, species TEXT,
  unit TEXT, qty TEXT, price INTEGER NOT NULL, market TEXT, market_type TEXT,
  PRIMARY KEY(source, seq));
CREATE INDEX IF NOT EXISTS price_item_date ON price(item, date);
"""


def env(name):
    for line in open(os.path.join(HERE, "data", ".env")):
        k, _, v = line.strip().partition("=")
        if k == name and v:
            return v
    sys.exit(f"data/.env에 {name}이 없다")


def db():
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    return con


SEOUL_URL = "http://openapi.seoul.go.kr:8088/{key}/json/ListNecessariesPricesService/{start}/{end}/"


def seoul_row(r):
    return ("seoul", str(int(r["P_SEQ"])), r["P_DATE"], r["PRDLST_NM"].strip(), r.get("SPCIES", "").strip(),
            r.get("UNIT", "").strip(), r.get("QY", "").strip(), int(float(r["A_PRICE"])),
            r.get("M_NAME", "").strip(), r.get("M_TYPE_NAME", "").strip())


def collect_seoul(since, key):
    con = db()
    start, page, n = 1, 1000, 0
    while True:
        for t in range(3):
            try:
                d = json.load(urlopen(SEOUL_URL.format(key=key, start=start, end=start + page - 1), timeout=120))
                break
            except Exception as e:
                if t == 2:
                    raise
                time.sleep(10)
        body = d.get("ListNecessariesPricesService") or sys.exit(f"seoul API: {d}")
        if body["RESULT"]["CODE"] != "INFO-000":
            sys.exit(f"seoul API: {body['RESULT']}")
        rows = [seoul_row(r) for r in body["row"] if r.get("A_PRICE") not in (None, "", "0")]
        n += con.executemany("INSERT OR IGNORE INTO price VALUES(?,?,?,?,?,?,?,?,?,?)", rows).rowcount
        con.commit()
        oldest = min(r[2] for r in rows) if rows else ""
        print(f"  {start}-{start+page-1}/{body['list_total_count']} oldest {oldest}, new rows {n}", flush=True)
        if start + page > body["list_total_count"] or oldest < since:
            break
        start += page
    print(f"seoul: {n} new rows; total {con.execute('SELECT count(*) FROM price WHERE source=?', ('seoul',)).fetchone()[0]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["seoul"])
    ap.add_argument("--since", default=(dt.date.today() - dt.timedelta(days=100)).isoformat())
    a = ap.parse_args()
    collect_seoul(a.since, env("SEOUL_API_KEY"))
