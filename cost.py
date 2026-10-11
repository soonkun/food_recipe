"""레시피 원가와 "3달 전보다 싸진 음식". 의존성 없음.

  python3 cost.py prices            # price 표 → item_price(출처·품목·날짜·원/kg): 포장 단위를 g으로 풀고 시장별 중앙값
  python3 cost.py recipes           # recipe×ingredient → recipe_cost(지금 원가·석 달 전 원가·가격을 아는 재료 비율)
  python3 cost.py top [--n 30]      # 석 달 전 대비 원가가 많이 내린 레시피(가격을 아는 재료 60% 이상, 원가 2,000원 이상)
  python3 cost.py item [양파 ...]   # 품목의 지금/석 달 전 원/kg

지금 = 최근 14일 중앙값, 석 달 전 = 76~104일 전 중앙값(주 1회 조사라 창을 넓게 잡는다). 둘 다 있는 품목만 비교한다.
출처가 여럿이면(서울시 소매 + 도매시장) 품목별로 도매를 먼저 쓴다 - 폭락 신호가 먼저 나타나는 쪽(소유자 방침).
"""
import argparse, datetime as dt, os, re, sqlite3, statistics, sys

from norm import PIECE_DEFAULT, PIECE_G, canon, grams

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "recipes.sqlite")
SCHEMA = """
CREATE TABLE IF NOT EXISTS item_price(source TEXT NOT NULL, item TEXT NOT NULL, date TEXT NOT NULL, won_per_kg REAL NOT NULL, n INTEGER,
  PRIMARY KEY(source, item, date));
CREATE TABLE IF NOT EXISTS recipe_cost(recipe_id INTEGER PRIMARY KEY, asof TEXT, cost_now REAL, cost_ref REAL, coverage REAL,
  n_priced INTEGER, n_total INTEGER, items TEXT, main_item TEXT);
CREATE INDEX IF NOT EXISTS recipe_cost_change ON recipe_cost(coverage, cost_now);
CREATE INDEX IF NOT EXISTS recipe_cost_main ON recipe_cost(main_item);
"""
SOURCE_RANK = {"mafra": 0, "seoul": 1}  # 작은 쪽을 먼저 쓴다
NOW_DAYS, REF_FROM, REF_TO = 14, 104, 76
MAX_G = 5000  # 재료 하나 5kg 넘게 읽히면 양 표기를 잘못 읽은 것 - 거기서 자른다
_W = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\b", re.I)
_X = re.compile(r"x\s*(\d+)")            # 135g x 2개입
_IN = re.compile(r"\((\d+)\s*(매|개|팩)입\)")  # 180g(10매입)
_CNT = re.compile(r"(\d+)\s*(개|마리|단|포기|통|송이|팩|병|봉|망|포|캔)")
# 서울시 조사의 크기 등급·길이 - 한 마리 무게(g)
SIZE_G = {("갈치", "대"): 900, ("갈치", "중"): 600, ("갈치", "소"): 350, ("고등어", "대"): 500, ("고등어", "중"): 350,
          ("명태", "40cm"): 500, ("오징어", "30cm"): 300, ("조기", "20cm"): 120, ("키위", "1팩"): 1000, ("맛김", "16팩"): 80}


def pack_grams(item, unit, qty):
    """포장 한 단위의 g. '1kg'·'100g'·'860ml'·'135g x 2개입'·'180g(10매입)'은 무게로, '10개'·'1마리'·'1단'·'대'는 품목별 한 개 무게로."""
    u = (unit or "").strip()
    q = (qty or "").strip()
    m = _W.search(u)
    if m:
        g = float(m.group(1)) * {"kg": 1000, "g": 1, "ml": 1, "l": 1000}[m.group(2).lower()]
        x = _X.search(u)
        return g * (float(x.group(1)) if x else 1)
    if (item, u) in SIZE_G:
        return SIZE_G[(item, u)]
    for text in (u, q):  # 단위 칸의 수('16팩', '3개입')가 먼저, 포장 칸('1봉')은 그 다음
        c = _CNT.search(text)
        if c:
            return float(c.group(1)) * PIECE_G.get(item, PIECE_DEFAULT)
    return None


def db():
    con = sqlite3.connect(DB, timeout=60)
    con.executescript(SCHEMA)
    return con


def build_item_prices(con):
    """price 표 전부 → item_price. 같은 (출처·품목·날짜)의 시장별 원/kg 중앙값. 한 출처가 포장을 못 풀면 그 행은 건너뛴다."""
    con.execute("DELETE FROM item_price")
    rows = con.execute("SELECT source, item, date, unit, qty, price FROM price WHERE price > 0").fetchall()
    bucket = {}
    skipped = {}
    for source, item, date, unit, qty, price in rows:
        if source == "mafra":
            g = 1000.0  # 도매는 kg당 가격으로 넣어 둔다(prices.py mafra)
        else:
            g = pack_grams(item, unit, qty)
        if not g:
            skipped[(item, unit, qty)] = skipped.get((item, unit, qty), 0) + 1
            continue
        bucket.setdefault((source, item, date), []).append(price / g * 1000)
    con.executemany("INSERT INTO item_price VALUES(?,?,?,?,?)",
                    [(s, i, d, statistics.median(v), len(v)) for (s, i, d), v in bucket.items()])
    con.commit()
    print(f"item_price: {len(bucket):,}행, 품목 {len({k[1] for k in bucket})}개, 못 푼 포장 {sum(skipped.values()):,}행")
    for (item, unit, qty), n in sorted(skipped.items(), key=lambda t: -t[1])[:10]:
        print(f"  건너뜀 {item} [{unit}|{qty}] {n}")


def item_levels(con, asof=None):
    """품목 → (지금 원/kg, 석 달 전 원/kg). 출처는 SOURCE_RANK 순으로 먼저 둘 다 있는 것."""
    asof = asof or dt.date.today()
    d = lambda n: (asof - dt.timedelta(days=n)).isoformat()
    now = {}
    ref = {}
    for source, item, when, won in con.execute("SELECT source, item, date, won_per_kg FROM item_price WHERE date >= ?", (d(REF_FROM),)):
        key = (source, item)
        if when >= d(NOW_DAYS):
            now.setdefault(key, []).append(won)
        elif when <= d(REF_TO):
            ref.setdefault(key, []).append(won)
    levels = {}
    for (source, item), v in now.items():
        if (source, item) not in ref:
            continue
        cand = (statistics.median(v), statistics.median(ref[(source, item)]))
        if item not in levels or SOURCE_RANK.get(source, 9) < SOURCE_RANK.get(levels[item][2], 9):
            levels[item] = (cand[0], cand[1], source)
    return levels


def build_recipe_costs(con):
    levels = item_levels(con)
    asof = dt.date.today().isoformat()
    con.execute("DELETE FROM recipe_cost")
    cur = con.execute("SELECT recipe_id, name, qty FROM ingredient ORDER BY recipe_id")
    out = []
    current, rows = None, []

    def flush():
        if current is None:
            return
        n_total = len(rows)
        cost_now = cost_ref = 0.0
        n_priced = 0
        items = {}
        share = {}
        for name, qty in rows:
            item = canon(name)
            if item is None or item not in levels:
                continue
            g = grams(item, qty)
            if g is None:
                g = PIECE_G.get(item, PIECE_DEFAULT)  # 양을 안 적은 재료는 한 개분
            g = min(g, MAX_G)
            won_now, won_ref, _ = levels[item]
            cost_now += g * won_now / 1000
            cost_ref += g * won_ref / 1000
            n_priced += 1
            items[item] = round(items.get(item, 0) + g)
            share[item] = share.get(item, 0) + g * won_ref / 1000
        if n_total:
            main_item = max(share, key=share.get) if share else None  # 석 달 전 원가에서 비중이 가장 큰 재료
            out.append((current, asof, round(cost_now), round(cost_ref), round(n_priced / n_total, 3), n_priced, n_total,
                        ",".join(f"{k}:{v}" for k, v in items.items()), main_item))

    for recipe_id, name, qty in cur:
        if recipe_id != current:
            flush()
            current, rows = recipe_id, []
        rows.append((name, qty))
    flush()
    con.executemany("INSERT INTO recipe_cost VALUES(?,?,?,?,?,?,?,?,?)", out)
    con.commit()
    print(f"recipe_cost: {len(out):,}개 레시피, 가격을 아는 품목 {len(levels)}개, 기준일 {asof}")


def cheaper(con, n=30, min_coverage=0.6, min_cost=2000, min_total=4, min_priced=3, per_item=3, level=None, main_item=None):
    """석 달 전보다 원가가 내린 레시피. 음식다운 것만(재료 4개 이상, 가격 아는 재료 3개 이상 - 세척법·보관법 같은 글을 거른다),
    주재료마다 per_item개까지(한 품목이 폭락하면 그 요리만 서른 개 나오는 것을 막는다). 돌려주는 것은 dict 목록(MCP·화면용)."""
    where = "c.coverage >= ? AND c.cost_now >= ? AND c.cost_ref > 0 AND c.n_total >= ? AND c.n_priced >= ?"
    args = [min_coverage, min_cost, min_total, min_priced]
    if level:
        where += " AND r.level = ?"
        args.append(level)
    if main_item:
        where += " AND c.main_item = ?"
        args.append(main_item)
    rows = con.execute(f"""
        SELECT r.id, r.name, r.level, r.servings, r.cook_time, c.cost_now, c.cost_ref, c.coverage, c.items, c.main_item, r.url, r.source
        FROM recipe_cost c JOIN recipe r ON r.id = c.recipe_id WHERE {where}
        ORDER BY (c.cost_ref - c.cost_now) * 1.0 / c.cost_ref DESC LIMIT ?""", args + [n * 40]).fetchall()
    out, seen = [], {}
    for rid, name, lvl, servings, cook_time, now, ref, cov, items, main, url, source in rows:
        if seen.get(main, 0) >= per_item:
            continue
        seen[main] = seen.get(main, 0) + 1
        out.append({"id": rid, "name": name, "level": lvl, "servings": servings, "cook_time": cook_time, "cost_now": now, "cost_ref": ref,
                    "change_pct": round((now - ref) / ref * 100, 1), "coverage": cov, "main_item": main,
                    "items_g": dict(kv.split(":") for kv in items.split(",") if kv), "url": url, "source": source})
        if len(out) >= n:
            break
    return out


def top(con, n=30, **kw):
    for r in cheaper(con, n, **kw):
        print(f"{-r['change_pct']:5.1f}%↓ {r['cost_ref']:>7,.0f}→{r['cost_now']:>7,.0f}원  {r['name']} [{r['level'] or '-'}·{r['servings'] or '-'}"
              f"·재료 {r['coverage']:.0%}·주재료 {r['main_item']}]  {r['url']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["prices", "recipes", "top", "item"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--n", type=int, default=30)
    a = ap.parse_args()
    con = db()
    if a.cmd == "prices":
        build_item_prices(con)
    elif a.cmd == "recipes":
        build_recipe_costs(con)
    elif a.cmd == "top":
        top(con, a.n)
    else:
        levels = item_levels(con)
        for item in a.args or sorted(levels):
            if item in levels:
                now, ref, src = levels[item]
                print(f"{item}: {now:,.0f}원/kg (석 달 전 {ref:,.0f}, {(now-ref)/ref*100:+.0f}%, {src})")
            else:
                print(f"{item}: 가격 없음")
