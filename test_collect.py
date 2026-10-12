import json, sqlite3, os
import collect

SAMPLE = '''<html><script type="application/ld+json">{"@type":"Recipe","name":"코다리조림","recipeIngredient":["코다리 2마리"],
"recipeInstructions":[{"@type":"HowToStep","text":"끓인다 &amp; 졸인다"}],"image":["a.jpg","b.jpg"]}</script>
<span class="view2_summary_info1">2인분</span><span class="view2_summary_info2">15분 이내</span><span class="view2_summary_info3">아무나</span>
<div class="ready_ingre3"><ul><b class="ready_ingre3_tt">[재료]</b><li><div class="ingre_list_name"><a href="#"> 코다리 </a></div>
<span class="ingre_list_ea">2마리 </span></li></ul><ul><b class="ready_ingre3_tt">[양념]</b><li><div class="ingre_list_name">간장</div>
<span class="ingre_list_ea">1컵</span></li></ul></div></html>'''

r = collect.parse_10000recipe(SAMPLE, 1)
assert r["name"] == "코다리조림" and r["level"] == "아무나" and r["servings"] == "2인분"
assert r["ingredients"] == [("코다리", "2마리", "재료"), ("간장", "1컵", "양념")]
assert r["steps"] == [] and r["image"] is None and r["summary"] is None  # 작성자 글은 안 남긴다
assert collect.parse_10000recipe("<html>no recipe</html>", 2) is None

collect.DB = "/tmp/test_recipes.sqlite"; os.path.exists(collect.DB) and os.remove(collect.DB)
con = collect.db(); collect.save(con, r); collect.save(con, r)  # 두 번 저장해도 한 건
assert con.execute("SELECT count(*) FROM recipe").fetchone()[0] == 1
assert con.execute("SELECT count(*) FROM ingredient WHERE name='코다리'").fetchone()[0] == 1
os.remove(collect.DB); print("ok")

import prices
r = prices.seoul_row({"P_SEQ": 2062332.0, "P_DATE": "2026-10-02", "PRDLST_NM": "쌀 ", "SPCIES": "[철원] 오대쌀", "UNIT": "4kg", "QY": "1포",
                      "A_PRICE": "22000", "M_NAME": "암사종합시장", "M_TYPE_NAME": "전통시장"})
assert r == ("seoul", "2062332", "2026-10-02", "쌀", "[철원] 오대쌀", "4kg", "1포", 22000, "암사종합시장", "전통시장")
print("ok prices")


def test_mafra_row_converts_to_won_per_kg():
    from prices import mafra_row, mafra_dates
    import datetime as dt
    r = {"trd_clcln_ymd": "2026-10-10", "whsl_mrkt_cd": "110001", "corp_cd": "1", "gds_mclsf_nm": "배추", "gds_sclsf_nm": "배추(일반)",
         "unit_qty": "10", "unit_nm": "kg", "avgprc": "12000", "whsl_mrkt_nm": "가락", "corp_nm": "A"}
    row = mafra_row(r)
    assert row[0] == "mafra" and row[2] == "2026-10-10" and row[3] == "배추" and row[5:8] == ("kg", "1", 1200)
    assert mafra_row({**r, "unit_nm": "g", "unit_qty": "500", "avgprc": "3000"})[7] == 6000
    assert mafra_row({**r, "unit_nm": "개"}) is None
    days = mafra_dates("2026-01-01", dt.date(2026, 10, 12))
    assert days[0] == "2026-10-12" and len(days) == 15 + 29 and "2026-07-28" in days  # 76일 전


def test_nongsaro_parse_maps_detail_to_recipe():
    xml = """<response><header><resultCode>00</resultCode></header><body><items><item>
      <cntntsNo>207957</cntntsNo><fdNm>감자전</fdNm>
      <matrlInfo>&lt;p&gt;주재료 : 감자 2개, 양파 1/2개&lt;br&gt;양념 : 소금 약간&lt;/p&gt;</matrlInfo>
      <ckngMthInfo>1. 감자를 간다.&lt;br&gt;2. 부친다.</ckngMthInfo><energyQy>210</energyQy>
    </item></items></body></response>"""
    (d,) = collect.ns_parse(xml)
    assert d["fdNm"] == "감자전" and d["energyQy"] == "210"
    assert collect.ns_steps(d["ckngMthInfo"]) == ["감자를 간다.", "부친다."]
    assert collect.parse_fs_parts(collect.ns_text(d["matrlInfo"])) == [
        ("감자", "2개", "주재료"), ("양파", "1/2개", "주재료"), ("소금", "약간", "양념")]


def test_nongsaro_parse_stops_on_error_code():
    import pytest
    with pytest.raises(SystemExit):
        collect.ns_parse("<response><header><resultCode>900</resultCode><resultMsg>인증키 오류</resultMsg></header></response>")


def test_nongsaro_rows_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "DB", str(tmp_path / "r.sqlite"))
    row = (3, {"cntntsNo": "1", "fdNm": "감자전", "fdSeCode": "A", "rtnFileCours": "http://x/a/", "rtnStreFileNm": "b.jpg"},
           {"fdNm": "감자전 ", "matrlInfo": "감자 2개, 소금 약간", "ckngMthInfo": "1. 간다<br>2. 부친다", "energyQy": "210"})
    collect.collect_nongsaro([row])
    con = sqlite3.connect(collect.DB)
    name, cat, image, steps, cal = con.execute("SELECT name, category, image, steps, calorie FROM recipe WHERE source='nongsaro'").fetchone()
    assert (name, cat, image, cal) == ("감자전", "3월/A", "http://x/a/b.jpg", "210") and json.loads(steps) == ["간다", "부친다"]
    assert con.execute("SELECT name, qty FROM ingredient ORDER BY seq").fetchall() == [("감자", "2개"), ("소금", "약간")]
