"""레시피 수집기. 출처마다 함수 하나, 결과는 data/recipes.sqlite 한 파일.

  python collect.py rda                 농촌진흥청 농식품정보누리 레시피 API (537건, RDA_FOOD_API_KEY 필요)
  python collect.py 10000recipe [--from 6800000] [--to 7090000] [--workers 4]
                                        만개의레시피 ID 순서대로 훑기(약 28만 건). 끊겨도 다시 돌리면 안 본 ID만 이어서 본다.
"""
import argparse, json, os, re, sqlite3, sys, time, html
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
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
    steps = ld.get("recipeInstructions") or []
    steps = [clean(s["text"] if isinstance(s, dict) else str(s)) for s in steps]
    img = ld.get("image")
    return dict(
        source="10000recipe", src_id=str(rid), name=clean(ld["name"]), summary=clean(ld.get("description", "")) or None,
        category=None, servings=summ[0] or ld.get("recipeYield"), cook_time=summ[1] or ld.get("totalTime"),
        level=summ[2], calorie=None, price=None, url=R10K_URL.format(id=rid),
        image=img[0] if isinstance(img, list) else img, steps=steps, ingredients=ingredients)


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
    ap.add_argument("source", choices=["rda", "10000recipe"])
    ap.add_argument("--from", dest="lo", type=int, default=6800000)
    ap.add_argument("--to", dest="hi", type=int, default=7090000)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    if a.source == "rda":
        collect_rda(os.environ.get("RDA_FOOD_API_KEY") or sys.exit("RDA_FOOD_API_KEY 환경변수가 없다 (koreanfood.rda.go.kr 오픈API 키)"))
    else:
        collect_10000recipe(a.lo, a.hi, a.workers)
