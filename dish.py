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
import random
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
  main_item TEXT, items TEXT, levels TEXT, asof TEXT, meal TEXT, cuisine TEXT, servings REAL, cost_now_ps REAL, cost_ref_ps REAL, role TEXT);
CREATE INDEX IF NOT EXISTS dish_cost_main ON dish_cost(main_item);
CREATE INDEX IF NOT EXISTS dish_cost_meal ON dish_cost(meal, cuisine);
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
        "제철", "건강식", "다이어트", "이유식", "유아식", "손님상", "명절", "김장", "밥상", "식빵", "설탕", "황설탕", "흑설탕", "백설탕", "사탕", "물엿",
        "올리고당", "조청", "요리당", "맛탕"}
# 꼬리말과 붙이지 않는 앞 토큰: 관형어("만든", "넣은", "맛있는", "간단한") - 붙이면 "만든샐러드" 같은 가짜 음식이 된다
_MODIFIER_TOKEN = re.compile(r"(는|은|던|든|한|운|인|할|될|된|없이|넣고|빼고)$")
# 꼬리말 중 그것만으로는 음식이 아닌 것("볶음" 혼자). 오므라이스·떡볶이·삼계탕처럼 혼자서도 음식인 꼬리말은 여기 없다.
GENERIC = {"조림", "볶음", "찌개", "국", "탕", "전", "무침", "구이", "튀김", "샐러드", "주스", "에이드", "잼", "김치", "밥", "면", "죽", "찜", "장아찌", "짱아찌",
           "카레", "파스타", "샌드위치", "토스트", "케이크", "쿠키", "빵", "수프", "스프", "나물", "말이", "부침개", "라면", "우동", "스테이크", "피자", "버거", "떡",
           "강정", "스무디", "요거트", "절임", "피클", "쌈", "까스", "가스", "산적", "갈비", "초밥", "롤", "푸딩", "젤리", "파이", "타르트", "머핀", "스콘", "도넛",
           "만두", "교자", "전골", "국수", "냉면", "덮밥", "볶음밥", "비빔밥", "전병", "커리", "스튜", "지리", "냉국", "소박이", "겉절이", "깍두기", "회", "라떼",
           "밀크티", "타코", "부리토", "와플", "팬케이크", "핫케이크", "또띠아", "리조또", "그라탕", "오믈렛", "스크램블", "빙수", "화채", "카나페", "커틀릿"}
_SUF = re.compile(rf"^([가-힣]{{0,10}}?(?:{SUFFIX}))$")

# ---- 분류(소유자 2026-10-11): 끼니 네 가지 × 요리 계통 여섯 가지. 이름의 낱말로 고르고 못 고르면 식사·한식.
MEAL_RULES = (
    ("음료", r"주스|쥬스|에이드|스무디|라떼|밀크티|식혜|수정과|칵테일|쉐이크|셰이크|커피|음료|차$|티$|우유$|두유|요거트음료|레모네이드|모히토|청량|탄산|주$|와인|막걸리|맥주|소주|칵테일|하이볼"),
    ("디저트·간식", r"잼|케이크|케익|쿠키|빵|파이|타르트|머핀|스콘|도넛|푸딩|젤리|빙수|화채|탕후루|맛탕|마카롱|브라우니|와플|팬케이크|핫케이크|요거트|아이스크림|샤베트|셔벗|"
                r"시럽|^[가-힣]*청$|과일샐러드|초콜릿|초코|캔디|사탕|젤라또|티라미수|크로플|마들렌|피낭시에|카스테라|카스텔라|식빵$|베이글|크루아상|크래커|약과|강정$|"
                r"다식|양갱|경단|송편|인절미|설기|백설기|떡$|호떡|붕어빵|핫도그|츄러스|과자|칩$|스낵|시리얼|그래놀라|견과|말랭이|절편|꿀떡|찹쌀떡|모찌|도라야키|푸딩"),
    ("기타", r"반죽|육수|양념장|양념$|소스$|드레싱|마요네즈|가루$|페이스트|스톡|육수|밑간|절임물|피클물|시즈닝|버터$|오일$|기름$|장$|식초$|물$"),
)
# 강정은 황태·코다리·닭이면 식사다(과자 강정은 쌀·견과·깨·콩).
MEAL_EXCEPTIONS = {"식사": r"(황태|코다리|닭|새우|두부|오징어|돼지|소고기|감자|고구마|연근|멸치)강정$|떡국$|떡볶이|떡갈비|떡만두|떡찜|떡볶이$|가래떡구이|빵가루|식빵피자|식빵토스트"}
CUISINE_RULES = (
    ("중식", r"짜장|짬뽕|탕수육|마파두부|깐풍|유린기|꿔바로우|양장피|고추잡채|마라|훠궈|딤섬|춘권|어향|궁보|팔보채|유산슬|멘보샤|라조기|동파육|탄탄면|우육면|중화|짜사이|볶음면|"
           r"칠리새우|크림새우|깐쇼|북경|사천|홍쇼|오향|샤오롱바오|딴딴면|마라탕|마라샹궈|지삼선|경장육사|부추잡채|양꼬치|꿔바|차슈|완탕|훈툰"),
    ("일식", r"초밥|스시|사시미|라멘|라면덮밥|우동|소바|돈부리|규동|가츠동|오야코동|돈까스|돈카츠|가라아게|오코노미야키|타코야키|야키토리|나베|스키야키|샤브샤브|규카츠|"
           r"미소|오니기리|타마고|텐동|소보로|니쿠자가|오뎅탕|사케동|연어덮밥|회덮밥|가츠|카라아게|차완무시|유부초밥|이나리|야키소바|오므라이스|함바그|함박|나가사키|"
           r"장어덮밥|우나기|텐푸라|튀김덮밥|오차즈케|타마고야키|규카츠|모찌|도라야키|자루소바|냉모밀|메밀소바"),
    ("아시아", r"쌀국수|팟타이|똠얌|분짜|반미|나시고렝|미고렝|커리|카레|탄두리|난$|사테|월남쌈|라이스페이퍼|라크사|푸팟퐁|카오팟|마살라|비리야니|팟카파오|쏨땀|"
            r"짜조|분보훈|반쎄오|렌당|사모사|차파티|로티|똠양|그린커리|레드커리|옐로커리|코코넛커리|타이|베트남|인도|태국|인도네시아|말레이|싱가포르|필리핀|아도보|룸피아"),
    ("양식", r"파스타|스파게티|피자|스테이크|리조또|리조토|그라탕|그라탱|샐러드|샌드위치|버거|토스트|오믈렛|스크램블|수프|스프|스튜|라자냐|뇨끼|까르보나라|카르보나라|알리오|"
           r"봉골레|라구|파니니|부리또|부리토|타코|퀘사디아|나초|케밥|바게트|크림|치즈|베이컨|소시지|핫도그|프렌치|이탈리안|프랑스|스페인|빠에야|파에야|라따뚜이|"
           r"브런치|에그베네딕트|프리타타|키쉬|키슈|감바스|아히요|뇨끼|폴렌타|미트볼|미트소스|로스트|바비큐|립$|폭립|감자튀김|프라이|코울슬로|샐러드$|포케|타파스|"
           r"슈니첼|굴라쉬|보르시|필라프|쿠스쿠스|후무스|팔라펠|샥슈카|부라타|카프레제|브루스케타|마르게리타|로제|크림파스타|뇨끼|리코타|머스타드|치킨$|윙$|너겟"),
)


def category_of(dish: str) -> tuple[str, str]:
    """(끼니, 계통). 끼니: 식사·음료·디저트·간식·기타, 계통: 한식·중식·일식·양식·아시아·기타. 한식은 기본값이다."""
    meal = "식사"
    for name, pat in MEAL_RULES:
        if re.search(pat, dish):
            meal = name
            break
    if meal != "식사" and re.search(MEAL_EXCEPTIONS["식사"], dish):
        meal = "식사"
    cuisine = "한식"
    for name, pat in CUISINE_RULES:
        if re.search(pat, dish):
            cuisine = name
            break
    if meal in ("음료", "디저트·간식") and cuisine == "한식" and not re.search(r"식혜|수정과|화채|약과|강정|다식|양갱|경단|송편|인절미|설기|떡|호떡|붕어빵|한과|유과|미숫가루|쌍화|맛탕", dish):
        cuisine = "기타"  # 주스·케이크 같은 보편 디저트·음료는 한식이 아니다
    return meal, cuisine


# ---- 식사 안의 역할(소유자 2026-10-11: 점심도 저녁도 양배추사과샐러드를 추천하면 안 된다).
# 주식(밥·면), 국찌개, 메인(고기·생선 요리), 반찬(무침·나물·작은 조림·볶음), 간단(샌드위치·토스트·샐러드·죽 - 아침·브런치).
PROTEIN = r"돼지|소고기|쇠고기|한우|닭|오리|삼겹|목살|갈비|등갈비|생선|고등어|갈치|조기|명태|황태|코다리|동태|북어|오징어|낙지|주꾸미|쭈꾸미|문어|새우|전복|꽃게|게|연어|참치|장어|삼치|가자미|꽁치|훈제|차돌|불고기|제육|수육|보쌈|족발|스테이크|함박|돈까스|돈가스|가츠|커틀릿|치킨|윙|너겟|소시지|소세지|햄|스팸|베이컨|두부|계란|달걀|어묵|오뎅|탕수육|깐풍|가라아게|닭갈비|닭볶음탕|찜닭|두루치기|떡갈비|산적|동그랑땡|미트볼"
ROLE_RULES = (
    ("간단", r"샌드위치|토스트|오믈렛|스크램블|팬케이크|핫케이크|와플|프렌치토스트|에그|브런치|죽$|오트밀|시리얼|그래놀라|주먹밥|유부초밥|삼각김밥|카나페|핑거푸드"),
    ("국찌개", r"국$|탕$|찌개|전골|스튜|수프|스프|나베|샤브샤브|해장국|곰탕|설렁탕|육개장|미역국|된장국|콩나물국|북엇국|북어국|순두부|부대찌개|김치찜$|갈비탕|삼계탕|감자탕|닭곰탕|매운탕|지리|라멘|쌀국수|우동|칼국수|수제비|떡국|만둣국|만두국|짬뽕|짬뽕탕"),
    ("주식", r"밥$|볶음밥|비빔밥|덮밥|김밥|죽|국수|면$|냉면|막국수|라면|파스타|스파게티|리조또|리조토|피자|버거|떡볶이|만두$|팟타이|짜장|카레|커리|오므라이스|돈부리|규동|초밥|스시|롤$|타코|부리또|부리토|퀘사디아|라자냐|뇨끼|쫄면|비빔면|콩국수|잔치국수|소바|메밀|파에야|빠에야|필라프|볶음면|야키소바|라이스|도시락|까르보나라|카르보나라|알리오|봉골레|라구|로제|아라비아타|페스토|마카로니|우동$|소면|중면|쫄면|막국수|물회"),
)


def role_of(dish: str) -> str:
    for name, pat in ROLE_RULES:
        if re.search(pat, dish):
            return name
    if re.search(r"샐러드|무침|나물|장아찌|짱아찌|김치$|깍두기|겉절이|피클|절임|초무침|냉채|생채|쌈$|장$|젓갈|젓$|지$", dish):
        return "반찬"
    if re.search(r"구이|찜|조림|볶음|튀김|강정|말이|전$|부침개|파전|까스|가스|스테이크|갈비|불고기|제육|수육|보쌈|족발|두루치기|떡갈비|산적|탕수육|깐풍|유린기|꿔바로우|마파두부|찹스테이크|로스트|바비큐|립$|폭립|감바스|아히요|프라이$", dish):
        return "메인" if re.search(PROTEIN, dish) else "반찬"
    return "메인" if re.search(PROTEIN, dish) else "반찬"


# 끼니 때에 어울리는 역할(먼저 적은 것이 우선)
WHEN_ROLES = {"아침": ("간단", "국찌개", "주식"), "점심": ("주식", "메인", "국찌개"), "저녁": ("메인", "국찌개", "주식"), "야식": ("주식", "간단", "메인"),
              "간식": ("간단",), "반찬": ("반찬",), "손님": ("메인", "국찌개", "주식")}
DEFAULT_ROLES = ("주식", "메인", "국찌개")  # 때를 말하지 않은 "음식 추천"에 반찬·샐러드만 내지 않게

_SERV = re.compile(r"(\d+)\s*인분")


def servings_of(text: str | None) -> float | None:
    m = _SERV.search(text or "")
    return float(m.group(1)) if m else None
_SPLIT = re.compile(r"[\s,/!?()\[\]{}~♥★☆#:·&+'\"“”‘’|…\-]+")
_STRIP = re.compile(r"(만들기|만드는법|만드는|황금레시피|레시피|요리|하는법|하기|추천|초간단|간단|초스피드)$")


def dish_of(title: str) -> str | None:
    toks = [_STRIP.sub("", t) for t in _SPLIT.split(title or "") if t]
    toks = [t for t in toks if t]
    best = None
    for i, t in enumerate(toks):
        cands = [t]
        if (i > 0 and t in GENERIC and re.fullmatch(r"[가-힣]{1,8}", toks[i - 1]) and toks[i - 1] not in STOP
                and not _MODIFIER_TOKEN.search(toks[i - 1])):
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
    rows = con.execute("""SELECT r.name, r.level, c.cost_now, c.cost_ref, c.coverage, c.n_priced, c.n_total, c.items, c.main_item, r.servings
                          FROM recipe r JOIN recipe_cost c ON c.recipe_id = r.id""").fetchall()
    groups: dict[str, list] = {}
    for name, level, now, ref, cov, n_priced, n_total, items, main, serv in rows:
        d = dish_of(name)
        if d:
            groups.setdefault(d, []).append((level, now, ref, cov, n_priced, n_total, items, main, servings_of(serv)))
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
        # 인분: 레시피가 적은 "N인분"의 중앙값과, 레시피별 1인분 원가의 중앙값(인분을 적은 레시피만)
        with_serv = [r for r in good if r[8]]
        servings = statistics.median(r[8] for r in with_serv) if with_serv else None
        now_ps = statistics.median(r[1] / r[8] for r in with_serv) if with_serv else None
        ref_ps = statistics.median(r[2] / r[8] for r in with_serv) if with_serv else None
        meal, cuisine = category_of(dish)
        out.append((dish, len(recs), len(good), round(now), round(ref), round((now - ref) / ref * 100, 1) if ref else None,
                    main[0][0] if main else None, json.dumps(typical, ensure_ascii=False), json.dumps(levels, ensure_ascii=False), asof,
                    meal, cuisine, servings, round(now_ps) if now_ps else None, round(ref_ps) if ref_ps else None,
                    role_of(dish) if meal == "식사" else None))
    con.executemany("INSERT INTO dish_cost VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    con.commit()
    print(f"dish_cost: 음식 {len(out):,}개(레시피 {MIN_RECIPES}건 이상), 제목에서 음식을 뽑은 레시피 {sum(len(v) for v in groups.values()):,}/{len(rows):,}")
    print("  끼니:", dict(Counter(o[10] for o in out)), " 계통:", dict(Counter(o[11] for o in out)), " 역할:", dict(Counter(o[15] for o in out if o[15])))


COLS = "dish, n_recipes, n_costed, cost_now, cost_ref, change_pct, main_item, items, levels, meal, cuisine, servings, cost_now_ps, cost_ref_ps, role"
MEALS = ("식사", "음료", "디저트·간식", "기타")
CUISINES = ("한식", "중식", "일식", "양식", "아시아", "기타")


def cheaper(con, n=20, min_cost=2000, level=None, main_item=None, per_item=3, meal="식사", cuisine=None, when=None, role=None,
            shuffle=True):
    """석 달 전보다 원가가 내린 음식. 기본은 끼니=식사이고 역할은 주식·메인·국찌개(반찬·샐러드만 내지 않게); `when`(아침·점심·저녁…)이
    오면 그때에 맞는 역할로, `role`이 오면 그 역할만. 주재료마다 per_item개, 역할마다 고르게. 같은 질문에 늘 같은 답이 나오지 않게
    상위 후보 안에서 조금 섞는다(하락폭이 클수록 앞에 오되 순서가 고정되진 않는다)."""
    where, args = "cost_now >= ? AND cost_ref > 0", [min_cost]
    if main_item:
        where += " AND main_item = ?"
        args.append(main_item)
    if meal and meal != "전체":
        where += " AND meal = ?"
        args.append(meal)
    if cuisine and cuisine != "전체":
        where += " AND cuisine = ?"
        args.append(cuisine)
    roles = None
    if role and role != "전체":
        roles = (role,)
    elif when and when in WHEN_ROLES:
        roles = WHEN_ROLES[when]
    elif (meal or "식사") == "식사" and not main_item:
        roles = DEFAULT_ROLES
    if roles:
        where += f" AND role IN ({','.join('?' * len(roles))})"
        args += list(roles)
    rows = con.execute(f"SELECT {COLS} FROM dish_cost WHERE {where} ORDER BY change_pct ASC LIMIT ?", args + [n * 30]).fetchall()
    if shuffle and len(rows) > n:
        pool = rows[: n * 4]
        rng = random.Random()
        rows = sorted(pool, key=lambda r: (r[5] or 0) * rng.uniform(0.6, 1.4)) + rows[n * 4:]
    out, seen, seen_role = [], Counter(), Counter()
    cap_role = max(2, -(-n // max(1, len(roles or DEFAULT_ROLES))))  # 역할마다 n/역할수(올림), 최소 2
    for row in rows:
        lv = json.loads(row[8])
        if level and not lv.get(level):
            continue
        if seen[row[6]] >= per_item:
            continue
        if roles and seen_role[row[14]] >= cap_role:
            continue
        seen[row[6]] += 1
        seen_role[row[14]] += 1
        out.append(as_dict(row))
        if len(out) >= n:
            break
    return out


def as_dict(row) -> dict:
    dish, n_r, n_c, now, ref, chg, main, items, levels, meal, cuisine, servings, now_ps, ref_ps, role = row
    return {"dish": dish, "meal": meal, "cuisine": cuisine, "role": role, "n_recipes": n_r, "n_costed": n_c,
            "servings": servings, "cost_now": now, "cost_ref": ref, "cost_per_serving_now": now_ps, "cost_per_serving_ref": ref_ps,
            "change_pct": chg, "main_item": main, "typical_items_g": json.loads(items), "levels": json.loads(levels)}


def lookup(con, query: str, n=5) -> list[dict]:
    rows = con.execute(f"SELECT {COLS} FROM dish_cost WHERE dish LIKE ? ORDER BY n_recipes DESC LIMIT ?", (f"%{query.strip()}%", n)).fetchall()
    return [as_dict(r) for r in rows]


def _selfcheck():
    cases = {"감자조림 만들기": "감자조림", "두부감자조림 만드는방법/칼칼하고 담백하게 두부조림 레시피": "두부감자조림", "감자채 볶음": "감자채볶음",
             "다슬기 무침": "다슬기무침", "꼬들 오이 짱아찌": "오이짱아찌", "콜라비깍두기": "콜라비깍두기", "부엌에서 음식물 쓰레기 줄이는 법": None,
             "술안주 추천": None, "오므라이스 만드는법": "오므라이스", "영양가득 옥수수감자전": "옥수수감자전", "밑반찬 멸치볶음": "멸치볶음",
             "샤인머스캣 에이드 만드는법 샤인머스캣음료": "샤인머스캣에이드", "볶음": None, "집밥백선생 사과잼 만들기": "사과잼"}
    for t, want in cases.items():
        got = dish_of(t)
        assert got == want, (t, got, want)
    cats = {"포도주스": ("음료", "기타"), "사과잼": ("디저트·간식", "기타"), "황태강정": ("식사", "한식"), "쌀강정": ("디저트·간식", "한식"),
            "감자조림": ("식사", "한식"), "짜장면": ("식사", "중식"), "돈까스": ("식사", "일식"), "까르보나라": ("식사", "양식"),
            "쌀국수": ("식사", "아시아"), "부침개반죽": ("기타", "한식"), "떡볶이": ("식사", "한식"), "식혜": ("음료", "한식"),
            "티라미수": ("디저트·간식", "기타"), "샤인머스캣에이드": ("음료", "기타"), "과일샐러드": ("디저트·간식", "양식"),
            "고구마맛탕": ("디저트·간식", "한식")}
    assert dish_of("설탕 없이 만든 샐러드") is None and dish_of("맛있는 감자 샐러드") == "감자샐러드" and dish_of("설탕 시럽") is None
    for d, want in cats.items():
        assert category_of(d) == want, (d, category_of(d), want)
    assert servings_of("6인분 이상") == 6 and servings_of("2인분") == 2 and servings_of("아무나") is None
    roles = {"양배추사과샐러드": "반찬", "감자조림": "반찬", "황태양념구이": "메인", "돼지고기말이": "메인", "김치찌개": "국찌개", "제육볶음": "메인",
             "김치볶음밥": "주식", "잔치국수": "주식", "멸치볶음": "반찬", "프렌치토스트": "간단", "삼계탕": "국찌개", "닭갈비": "메인", "두부조림": "메인",
             "알감자버터구이": "반찬", "돈까스": "메인", "까르보나라": "주식", "오이무침": "반찬", "계란말이": "메인", "미역국": "국찌개"}
    for d, want in roles.items():
        assert role_of(d) == want, (d, role_of(d), want)
    print("dish ok")


if __name__ == "__main__":
    con = sqlite3.connect(DB, timeout=60)
    if "build" in sys.argv:
        build(con)
    elif "top" in sys.argv:
        n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 30
        for d in cheaper(con, n):
            ps = f"{d['servings']:g}인분·1인 {d['cost_per_serving_now']:,}원" if d["servings"] else "인분 미상"
            print(f"{-d['change_pct']:5.1f}%↓ {d['cost_ref']:>7,}→{d['cost_now']:>7,}원 ({ps})  {d['dish']} [{d['meal']}·{d['cuisine']}·{d['role']}·{d['n_recipes']}건·"
                  f"주재료 {d['main_item']}] {','.join(f'{k}:{v}' for k, v in d['typical_items_g'].items())[:50]}")
    else:
        _selfcheck()
