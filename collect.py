"""레시피 수집기. 출처마다 함수 하나, 결과는 data/recipes.sqlite 한 파일.

  python collect.py nongsaro            농사로 '이달의 음식 정보' Open API (api.nongsaro.go.kr, NONGSARO_API_KEY 필요)
  python collect.py rda                 농식품 공공데이터 포털 레시피 API (537건, RDA_FOOD_API_KEY 필요 - 그 포털 계정 키)
  python collect.py foodsafety [--file 경로…]
                                        식품안전나라 조리식품 레시피 DB(COOKRCP01, 1,100여 건, 공공누리). FOODSAFETY_API_KEY로 API를 직접 받거나,
                                        이 서버에서 그 호스트가 막혀 있으면 다른 곳에서 받은 JSON(…/COOKRCP01/json/1/1000 응답)을 --file로 넣는다.
                                        --file 없이 부르면 data/_incoming/foodsafety/*.json 도 함께 읽는다.
  키는 환경변수 또는 data/.env (SEOUL·MAFRA·NONGSARO·RDA_FOOD·FOODSAFETY_API_KEY).
  python collect.py 10000recipe [--from 6800000] [--to 7090000] [--workers 4]
                                        만개의레시피 ID 순서대로 훑기(약 28만 건). 끊겨도 다시 돌리면 안 본 ID만 이어서 본다.
"""
import argparse, json, os, re, sqlite3, sys, time, html
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "recipes.sqlite")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) food_recipe collector"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipe(
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, src_id TEXT NOT NULL,
  name TEXT NOT NULL, summary TEXT, category TEXT, servings TEXT, cook_time TEXT, level TEXT,
  calorie TEXT, price TEXT, url TEXT, image TEXT, steps TEXT, fetched_at TEXT,
  UNIQUE(source, src_id));
CREATE TABLE IF NOT EXISTS ingredient(
  recipe_id INTEGER NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
  seq INTEGER, name TEXT NOT NULL, qty TEXT, kind TEXT);
CREATE INDEX IF NOT EXISTS ingredient_name ON ingredient(name);
CREATE TABLE IF NOT EXISTS scan(source TEXT, src_id INTEGER, status TEXT, PRIMARY KEY(source, src_id));
"""


def env(name):
    """환경변수 → data/.env 순. 없으면 None."""
    if os.environ.get(name):
        return os.environ[name]
    path = os.path.join(os.path.dirname(DB), ".env")
    if os.path.exists(path):
        for line in open(path):
            k, _, v = line.strip().partition("=")
            if k == name and v:
                return v
    return None


def db():
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    return con


def save(con, r):
    """r: dict(source, src_id, name, ..., steps=[str], ingredients=[(name, qty, kind)])"""
    cols = ["source", "src_id", "name", "summary", "category", "servings", "cook_time", "level",
            "calorie", "price", "url", "image"]
    con.execute("DELETE FROM recipe WHERE source=? AND src_id=?", (r["source"], r["src_id"]))
    cur = con.execute(
        f"INSERT INTO recipe({','.join(cols)},steps,fetched_at) VALUES({','.join('?'*len(cols))},?,datetime('now'))",
        [r.get(c) for c in cols] + [json.dumps(r.get("steps", []), ensure_ascii=False)])
    con.execute("DELETE FROM ingredient WHERE recipe_id=?", (cur.lastrowid,))
    con.executemany("INSERT INTO ingredient VALUES(?,?,?,?,?)",
                    [(cur.lastrowid, i, n, q, k) for i, (n, q, k) in enumerate(r.get("ingredients", []), 1)])


def get(url, tries=3):
    for t in range(tries):
        try:
            with urlopen(Request(url, headers=UA), timeout=30) as f:
                return f.read().decode("utf-8", "replace")
        except HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 503) or t == tries - 1:
                if t == tries - 1:
                    raise
            time.sleep(5 * (t + 1))
        except (URLError, TimeoutError, ConnectionError):
            if t == tries - 1:
                raise
            time.sleep(5 * (t + 1))


# ---------------- 농촌진흥청 농식품정보누리 ----------------
RDA_BASE = "http://211.237.50.150:7080/openapi/{key}/json/{grid}/{start}/{end}"
RDA_GRIDS = {"base": "Grid_20150827000000000226_1", "ingr": "Grid_20150827000000000227_1", "step": "Grid_20150827000000000228_1"}


def rda_rows(key, grid, page=1000):
    start, rows = 1, []
    while True:
        d = json.loads(get(RDA_BASE.format(key=key, grid=grid, start=start, end=start + page - 1)))
        if "result" in d and d["result"].get("code", "").startswith("ERROR"):
            sys.exit(f"RDA API: {d['result']['message']}")
        body = d[grid]
        rows += body.get("row", [])
        if body["endRow"] >= body["totalCnt"]:
            return rows
        start += page


def collect_rda(key):
    base = rda_rows(key, RDA_GRIDS["base"])
    ingr, step = {}, {}
    for r in rda_rows(key, RDA_GRIDS["ingr"]):
        ingr.setdefault(r["RECIPE_ID"], []).append((r["IRDNT_SN"], r["IRDNT_NM"].strip(), r["IRDNT_CPCTY"].strip(), r["IRDNT_TY_NM"]))
    for r in rda_rows(key, RDA_GRIDS["step"]):
        step.setdefault(r["RECIPE_ID"], []).append((r["COOKING_NO"], r["COOKING_DC"].strip()))
    con = db()
    for b in base:
        rid = b["RECIPE_ID"]
        save(con, dict(
            source="rda", src_id=str(rid), name=b["RECIPE_NM_KO"].strip(), summary=b.get("SUMRY"),
            category=f"{b.get('NATION_NM','')}/{b.get('TY_NM','')}/{b.get('IRDNT_CODE','')}",
            servings=b.get("QNT"), cook_time=b.get("COOKING_TIME"), level=b.get("LEVEL_NM"),
            calorie=b.get("CALORIE"), price=b.get("PC_NM"), url=None, image=None,
            steps=[s for _, s in sorted(step.get(rid, []))],
            ingredients=[(n, q, k) for _, n, q, k in sorted(ingr.get(rid, []))]))
    con.commit()
    print(f"rda: {len(base)} recipes")


# ---------------- 농사로 이달의 음식 (api.nongsaro.go.kr/service/monthFd) ----------------
NS_URL = "http://api.nongsaro.go.kr/service/monthFd/{op}?apiKey={key}&{q}"
NS_YEARS = (2016, 2017, 2018, 2019)  # 12개월이 다 찬 해(2026-07 타인 실측). 2015·2020·2023은 몇 달뿐이라 올해만 덧붙인다.


def ns_text(s):
    """농사로 상세 필드는 HTML. <br>·</p>는 줄바꿈, 나머지 태그는 지운다."""
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", "\n", s or "")
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def ns_parse(xml_text):
    """응답 XML → [ {태그: 값} ]. resultCode가 00이 아니면 멈춘다."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_text)
    code = root.findtext(".//resultCode")
    if code not in (None, "", "00"):
        sys.exit(f"농사로 API: {code} {root.findtext('.//resultMsg')}")
    return [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]


def ns_items(key, op, **q):
    return ns_parse(get(NS_URL.format(op=op, key=key, q=urlencode(q))))


def ns_steps(text):
    return [re.sub(r"^\s*\d+[.)]\s*", "", ln).strip() for ln in ns_text(text).split("\n") if ln.strip()]


def ns_fetch(key, years):
    """(월, 목록 항목, 상세) 를 하나씩. 농사로가 막힌 서버에서는 5초 만에 포기한다."""
    import socket
    try:
        socket.create_connection(("api.nongsaro.go.kr", 80), timeout=5).close()
    except OSError as e:
        sys.exit(f"api.nongsaro.go.kr:80 에 TCP가 닿지 않는다({e}). 이 서버에서는 농사로가 막혀 있다 - 닿는 PC에서 --dump 로 받아 --file 로 넣는다")
    seen = set()
    for y in years:
        for m in range(1, 13):
            for f in ns_items(key, "monthNewFdLst", thisYear=y, thisMonth=f"{m:02d}"):
                no = f.get("cntntsNo")
                if no and no not in seen:
                    seen.add(no)
                    yield m, f, (ns_items(key, "monthNewFdDtl", cntntsNo=no) or [{}])[0]


def collect_nongsaro(rows):
    con, n = db(), 0
    for m, f, d in rows:
        no = f["cntntsNo"]
        img = f.get("rtnFileCours", "") and f"{f['rtnFileCours'].rstrip('/')}/{f.get('rtnStreFileNm', '')}"
        save(con, dict(
            source="nongsaro", src_id=no, name=(d.get("fdNm") or f.get("fdNm", "")).strip(),
            summary=ns_text(d.get("rmInfo")) or None, category=f"{m}월/{f.get('fdSeCode', '')}",
            servings=ns_text(d.get("ckngmhrlsUntInfo")) or None, calorie=d.get("energyQy") or None,
            url=f"https://www.nongsaro.go.kr/portal/ps/psr/psrb/monthNewFdDtl.ps?cntntsNo={no}", image=img or None,
            steps=ns_steps(d.get("ckngMthInfo")), ingredients=parse_fs_parts(ns_text(d.get("matrlInfo")))))
        n += 1
    con.commit()
    print(f"nongsaro: {n} recipes")


# ---------------- 식품안전나라 COOKRCP01 ----------------
FS_URL = "http://openapi.foodsafetykorea.go.kr/api/{key}/COOKRCP01/json/{start}/{end}"
FS_PAGE = "https://www.foodsafetykorea.go.kr/api/openApiInfo.do?menu_grp=MENU_GRP31&menu_no=661&svc_no=COOKRCP01"
_FS_ITEM = re.compile(r"^\s*(.*?[가-힣a-zA-Z)\]])\s*([\d½¼¾⅓⅔][\d./~\-\s]*[^,]*|약간|적당량|조금)?\s*$")


def parse_fs_parts(text):
    """RCP_PARTS_DTLS → [(이름, 양, 구분)]. '●양념장 : 고추장 20g(1큰술), …' 처럼 줄·구분 머리말이 있고 쉼표로 나뉜다."""
    out = []
    kind = "재료"
    for line in re.split(r"[\n\r]+", text or ""):
        line = line.strip().lstrip("●•·-* ")
        if not line:
            continue
        m = re.match(r"^([가-힣A-Za-z ]{1,12})\s*[:：]\s*(.*)$", line)
        if m:
            kind, line = m.group(1).strip() or kind, m.group(2)
        for item in re.split(r",|、", line):
            item = item.strip().rstrip(".")
            if not item:
                continue
            # 이름은 첫 숫자(또는 '약간')가 나오기 전까지
            mm = re.match(r"^(.*?)(?=\s*(?:\d|½|¼|¾|⅓|⅔|약간|적당량|조금)|$)(.*)$", item)
            name, qty = (mm.group(1).strip(), mm.group(2).strip()) if mm else (item, "")
            name = re.sub(r"\(.*?\)$", "", name).strip()  # '새송이버섯(소)' → 새송이버섯
            if name:
                out.append((name, qty, kind))
    return out


def fs_rows_from_api(key):
    rows, start, page = [], 1, 1000
    while True:
        d = json.loads(get(FS_URL.format(key=key, start=start, end=start + page - 1)))
        body = d.get("COOKRCP01") or sys.exit(f"foodsafety API: {str(d)[:300]}")
        rows += body.get("row", [])
        total = int(body.get("total_count") or 0)
        print(f"  foodsafety API {start}-{start+page-1}/{total}", flush=True)
        if start + page > total or not body.get("row"):
            return rows
        start += page


def fs_rows_from_files(paths):
    rows = []
    for path in paths:
        d = json.load(open(path, encoding="utf-8"))
        body = d.get("COOKRCP01") or d
        rows += body.get("row", body if isinstance(body, list) else [])
        print(f"  {os.path.basename(path)}: {len(body.get('row', []))}건")
    return rows


def collect_foodsafety(files):
    paths = list(files or [])
    inbox = os.path.join(os.path.dirname(DB), "_incoming", "foodsafety")
    if not files and os.path.isdir(inbox):
        paths = sorted(os.path.join(inbox, f) for f in os.listdir(inbox) if f.lower().endswith(".json"))
    key = env("FOODSAFETY_API_KEY")
    rows = fs_rows_from_files(paths) if paths else []
    if not rows and key:
        rows = fs_rows_from_api(key)
    if not rows:
        sys.exit("식품안전나라 자료가 없다: FOODSAFETY_API_KEY(API) 또는 --file / data/_incoming/foodsafety/*.json")
    con = db()
    n = 0
    for r in rows:
        if not r.get("RCP_NM"):
            continue
        steps = [re.sub(r"\s*[a-z]$", "", (r.get(f"MANUAL{i:02d}") or "").strip()) for i in range(1, 21)]
        save(con, dict(
            source="foodsafety", src_id=str(r.get("RCP_SEQ")), name=r["RCP_NM"].strip(), summary=r.get("RCP_NA_TIP") or None,
            category=f"{r.get('RCP_PAT2','')}/{r.get('RCP_WAY2','')}", servings=r.get("INFO_WGT") or None, cook_time=None, level=None,
            calorie=r.get("INFO_ENG") or None, price=None, url=FS_PAGE, image=r.get("ATT_FILE_NO_MAIN") or None,
            steps=[s for s in steps if s], ingredients=parse_fs_parts(r.get("RCP_PARTS_DTLS"))))
        n += 1
    con.commit()
    print(f"foodsafety: {n} recipes")


# ---------------- 만개의레시피 ----------------
R10K_URL = "https://www.10000recipe.com/recipe/{id}"
_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_GROUP = re.compile(r'<b class="ready_ingre3_tt">\[?(.*?)\]?</b>(.*?)</ul>', re.S)
_ITEM = re.compile(r'<div class="ingre_list_name">(.*?)</div>\s*<span class="ingre_list_ea">(.*?)</span>', re.S)
_SUMMARY = re.compile(r'view2_summary_info\d[^>]*>([^<]*)<')
_TAG = re.compile(r"<[^>]+>")


def clean(s):
    return html.unescape(_TAG.sub("", s)).replace("​", "").strip()


def parse_10000recipe(h, rid):
    m = _LD.search(h)
    if not m:
        return None
    ld = json.loads(m.group(1))
    if ld.get("@type") != "Recipe" or not ld.get("name"):
        return None
    ingredients = []
    i = h.find('class="ready_ingre3"')
    if i > 0:
        for grp, body in _GROUP.findall(h[i:i + 40000]):
            for name, qty in _ITEM.findall(body):
                ingredients.append((clean(name), clean(qty), clean(grp)))
    if not ingredients:  # 재료 칸이 없는 옛 글은 JSON-LD의 "코다리 2마리" 문자열을 그대로
        ingredients = [(clean(s), "", "재료") for s in ld.get("recipeIngredient", [])]
    summ = [clean(s) for s in _SUMMARY.findall(h)] + [None] * 3
    # 저작권: 작성자 글(조리문·설명·사진)은 저장하지 않는다. 이름·재료·난이도·시간·링크만 (원가 계산용).
    return dict(
        source="10000recipe", src_id=str(rid), name=clean(ld["name"]), summary=None,
        category=None, servings=summ[0] or ld.get("recipeYield"), cook_time=summ[1] or ld.get("totalTime"),
        level=summ[2], calorie=None, price=None, url=R10K_URL.format(id=rid),
        image=None, steps=[], ingredients=ingredients)


def fetch_10000recipe(rid):
    try:
        h = get(R10K_URL.format(id=rid))
        r = parse_10000recipe(h, rid) if h and len(h) > 1000 else None
        return rid, ("ok" if r else "missing"), r
    except Exception as e:  # 다음 실행에서 다시 본다
        return rid, "error", None


def collect_10000recipe(lo, hi, workers):
    con = db()
    seen = {i for (i,) in con.execute("SELECT src_id FROM scan WHERE source='10000recipe' AND status IN ('ok','missing')")}
    todo = [i for i in range(lo, hi + 1) if i not in seen]
    print(f"10000recipe: {len(todo)} ids to visit ({lo}..{hi}), {len(seen)} already seen")
    n = ok = 0
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        for rid, status, r in ex.map(fetch_10000recipe, todo):
            if r:
                save(con, r); ok += 1
            con.execute("INSERT OR REPLACE INTO scan VALUES('10000recipe',?,?)", (rid, status))
            n += 1
            if n % 200 == 0:
                con.commit()
                print(f"  {n}/{len(todo)} visited, {ok} saved, {n/(time.time()-t0):.1f} id/s", flush=True)
    con.commit()
    print(f"10000recipe: visited {n}, saved {ok}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["nongsaro", "rda", "10000recipe", "foodsafety"])
    ap.add_argument("--file", nargs="*", help="foodsafety: 받아 둔 COOKRCP01 JSON 파일들 / nongsaro: --dump 로 받은 JSON")
    ap.add_argument("--dump", help="nongsaro: DB 대신 이 JSON 파일로 저장(농사로가 닿는 PC에서 받아 서버로 옮길 때)")
    ap.add_argument("--from", dest="lo", type=int, default=6800000)
    ap.add_argument("--to", dest="hi", type=int, default=7090000)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    if a.source == "nongsaro":
        if a.file:  # 다른 PC에서 --dump 로 받은 JSON
            collect_nongsaro(tuple(r) for p in a.file for r in json.load(open(p, encoding="utf-8")))
        else:
            rows = ns_fetch(env("NONGSARO_API_KEY") or sys.exit("NONGSARO_API_KEY가 없다 (농사로 마이페이지 → OPEN API 신청내역의 '이달의 음식 정보' 인증키)"),
                            NS_YEARS + (time.localtime().tm_year,))
            if a.dump:
                json.dump(list(rows), open(a.dump, "w", encoding="utf-8"), ensure_ascii=False)
                print(f"nongsaro: dumped to {a.dump}")
            else:
                collect_nongsaro(rows)
    elif a.source == "rda":
        collect_rda(env("RDA_FOOD_API_KEY") or sys.exit("RDA_FOOD_API_KEY가 없다 (환경변수 또는 data/.env; 농식품 공공데이터 포털 data.mafra.go.kr 키 - 그 계정이 레시피 기본·재료·과정정보 API를 신청해 두어야 한다)"))
    elif a.source == "foodsafety":
        collect_foodsafety(a.file)
    else:
        collect_10000recipe(a.lo, a.hi, a.workers)
