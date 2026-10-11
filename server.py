"""food_recipe MCP 서버(의존성 없음, 127.0.0.1:8230). MOPAN이 "MCP 서버 관리"에 http://127.0.0.1:8230/mcp 로 등록해 쓴다.

  python3 server.py [--port 8230]

도구
  cheaper_recipes  석 달 전보다 원가가 내린 요리(난이도·주재료로 좁히기). 답은 원가·변동률·주재료·원문 링크. 조리문은 주지 않는다 - 링크로 안내.
  recipe_cost      이름으로 레시피를 찾아 지금 원가·석 달 전 원가·재료별 g을 준다.
  item_prices      품목의 지금/석 달 전 원/kg(출처 포함). 품목을 안 주면 전부.
  price_movers     석 달 전보다 많이 내린/오른 품목 - "요즘 뭐가 싸졌나"의 답. 폭락 원인은 이 서버가 모른다: 인터넷 조사 도구에 품목명으로 묻는다.

프로토콜은 soonkun/mcp_server/maps/server.py와 같다(JSON-RPC over HTTP POST /mcp: initialize·tools/list·tools/call). GET /healthz.
"""
import argparse, json, sqlite3, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cost

PROTOCOL_VERSION = "2025-06-18"
TOOLS = [
    {"name": "cheaper_recipes",
     "description": "석 달 전보다 재료 원가가 많이 내린 요리를 추천한다. 농산물 값이 내렸을 때 '그걸로 뭘 해 먹을까'의 답. 결과는 요리 이름·"
                    "난이도(아무나/초급/중급/고급)·인분·지금 원가·석 달 전 원가·변동률·주재료·원문 링크. 만드는 법은 링크를 안내한다(저작권).",
     "inputSchema": {"type": "object", "properties": {
         "n": {"type": "integer", "description": "몇 개(기본 10, 최대 50)"},
         "level": {"type": "string", "description": "난이도로 좁히기: 아무나·초급·중급·고급"},
         "main_item": {"type": "string", "description": "주재료 품목으로 좁히기(예: 포도·감자·사과). item_prices의 품목명"},
         "min_cost": {"type": "integer", "description": "지금 원가 하한(원, 기본 2000 - 양념만 있는 글을 거른다)"}}}},
    {"name": "recipe_cost",
     "description": "요리 이름(일부)으로 레시피를 찾아 재료 원가를 계산해 준다: 지금 원가, 석 달 전 원가, 가격을 아는 재료 비율, 재료별 g.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "요리 이름 또는 그 일부(예: 감자조림)"},
         "n": {"type": "integer", "description": "몇 개(기본 5)"}}, "required": ["query"]}},
    {"name": "item_prices",
     "description": "농축수산물 품목의 지금 가격과 석 달 전 가격(원/kg)과 변동률. 품목을 안 주면 가격을 아는 품목 전부.",
     "inputSchema": {"type": "object", "properties": {
         "items": {"type": "array", "items": {"type": "string"}, "description": "품목명(예: 양파, 돼지고기, 포도)"}}}},
    {"name": "price_movers",
     "description": "석 달 전보다 값이 많이 내린 품목과 오른 품목. '요즘 뭐가 싸졌나'에 먼저 부른다. 내린 원인(풍작·수입·소비 감소 등)은 "
                    "이 도구가 모르니 품목명으로 인터넷 조사 도구에 묻는다.",
     "inputSchema": {"type": "object", "properties": {"n": {"type": "integer", "description": "내린 것·오른 것 각각 몇 개(기본 10)"}}}},
]


def _con():
    return sqlite3.connect(cost.DB, timeout=30)


def t_cheaper(a):
    con = _con()
    n = max(1, min(int(a.get("n") or 10), 50))
    rows = cost.cheaper(con, n, min_cost=int(a.get("min_cost") or 2000), level=a.get("level") or None, main_item=a.get("main_item") or None)
    asof = con.execute("SELECT asof FROM recipe_cost LIMIT 1").fetchone()
    return {"asof": asof[0] if asof else None, "note": "원가는 재료값만(양념 포함, 가격을 아는 재료 기준). 만드는 법은 url을 안내할 것.", "recipes": rows}


def t_recipe_cost(a):
    con = _con()
    q = f"%{str(a.get('query', '')).strip()}%"
    n = max(1, min(int(a.get("n") or 5), 20))
    rows = con.execute("""SELECT r.id, r.name, r.level, r.servings, r.cook_time, c.cost_now, c.cost_ref, c.coverage, c.items, c.main_item, r.url, r.source
                          FROM recipe r JOIN recipe_cost c ON c.recipe_id = r.id WHERE r.name LIKE ? AND c.n_priced > 0
                          ORDER BY c.coverage DESC, c.n_total DESC LIMIT ?""", (q, n)).fetchall()
    out = []
    for rid, name, lvl, servings, cook_time, now, ref, cov, items, main, url, source in rows:
        ingredients = con.execute("SELECT name, qty FROM ingredient WHERE recipe_id=? ORDER BY seq", (rid,)).fetchall()
        out.append({"id": rid, "name": name, "level": lvl, "servings": servings, "cook_time": cook_time, "cost_now": now, "cost_ref": ref,
                    "change_pct": round((now - ref) / ref * 100, 1) if ref else None, "coverage": cov, "main_item": main,
                    "items_g": dict(kv.split(":") for kv in items.split(",") if kv),
                    "ingredients": [{"name": nm, "qty": qt} for nm, qt in ingredients], "url": url, "source": source})
    return {"query": a.get("query"), "recipes": out, "note": "ingredients는 재료 이름·양만(조리문 없음). 만드는 법은 url."}


def _levels(con):
    return cost.item_levels(con)


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
    return {"down": [fmt(t) for t in rows[:n] if t[1] < t[2]], "up": [fmt(t) for t in reversed(rows[-n:]) if t[1] > t[2]],
            "note": "원인은 모른다 - 품목명으로 인터넷 조사. 요리 추천은 cheaper_recipes(main_item=품목)."}


HANDLERS = {"cheaper_recipes": t_cheaper, "recipe_cost": t_recipe_cost, "item_prices": t_item_prices, "price_movers": t_movers}


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
            n = con.execute("SELECT count(*) FROM recipe_cost").fetchone()[0]
            asof = con.execute("SELECT asof FROM recipe_cost LIMIT 1").fetchone()
            return self._send(200, {"ok": True, "recipes_costed": n, "asof": asof[0] if asof else None})
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
    print(f"food_recipe MCP on http://{a.host}:{a.port}/mcp", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
