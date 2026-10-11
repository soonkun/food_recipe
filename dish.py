"""음식 단위 통계 - 개별 레시피 대신 "감자조림의 보통 재료 구성과 원가". 밖으로 내보내는 건 이것뿐이다(방침, README).

  python3 dish.py            # 자기검사(제목 → 음식 이름)
  python3 dish.py build      # recipe_cost → dish_cost(음식·레시피 수·지금/석 달 전 원가 중앙값·대표 재료 구성·주재료·난이도 분포)
  python3 dish.py top [--n 30]

음식 이름은 제목에서 뽑는다: 공백·기호로 나눈 토큰 중 음식 꼬리말(조림·볶음·찌개·…)로 끝나는 가장 긴 것. "감자채 볶음"처럼 꼬리말만
따로 떨어진 토큰은 앞 토큰과 붙인다. 못 뽑은 제목(약 1/4)은 통계에서 빠진다. 레시피 5건 미만인 음식도 빠진다 - 한두 건의 통계는
결국 그 레시피를 보여주는 것과 같아서(개별 레시피를 내보내지 않는다는 방침의 하한).
"""
import json
import os
import re
import sqlite3
import statistics
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "recipes.sqlite")
MIN_RECIPES = 5
SCHEMA = """
CREATE TABLE IF NOT EXISTS dish_cost(dish TEXT PRIMARY KEY, n_recipes INTEGER, n_costed INTEGER, cost_now REAL, cost_ref REAL, change_pct REAL,
  main_item TEXT, items TEXT, levels TEXT, asof TEXT);
CREATE INDEX IF NOT EXISTS dish_cost_main ON dish_cost(main_item);
"""
SUFFIX = ("조림|볶음밥|볶음|찌개|국수|국|탕수육|탕|전골|전|무침|구이|튀김|샐러드|주스|에이드|잼|김치|비빔밥|덮밥|김밥|밥|냉면|면|죽|찜|장아찌|짱아찌|카레|파스타|"
          "샌드위치|토스트|케이크|쿠키|빵|수프|스프|떡볶이|나물|말이|부침개|파전|라면|우동|스테이크|피자|버거|떡|강정|스무디|요거트|절임|피클|쌈|돈까스|까스|가스|"
          "산적|갈비|불고기|제육|수육|보쌈|족발|초밥|롤|오믈렛|스크램블|푸딩|젤리|빙수|화채|식혜|밀크티|라떼|리조또|그라탕|파이|타르트|머핀|스콘|도넛|핫도그|만두|"
          "교자|칼국수|수제비|잡채|떡국|순두부|주먹밥|유부초밥|카나페|깍두기|겉절이|소박이|오므라이스|까르보나라|마들렌|동그랑땡|커틀릿|샤브샤브|전병|"
          "쿠키|브라우니|마카롱|티라미수|크로플|와플|팬케이크|핫케이크|또띠아|타코|부리토|샐러드|라자냐|뇨끼|스튜|커리|비빔국수|쫄면|막국수|물회|회덮밥|장조림|"
          "계란찜|계란말이|닭갈비|닭볶음탕|삼계탕|곰탕|설렁탕|육개장|감자탕|해장국|순대국|추어탕|매운탕|지리|찜닭|족발|보쌈|두루치기|불백|쌈밥|비빔면|냉국|"
          "콩국수|어묵탕|어묵볶음|멸치볶음|진미채볶음|오이무침|콩나물무침|시금치무침|부추무침|가지볶음|애호박볶음|감자볶음|떡갈비|월남쌈|탕평채|구절판|신선로")
# 음식이 아닌 토큰(모음·끼니·양념). 꼬리말 하나짜리도 음식이 아니다("볶음" 혼자).
STOP = {"밑반찬", "반찬", "술안주", "안주", "집밥", "간식", "디저트", "브런치", "도시락", "수미네반찬", "고추장", "간장", "양념장", "소스", "장", "청", "차",
        "국", "탕", "전", "밥", "면", "떡", "빵", "죽", "찜", "쌈", "롤", "회", "요리", "레시피", "한끼", "저녁", "아침", "점심", "야식", "식단", "메뉴", "봄나물",
        "제철", "건강식", "다이어트", "이유식", "유아식", "손님상", "명절", "김장", "밥상", "식빵"}
# 꼬리말 중 그것만으로는 음식이 아닌 것("볶음" 혼자). 오므라이스·떡볶이·삼계탕처럼 혼자서도 음식인 꼬리말은 여기 없다.
GENERIC = {"조림", "볶음", "찌개", "국", "탕", "전", "무침", "구이", "튀김", "샐러드", "주스", "에이드", "잼", "김치", "밥", "면", "죽", "찜", "장아찌", "짱아찌",
           "카레", "파스타", "샌드위치", "토스트", "케이크", "쿠키", "빵", "수프", "스프", "나물", "말이", "부침개", "라면", "우동", "스테이크", "피자", "버거", "떡",
           "강정", "스무디", "요거트", "절임", "피클", "쌈", "까스", "가스", "산적", "갈비", "초밥", "롤", "푸딩", "젤리", "파이", "타르트", "머핀", "스콘", "도넛",
           "만두", "교자", "전골", "국수", "냉면", "덮밥", "볶음밥", "비빔밥", "전병", "커리", "스튜", "지리", "냉국", "소박이", "겉절이", "깍두기", "회", "라떼",
           "밀크티", "타코", "부리토", "와플", "팬케이크", "핫케이크", "또띠아", "리조또", "그라탕", "오믈렛", "스크램블", "빙수", "화채", "카나페", "커틀릿"}
_SUF = re.compile(rf"^([가-힣]{{0,10}}?(?:{SUFFIX}))$")
_SPLIT = re.compile(r"[\s,/!?()\[\]{}~♥★☆#:·&+'\"“”‘’|…\-]+")
_STRIP = re.compile(r"(만들기|만드는법|만드는|황금레시피|레시피|요리|하는법|하기|추천|초간단|간단|초스피드)$")


def dish_of(title: str) -> str | None:
    toks = [_STRIP.sub("", t) for t in _SPLIT.split(title or "") if t]
    toks = [t for t in toks if t]
    best = None
    for i, t in enumerate(toks):
        cands = [t]
        if i > 0 and t in GENERIC and re.fullmatch(r"[가-힣]{1,8}", toks[i - 1]) and toks[i - 1] not in STOP:
            cands.append(toks[i - 1] + t)  # '감자채 볶음' → 감자채볶음
        for c in cands:
            m = _SUF.match(c)
            if m and c not in STOP and c not in GENERIC:
                if best is None or len(c) > len(best):
                    best = c
    return best


def build(con) -> None:
    con.executescript(SCHEMA)
    con.execute("DELETE FROM dish_cost")
    asof = con.execute("SELECT asof FROM recipe_cost LIMIT 1").fetchone()[0]
    rows = con.execute("""SELECT r.name, r.level, c.cost_now, c.cost_ref, c.coverage, c.n_priced, c.n_total, c.items, c.main_item
                          FROM recipe r JOIN recipe_cost c ON c.recipe_id = r.id""").fetchall()
    groups: dict[str, list] = {}
    for name, level, now, ref, cov, n_priced, n_total, items, main in rows:
        d = dish_of(name)
        if d:
            groups.setdefault(d, []).append((level, now, ref, cov, n_priced, n_total, items, main))
    out = []
    for dish, recs in groups.items():
        if len(recs) < MIN_RECIPES:
            continue
        # 원가 통계는 재료를 충분히 아는 레시피로만(가격 아는 재료 60%↑·2개↑·재료 2개↑ - 과일주스처럼 재료가 둘뿐인 음식도 음식이다)
        good = [r for r in recs if r[3] >= 0.6 and r[4] >= 2 and r[5] >= 2 and r[2] > 0]
        if len(good) < MIN_RECIPES:
            continue
        now = statistics.median(r[1] for r in good)
        ref = statistics.median(r[2] for r in good)
        item_g: dict[str, list] = {}
        for r in good:
            for kv in (r[6] or "").split(","):
                if kv:
                    k, v = kv.split(":")
                    item_g.setdefault(k, []).append(float(v))
        typical = {k: round(statistics.median(v)) for k, v in item_g.items() if len(v) >= len(good) * 0.4}  # 레시피 40% 이상에 드는 재료
        main = Counter(r[7] for r in good if r[7]).most_common(1)
        levels = dict(Counter(r[0] or "미상" for r in recs))
        out.append((dish, len(recs), len(good), round(now), round(ref), round((now - ref) / ref * 100, 1) if ref else None,
                    main[0][0] if main else None, json.dumps(typical, ensure_ascii=False), json.dumps(levels, ensure_ascii=False), asof))
    con.executemany("INSERT INTO dish_cost VALUES(?,?,?,?,?,?,?,?,?,?)", out)
    con.commit()
    print(f"dish_cost: 음식 {len(out):,}개(레시피 {MIN_RECIPES}건 이상), 제목에서 음식을 뽑은 레시피 {sum(len(v) for v in groups.values()):,}/{len(rows):,}")


def cheaper(con, n=20, min_cost=2000, level=None, main_item=None, per_item=3):
    """석 달 전보다 원가가 내린 음식. 주재료마다 per_item개까지. level은 그 난이도 레시피가 하나라도 있는 음식."""
    where, args = "cost_now >= ? AND cost_ref > 0", [min_cost]
    if main_item:
        where += " AND main_item = ?"
        args.append(main_item)
    rows = con.execute(f"SELECT dish, n_recipes, n_costed, cost_now, cost_ref, change_pct, main_item, items, levels FROM dish_cost "
                       f"WHERE {where} ORDER BY change_pct ASC LIMIT ?", args + [n * 30]).fetchall()
    out, seen = [], Counter()
    for dish, n_r, n_c, now, ref, chg, main, items, levels in rows:
        lv = json.loads(levels)
        if level and not lv.get(level):
            continue
        if seen[main] >= per_item:
            continue
        seen[main] += 1
        out.append(as_dict((dish, n_r, n_c, now, ref, chg, main, items, levels)))
        if len(out) >= n:
            break
    return out


def as_dict(row) -> dict:
    dish, n_r, n_c, now, ref, chg, main, items, levels = row
    return {"dish": dish, "n_recipes": n_r, "n_costed": n_c, "cost_now": now, "cost_ref": ref, "change_pct": chg, "main_item": main,
            "typical_items_g": json.loads(items), "levels": json.loads(levels)}


def lookup(con, query: str, n=5) -> list[dict]:
    rows = con.execute("SELECT dish, n_recipes, n_costed, cost_now, cost_ref, change_pct, main_item, items, levels FROM dish_cost "
                       "WHERE dish LIKE ? ORDER BY n_recipes DESC LIMIT ?", (f"%{query.strip()}%", n)).fetchall()
    return [as_dict(r) for r in rows]


def _selfcheck():
    cases = {"감자조림 만들기": "감자조림", "두부감자조림 만드는방법/칼칼하고 담백하게 두부조림 레시피": "두부감자조림", "감자채 볶음": "감자채볶음",
             "다슬기 무침": "다슬기무침", "꼬들 오이 짱아찌": "오이짱아찌", "콜라비깍두기": "콜라비깍두기", "부엌에서 음식물 쓰레기 줄이는 법": None,
             "술안주 추천": None, "오므라이스 만드는법": "오므라이스", "영양가득 옥수수감자전": "옥수수감자전", "밑반찬 멸치볶음": "멸치볶음",
             "샤인머스캣 에이드 만드는법 샤인머스캣음료": "샤인머스캣에이드", "볶음": None, "집밥백선생 사과잼 만들기": "사과잼"}
    for t, want in cases.items():
        got = dish_of(t)
        assert got == want, (t, got, want)
    print("dish ok")


if __name__ == "__main__":
    con = sqlite3.connect(DB, timeout=60)
    if "build" in sys.argv:
        build(con)
    elif "top" in sys.argv:
        n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 30
        for d in cheaper(con, n):
            print(f"{-d['change_pct']:5.1f}%↓ {d['cost_ref']:>7,}→{d['cost_now']:>7,}원  {d['dish']} [{d['n_recipes']}건·주재료 {d['main_item']}] "
                  f"{','.join(f'{k}:{v}' for k, v in d['typical_items_g'].items())[:70]}")
    else:
        _selfcheck()
