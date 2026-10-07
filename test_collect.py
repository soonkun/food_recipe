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
assert r["steps"] == ["끓인다 & 졸인다"] and r["image"] == "a.jpg"
assert collect.parse_10000recipe("<html>no recipe</html>", 2) is None

collect.DB = "/tmp/test_recipes.sqlite"; os.path.exists(collect.DB) and os.remove(collect.DB)
con = collect.db(); collect.save(con, r); collect.save(con, r)  # 두 번 저장해도 한 건
assert con.execute("SELECT count(*) FROM recipe").fetchone()[0] == 1
assert con.execute("SELECT count(*) FROM ingredient WHERE name='코다리'").fetchone()[0] == 1
os.remove(collect.DB); print("ok")
