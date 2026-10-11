"""food_recipe MCP 서버(의존성 없음, 127.0.0.1:8230). MOPAN이 "MCP 서버 관리"에 http://127.0.0.1:8230/mcp 로 등록해 쓴다.

  python3 server.py [--port 8230]

방침(README): 민간 레시피 사이트 자료는 **음식 단위 통계**(감자조림의 보통 재료 구성과 원가)를 내는 데만 쓰고, 개별 레시피·링크·원문 재료 목록은
밖으로 내보내지 않는다. 레시피 본문과 링크는 공공 자료(농진청·식품안전나라)만 보여준다(public_recipes).

도구
  price_movers     석 달 전보다 많이 내린/오른 품목 + 내린 품목마다 싸진 음식 둘. "요즘 뭐가 싸졌나"에 먼저 부른다.
  cheaper_dishes   석 달 전보다 원가가 내린 음식(난이도·주재료로 좁히기). 음식 이름·레시피 수·원가·변동률·대표 재료 구성.
  dish_cost        음식 이름으로 보통 재료 구성과 원가.
  item_prices      품목의 지금/석 달 전 원/kg.
  public_recipes   공공 자료 레시피(농진청·식품안전나라)를 이름으로 찾아 재료·링크를 준다 - 본문을 보여줄 수 있는 유일한 출처.

프로토콜은 soonkun/mcp_server/maps/server.py와 같다(JSON-RPC over HTTP POST /mcp: initialize·tools/list·tools/call). GET /healthz.
"""
import argparse, json, sqlite3, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cost
import dish

PROTOCOL_VERSION = "2025-06-18"
PUBLIC_SOURCES = ("rda", "foodsafety")
NOTE_DISH = ("원가는 그 음식 레시피 여러 건의 중앙값(가격을 아는 재료 기준, 양념 포함)이고 cost_now/cost_ref는 레시피 한 번 분량(보통 servings인분), "
             "cost_per_serving_*은 1인분 환산이다 - 답할 때는 'N인분 기준 X원(1인분 Y원)'으로 말한다. meal(식사·음료·디저트·간식·기타)과 "
             "cuisine(한식·중식·일식·양식·아시아·기타)은 이름으로 나눈 분류다. 특정 사이트의 레시피가 아니라 음식 단위 통계라 링크는 없다. "
             "만드는 법을 물으면 public_recipes(공공 자료)를 먼저 찾고, 없으면 사과하거나 '제공할 수 없다'고 끝내지 말고 그 음식의 일반적인 "
             "조리 순서를 네 상식으로 3~6단계로 친절히 설명한다(특정 사이트의 레시피를 옮기는 게 아니라 누구나 아는 조리법이므로 괜찮다 - "
             "'일반적인 방법'이라고 한 줄만 밝힌다). 대표 재료 구성(typical_items_g)을 재료 안내로 삼는다.")
TOOLS = [
    {"name": "price_movers",
     "description": "석 달 전보다 값이 많이 내린 품목과 오른 품목(원/kg, 변동률) - '요즘 뭐가 싸졌나/올랐나', '가격이 급락한 농산물'처럼 품목 시세가 "
                    "주제일 때 부른다. 내린 품목마다 그 재료의 싸진 음식 둘을 덤으로 준다. '싸게 먹을 수 있는 음식·요리 추천'이 주제면 cheaper_dishes가 맞다. "
                    "내린 원인(풍작·수입·소비 감소 등)은 모르니 품목명으로 인터넷 조사 도구에 묻는다.",
     "inputSchema": {"type": "object", "properties": {"n": {"type": "integer", "description": "내린 것·오른 것 각각 몇 개(기본 10)"}}}},
    {"name": "cheaper_dishes",
     "description": "석 달 전보다 재료 원가가 내린 음식(요리)을 추천한다. '무엇으로 뭘 해 먹나', '○○를 주재료로 싸게 먹을 수 있는 음식은', "
                    "'초급 요리 추천'처럼 음식·요리·메뉴를 묻는 질문은 이 도구다(item_prices는 품목 가격만 준다). 기본은 끼니가 '식사'인 음식 중 "
                    "주식·메인·국찌개만(반찬·샐러드만 내지 않게) - 질문에 아침·점심·저녁·야식이 있으면 when을 꼭 준다(그때에 맞는 역할로 고른다), "
                    "반찬·국·찌개만 원하면 role을. 음료·디저트를 물으면 meal을 준다. 결과는 음식 이름·끼니·계통·역할(주식·메인·국찌개·반찬·간단)·"
                    "레시피 수·보통 인분·한 번 분량 원가와 1인분 원가(지금/석 달 전)·변동률·주재료·대표 재료 구성(품목:g)·난이도 분포. "
                    "음식 단위 통계라 특정 레시피나 링크는 없다. 같은 질문이라도 상위 후보 안에서 조금씩 다르게 나온다.",
     "inputSchema": {"type": "object", "properties": {
         "n": {"type": "integer", "description": "몇 개(기본 10, 최대 50)"},
         "when": {"type": "string", "description": "끼니 때: 아침·점심·저녁·야식·간식·손님. 질문에 있으면 반드시 넣는다"},
         "role": {"type": "string", "description": "식사 안의 역할로 좁히기: 주식·메인·국찌개·반찬·간단·전체"},
         "meal": {"type": "string", "description": "끼니: 식사(기본)·음료·디저트·간식·기타·전체"},
         "cuisine": {"type": "string", "description": "계통: 한식·중식·일식·양식·아시아(동남아·인도)·기타·전체(기본)"},
         "level": {"type": "string", "description": "난이도로 좁히기: 아무나·초급·중급·고급(그 난이도 레시피가 있는 음식)"},
         "main_item": {"type": "string", "description": "주재료 품목으로 좁히기(예: 포도·감자·사과). item_prices의 품목명"},
         "min_cost": {"type": "integer", "description": "지금 원가 하한(원, 기본 2000)"}}}},
    {"name": "dish_cost",
     "description": "음식 이름(일부)으로 그 음식의 보통 재료 구성(품목:g)과 지금/석 달 전 원가, 레시피 수, 난이도 분포를 준다.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "음식 이름 또는 그 일부(예: 감자조림)"},
         "n": {"type": "integer", "description": "몇 개(기본 5)"}}, "required": ["query"]}},
    {"name": "item_prices",
     "description": "농축수산물 품목(양파·돼지고기·포도…)의 지금 가격과 석 달 전 가격(원/kg)과 변동률. 품목 시세만 묻는 질문용이다 - 음식·요리·"
                    "메뉴를 물으면 cheaper_dishes 또는 dish_cost를 부른다. 품목을 안 주면 가격을 아는 품목 전부.",
     "inputSchema": {"type": "object", "properties": {
         "items": {"type": "array", "items": {"type": "string"}, "description": "품목명(예: 양파, 돼지고기, 포도)"}}}},
    {"name": "public_recipes",
     "description": "공공 자료 레시피(농촌진흥청 농식품정보누리, 식품안전나라)를 이름으로 찾아 재료 목록·조리 순서·열량·원가·출처 링크를 준다. "
                    "만드는 법을 보여 줄 수 있는 유일한 출처(공공누리, 출처 표기). 없으면 빈 목록.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "음식 이름 또는 그 일부"},
         "n": {"type": "integer", "description": "몇 개(기본 5)"}}, "required": ["query"]}},
]


def _con():
    return sqlite3.connect(cost.DB, timeout=30)


def _levels(con):
    return cost.item_levels(con)


def _asof(con):
    row = con.execute("SELECT asof FROM dish_cost LIMIT 1").fetchone()
    return row[0] if row else None


def t_cheaper(a):
    con = _con()
    n = max(1, min(int(a.get("n") or 10), 50))
    meal = (a.get("meal") or "식사").replace("디저트", "디저트·간식").replace("디저트·간식·간식", "디저트·간식").replace("간식", "디저트·간식").replace("디저트·디저트·간식", "디저트·간식")
    rows = dish.cheaper(con, n, min_cost=int(a.get("min_cost") or 2000), level=a.get("level") or None, main_item=a.get("main_item") or None,
                        meal=meal, cuisine=a.get("cuisine") or None, when=a.get("when") or None, role=a.get("role") or None)
    tip = ("저녁·점심 추천은 메인 하나 + 국/찌개 하나 + 주식(밥·면) 하나로 한 상을 짜서 권하면 좋다. 반찬·샐러드는 곁들임으로만."
           if (a.get("when") in ("점심", "저녁", "손님") or not a.get("when")) else "")
    return {"asof": _asof(con), "meal": meal, "cuisine": a.get("cuisine") or "전체", "when": a.get("when"), "role": a.get("role") or "주식·메인·국찌개",
            "note": NOTE_DISH + (" " + tip if tip else ""), "dishes": rows}


def t_dish_cost(a):
    con = _con()
    rows = dish.lookup(con, str(a.get("query", "")), max(1, min(int(a.get("n") or 5), 20)))
    return {"query": a.get("query"), "asof": _asof(con), "note": NOTE_DISH, "dishes": rows}


def t_public(a):
    con = _con()
    q = f"%{str(a.get('query', '')).strip()}%"
    n = max(1, min(int(a.get("n") or 5), 20))
    marks = ",".join("?" * len(PUBLIC_SOURCES))
    rows = con.execute(f"""SELECT r.id, r.name, r.source, r.level, r.servings, r.cook_time, r.calorie, r.url, r.steps, r.summary, r.category,
                                  c.cost_now, c.cost_ref, c.coverage
                           FROM recipe r LEFT JOIN recipe_cost c ON c.recipe_id = r.id
                           WHERE r.source IN ({marks}) AND r.name LIKE ? ORDER BY r.name LIMIT ?""", (*PUBLIC_SOURCES, q, n)).fetchall()
    out = []
    for rid, name, source, level, servings, cook_time, calorie, url, steps, summary, category, now, ref, cov in rows:
        ingredients = con.execute("SELECT name, qty FROM ingredient WHERE recipe_id=? ORDER BY seq", (rid,)).fetchall()
        try:
            steps_list = json.loads(steps) if steps else []
        except ValueError:
            steps_list = []
        out.append({"name": name, "source": {"rda": "농촌진흥청 농식품정보누리", "foodsafety": "식품안전나라(식약처)"}.get(source, source),
                    "category": category, "level": level, "servings": servings, "cook_time": cook_time, "calorie": calorie,
                    "cost_now": now, "cost_ref": ref, "coverage": cov, "ingredients": [{"name": nm, "qty": qt} for nm, qt in ingredients],
                    "steps": steps_list, "tip": summary, "url": url})
    return {"query": a.get("query"), "recipes": out,
            "note": ("공공누리 자료라 재료·조리 순서·링크를 보여 줘도 된다(출처 표기)." if out else
                     "공공 레시피에는 없는 음식이다. 그렇다고 '제공할 수 없다'로 끝내지 말 것: 누구나 아는 일반적인 조리 순서를 3~6단계로 "
                     "친절하게 설명하고(특정 사이트 레시피를 옮기는 것이 아니다), 재료 양은 dish_cost의 대표 재료 구성을 참고하라고 안내한다. "
                     "'일반적인 방법이라 집집마다 다를 수 있다'고 한 줄 덧붙인다.")}


def t_item_prices(a):
    con = _con()
    levels = _levels(con)
    wanted = a.get("items") or sorted(levels)
    out = []
    for item in wanted:
        key = cost.canon(item) or item
        if key in levels:
            now, ref, src = levels[key]
            out.append({"item": key, "won_per_kg_now": round(now), "won_per_kg_3m_ago": round(ref), "change_pct": round((now - ref) / ref * 100, 1),
                        "source": {"seoul": "서울시 생필품 소매가(전통시장·대형마트)", "mafra": "전국 공영도매시장 정산가"}.get(src, src)})
        else:
            out.append({"item": item, "error": "가격을 아는 품목이 아닙니다"})
    return {"items": out, "note": "지금=최근 14일 중앙값, 석 달 전=76~104일 전 중앙값"}


def t_movers(a):
    con = _con()
    n = max(1, min(int(a.get("n") or 10), 40))
    skip = {"비누", "샴푸", "세제", "치약", "칫솔", "바디워시", "생수", "맥주", "소주", "콜라", "사이다", "라면", "컵라면", "즉석밥"}
    rows = sorted(((item, now, ref, src) for item, (now, ref, src) in _levels(con).items() if item not in skip), key=lambda t: (t[1] - t[2]) / t[2])
    fmt = lambda t: {"item": t[0], "won_per_kg_now": round(t[1]), "won_per_kg_3m_ago": round(t[2]), "change_pct": round((t[1] - t[2]) / t[2] * 100, 1), "source": t[3]}
    # 도구 숙고가 한 턴에 한 번만 부르므로, 내린 품목마다 싸진 음식 둘을 같이 얹는다 - 한 번의 호출로 "뭐가 싸졌고 뭘 해 먹나"가 다 나오게.
    down = []
    for t in rows[:n]:
        if t[1] >= t[2]:
            continue
        row = fmt(t)
        picks = dish.cheaper(con, 2, main_item=t[0], per_item=2) or dish.cheaper(con, 2, main_item=t[0], per_item=2, meal="전체")  # 식사 먼저, 없으면 전체
        row["dishes"] = [{k: d[k] for k in ("dish", "meal", "cuisine", "n_recipes", "servings", "cost_now", "cost_per_serving_now", "cost_ref", "change_pct", "levels")}
                         for d in picks]
        down.append(row)
    return {"asof": _asof(con), "down": down, "up": [fmt(t) for t in reversed(rows[-n:]) if t[1] > t[2]],
            "note": "내린 원인은 모른다 - 품목명으로 인터넷 조사 도구에 묻는다. 음식을 더 보려면 cheaper_dishes(main_item=품목). " + NOTE_DISH}


HANDLERS = {"price_movers": t_movers, "cheaper_dishes": t_cheaper, "dish_cost": t_dish_cost, "item_prices": t_item_prices, "public_recipes": t_public}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            con = _con()
            n = con.execute("SELECT count(*) FROM dish_cost").fetchone()[0]
            return self._send(200, {"ok": True, "dishes": n, "asof": _asof(con)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/mcp":
            return self._send(404, {"error": "not found"})
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except Exception:
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        method, rid = payload.get("method"), payload.get("id")
        if method == "notifications/initialized":
            self.send_response(202); self.end_headers(); return
        if method == "initialize":
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                                                                            "serverInfo": {"name": "soonkun-food-recipe", "version": "0.1.0"}}})
        if method == "tools/list":
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        if method == "tools/call":
            params = payload.get("params") or {}
            fn = HANDLERS.get(params.get("name"))
            if fn is None:
                return self._send(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool: {params.get('name')}"}})
            try:
                text, is_error = json.dumps(fn(params.get("arguments") or {}), ensure_ascii=False, indent=1), False
            except Exception as exc:
                text, is_error = f"도구 실행에 실패했습니다: {exc}", True
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}], "isError": is_error}})
        self._send(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method: {method}"}})

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {self.address_string()} {fmt % args}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8230)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    sqlite3.connect(cost.DB, timeout=30).executescript(dish.SCHEMA)  # 표가 아직 없어도 /healthz와 도구가 빈 결과로 답하게
    print(f"food_recipe MCP on http://{a.host}:{a.port}/mcp", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
