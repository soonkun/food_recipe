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
