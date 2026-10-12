"""농산물 가격 수집. 출처마다 함수 하나, 결과는 data/recipes.sqlite의 price 표.

  python3 prices.py seoul [--since 2025-01-01]
      서울시 열린데이터 "생필품 농수축산물 가격"(ListNecessariesPricesService, 전통시장·대형마트 주 1회 조사).
      최신순으로 1000건씩 받아 since 날짜 전까지. 이미 받은 행(P_SEQ)은 건너뛴다. 키: data/.env의 SEOUL_API_KEY.
  python3 prices.py mafra [--since 2025-01-01] [--max-requests 900]
      공공데이터포털 "한국농수산식품유통공사_전국 공영도매시장 정산정보"(data.go.kr 15141809, apis.data.go.kr/B552845/katSale/trades).
      날짜마다 1000건씩. 원가 계산이 보는 두 창(최근 14일, 76~104일 전)만 받고, 이미 받은 날짜는 건너뛴다.
      가격은 받을 때 원/kg으로 바꿔 넣는다(unit이 kg·g인 행만). 키: data/.env의 MAFRA_API_KEY(data.go.kr 일반 인증키, 64자리).
      이 호스트는 브라우저가 아닌 User-Agent(curl·Python-urllib)의 요청을 응답 없이 버린다 - 반드시 UA를 붙인다.
"""
import argparse, datetime as dt, json, os, sqlite3, sys, time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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


MAFRA_URL = "https://apis.data.go.kr/B552845/katSale/trades"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) food_recipe collector"}
_KG = {"kg": 1.0, "g": 0.001, "㎏": 1.0, "ｇ": 0.001}


def mafra_row(r):
    """정산 행 하나 → price 행(원/kg). 단위가 kg·g가 아니면(개·마리·단) None - 무게를 모른다."""
    unit = (r.get("unit_nm") or "").strip().lower()
    try:
        qty = float(r.get("unit_qty") or 0)
        avg = float(r.get("avgprc") or 0)
    except ValueError:
        return None
    if unit not in _KG or qty <= 0 or avg <= 0:
        return None
    won_per_kg = avg / (qty * _KG[unit])
    date = (r.get("trd_clcln_ymd") or "").replace("-", "")[:8]
    date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    seq = "|".join(str(r.get(k) or "") for k in ("trd_clcln_ymd", "whsl_mrkt_cd", "corp_cd", "gds_sclsf_cd", "unit_cd", "pkg_cd", "sz_cd", "grd_cd", "plor_cd", "trd_se"))
    return ("mafra", seq, date, (r.get("gds_mclsf_nm") or "").strip(), (r.get("gds_sclsf_nm") or "").strip(),
            "kg", "1", int(round(won_per_kg)), (r.get("whsl_mrkt_nm") or "").strip(), (r.get("corp_nm") or "").strip())


def mafra_dates(since, today=None):
    """원가 계산(cost.py)이 보는 두 창만: 최근 14일과 76~104일 전. 그 밖의 날은 받아도 쓰이지 않는다."""
    today = today or dt.date.today()
    lo = dt.date.fromisoformat(since)
    out = []
    for n in list(range(0, 15)) + list(range(76, 105)):
        d = today - dt.timedelta(days=n)
        if d >= lo:
            out.append(d.isoformat())
    return out


def collect_mafra(since, key, max_requests=900):
    # ponytail: 날짜당 수만 행이라 요청 수가 곧 비용이다 - data.go.kr 개발계정 한도(하루 1,000회) 안에서 멈춘다.
    con = db()
    done = {d for (d,) in con.execute("SELECT DISTINCT date FROM price WHERE source='mafra'")}
    n = requests = 0
    for day in mafra_dates(since):
        if day in done:
            continue
        page = 1
        while requests < max_requests:
            q = urlencode({"serviceKey": key, "pageNo": page, "numOfRows": 1000, "returnType": "json", "cond[trd_clcln_ymd::EQ]": day})
            requests += 1
            try:
                d = json.load(urlopen(Request(f"{MAFRA_URL}?{q}", headers=UA), timeout=120))
            except Exception as e:
                print(f"  {day} p{page}: {e}", flush=True)
                break
            if "OpenAPI_ServiceResponse" in d:  # 게이트웨이 거절(키 미등록·한도 초과)
                sys.exit(f"mafra API: {d['OpenAPI_ServiceResponse'].get('cmmMsgHeader')}")
            body = d.get("response", {}).get("body", {})
            items = (body.get("items") or {}).get("item") or []
            rows = [x for x in (mafra_row(r) for r in items) if x]
            n += con.executemany("INSERT OR IGNORE INTO price VALUES(?,?,?,?,?,?,?,?,?,?)", rows).rowcount
            con.commit()
            total = int(body.get("totalCount") or 0)
            print(f"  {day} p{page}: {len(items)} rows ({len(rows)} by weight) / {total}", flush=True)
            if page * 1000 >= total or not items:
                break
            page += 1
        if requests >= max_requests:
            print(f"요청 한도 {max_requests}회에 닿아 멈춤 - 내일 이어서 받는다")
            break
    print(f"mafra: {n} new rows, {requests} requests; total {con.execute('SELECT count(*) FROM price WHERE source=?', ('mafra',)).fetchone()[0]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["seoul", "mafra"])
    ap.add_argument("--since", default=(dt.date.today() - dt.timedelta(days=100)).isoformat())
    ap.add_argument("--max-requests", type=int, default=900)
    a = ap.parse_args()
    if a.source == "seoul":
        collect_seoul(a.since, env("SEOUL_API_KEY"))
    else:
        collect_mafra(a.since, env("MAFRA_API_KEY"), a.max_requests)
