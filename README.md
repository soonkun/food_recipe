# food_recipe - 농산물 가격 기준 레시피 추천 MCP 서버

매일 농산물 가격을 받아 레시피 원가를 계산해 두고, 값이 많이 내린 음식을 추천한다. 아직 1단계(레시피 모으기)만 있다.

## 1단계: 레시피 모으기 (`collect.py`, 의존성 없음 - 시스템 python3)
- `python3 collect.py 10000recipe` - 만개의레시피(약 28만 건)를 ID 순서대로 훑는다(6,828,xxx부터 7,084,xxx). 재료는 이름·양·구분으로 따로,
  난이도("아무나/초급/중급/고급")·시간·인분은 페이지의 JSON-LD와 요약칸에서. 작성자의 조리문·사진·설명은 저작권 때문에 저장하지 않는다(이름·재료·링크만, 원가 계산용). 공개 API가 없어 페이지를 읽는다(robots.txt 허용 범위).
  동시 4개, 약 10 ID/s → 전체 8시간쯤. 끊겨도 다시 돌리면 `scan` 표에 없는 ID만 이어서 본다. 로그: `data/collect.log`.
- `RDA_FOOD_API_KEY=… python3 collect.py rda` - 농촌진흥청 농식품정보누리 레시피 API(537건, 난이도·열량·1인분 가격 포함).
  키는 koreanfood.rda.go.kr → 오픈API에서 받는다(샘플키는 5건까지).
- 결과: `data/recipes.sqlite` - `recipe`(출처·이름·인분·시간·난이도·조리법 JSON) + `ingredient`(recipe_id·이름·양·구분) + `scan`(훑은 ID).

이 서버에서 막혀 있는 곳(타임아웃): 식품안전나라 레시피 API(openapi.foodsafetykorea.go.kr), 농사로(nongsaro.go.kr). 방화벽이 풀리면 추가.

## 2단계: 가격 모으기 (`prices.py`)
- `python3 prices.py seoul [--since 2025-01-01]` - 서울시 열린데이터 생필품 가격(전통시장·대형마트, 주 1회, 농축수산물 60여 품목). 결과: `price` 표(출처·날짜·품목·단위·가격·시장).
  키는 `data/.env`의 `SEOUL_API_KEY=…` (git에 안 올라감). 자료는 2025-01부터 있다.
- KAMIS(www.kamis.or.kr)는 이 서버에서 막혀 있다. 농림축산식품 공공데이터포털(data.mafra.go.kr, 레시피와 같은 키)의 도매 경락가격은 키가 오면 붙인다.

## 방침
- 민간 레시피 사이트 자료는 이름·재료·난이도·링크만 두고 원가 계산에만 쓴다. 답은 계산 결과 + "만드는 법은 여기: 링크". 조리문·사진은 저장도 노출도 하지 않는다.
