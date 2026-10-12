# food_recipe - 농산물 가격 기준 음식 추천 MCP 서버 (soonkun/mcp_server/food_recipe, 자체 git)

매일 농산물 가격을 받아 음식별 재료 원가를 계산해 두고, 석 달 전보다 값이 많이 내린 음식을 추천한다. 2026-10-11 밤 `soonkun/food_recipe`에서 여기로 옮겼다
(maps·web과 같이 `mcp_server/start.sh`가 띄운다, 127.0.0.1:8230).

## 1단계: 레시피 모으기 (`collect.py`, 의존성 없음 - 시스템 python3)
- `python3 collect.py 10000recipe` - 만개의레시피(약 28만 건)를 ID 순서대로 훑는다(6,828,xxx부터 7,084,xxx). 재료는 이름·양·구분으로 따로,
  난이도("아무나/초급/중급/고급")·시간·인분은 페이지의 JSON-LD와 요약칸에서. 작성자의 조리문·사진·설명은 저작권 때문에 저장하지 않는다(이름·재료·링크만, 원가 계산용). 공개 API가 없어 페이지를 읽는다(robots.txt 허용 범위).
  동시 4개, 약 10 ID/s → 전체 8시간쯤. 끊겨도 다시 돌리면 `scan` 표에 없는 ID만 이어서 본다. 로그: `data/collect.log`.
- `python3 collect.py nongsaro` - **농사로 '이달의 음식 정보' Open API**(`api.nongsaro.go.kr/service/monthFd`, XML). 키는 농사로 마이페이지 → OPEN API 신청내역의
  인증키('날짜8자리+영문 21자' 꼴, 2026-10-07 승인) → `data/.env`의 `NONGSARO_API_KEY`. 월별 목록 `monthNewFdLst?thisYear=&thisMonth=` → 건별 상세 `monthNewFdDtl?cntntsNo=`
  (재료 `matrlInfo`, 조리법 `ckngMthInfo`, 열량 `energyQy` 등). 12개월이 다 찬 해는 2016~2019뿐이라 그 네 해 + 올해를 훑는다(같은 cntntsNo는 한 번).
  **이 서버(59.150.32.1)에서는 api.nongsaro.go.kr·www.rda.go.kr 로 TCP 자체가 막혀 있다**(2026-10-12 실측, 사용자 PC에서는 열림). 5초 안에 포기하므로
  닿는 PC에서 `python3 collect.py nongsaro --dump nongsaro.json` 으로 받아 서버에서 `python3 collect.py nongsaro --file nongsaro.json` 으로 넣는다.
  손으로 확인: 브라우저에 `http://api.nongsaro.go.kr/service/monthFd/monthNewFdLst?apiKey=<키>&thisYear=2019&thisMonth=10`.
- `python3 collect.py rda` - 농식품 공공데이터 포털(data.mafra.go.kr) 레시피 API(537건, 난이도·열량·1인분 가격 포함). **위 농사로 키와는 다른 포털**이라
  그 포털 계정으로 `Grid_20150827000000000226_1`(기본)·`…227_1`(재료)·`…228_1`(과정) 세 API를 활용신청해야 그 계정 키(`RDA_FOOD_API_KEY`)로 받아진다.
  신청 안 한 API는 `INFO-100 인증키가 유효하지 않습니다`. 211.237.50.150:7080은 브라우저가 아닌 User-Agent(curl 기본, Python-urllib)의 요청을 응답 없이
  버린다 - 손으로 확인할 때도 `curl -A Mozilla/5.0`. (2026-10-12: 이 포털 계정은 아직 없음)
- 결과: `data/recipes.sqlite` - `recipe`(출처·이름·인분·시간·난이도·조리법 JSON) + `ingredient`(recipe_id·이름·양·구분) + `scan`(훑은 ID).

이 서버에서 막혀 있는 곳(TCP 무응답): 식품안전나라 레시피 API(openapi.foodsafetykorea.go.kr), 농사로(api/www.nongsaro.go.kr), www.rda.go.kr. 둘 다 다른 PC에서 받은 파일을 넣는 길(`--file`)이 있다.

## 2단계: 가격 모으기 (`prices.py`)
- `python3 prices.py seoul [--since 2025-01-01]` - 서울시 열린데이터 생필품 가격(전통시장·대형마트, 주 1회, 농축수산물 60여 품목). 결과: `price` 표(출처·날짜·품목·단위·가격·시장).
  키는 `data/.env`의 `SEOUL_API_KEY=…` (git에 안 올라감). 자료는 2025-01부터 있다.
- `python3 prices.py mafra [--since …] [--max-requests 900]` - 도매(1순위, 소유자 방침 - 폭락 신호가 먼저 나오고 품목이 넓다): 공공데이터포털
  '한국농수산식품유통공사_전국 공영도매시장 정산정보'(data.go.kr 15141809, `https://apis.data.go.kr/B552845/katSale/trades`,
  `cond[trd_clcln_ymd::EQ]=YYYY-MM-DD`). 키는 `data/.env`의 `MAFRA_API_KEY`(data.go.kr 일반 인증키 64자리). 원가가 보는 두 창(최근 14일·76~104일 전)만
  날짜별로 받아 원/kg으로 바꿔 `price`(source='mafra', 품목=중분류명, 품종=소분류명)에 넣는다. 날짜당 수만 행이라 요청 수 한도 안에서 멈추고 다음 날 이어
  받는다. **2026-10-12 현재 키가 `SERVICE_KEY_IS_NOT_REGISTERED_ERROR(30)`** - data.go.kr 마이페이지 → 활용신청 현황에서 이 API가 승인됐는지, 복사한 키가
  그 계정의 것인지 확인해야 한다(실시간 경매정보 15141808에도 같은 응답). `api.agromarket.kr` 원천 주소는 열려 있지만 빈 결과만 준다. KAMIS는 이 서버에서 막혀 있다.

## 3단계: 원가와 추천 (`norm.py`, `cost.py`)
- `norm.py` - 재료 이름 → 가격 품목(`canon`: "다진 마늘"→마늘, "달걀"→계란, "대패삼겹살"→돼지고기 …), 양 → g(`grams`: 1/2개·2큰술·100g·약간 …).
  규칙표(`ALIASES`·`PIECE_G`)가 전부다. `python3 norm.py`가 자기검사, `--report`가 전체 재료 행의 품목별 건수와 미인식 상위 이름을 찍는다
  (2026-10-11: 재료 행 221만 중 77%가 품목으로 묶임, 97품목). 미인식 상위를 보고 규칙을 늘리면 된다.
- `python3 cost.py prices` - `price` → `item_price`(출처·품목·날짜·원/kg). 포장("1kg", "860ml", "135g x 2개입", "10개", "대 1마리")을 g으로 풀고 시장별 중앙값.
- `python3 cost.py recipes` - 레시피마다 지금 원가(최근 14일)·석 달 전 원가(76~104일 전)·가격을 아는 재료 비율·주재료 → `recipe_cost`. 24만 건에 20초.
- `python3 cost.py top` / `item [품목…]` - 많이 싸진 요리(주재료마다 3개까지, 재료 4개 이상·아는 재료 3개 이상·원가 2,000원 이상), 품목 가격.
- `./daily.sh` - 위 셋을 한 번에(크론 하루 한 번). 로그 `data/daily.log`.

## 4단계: 음식 단위 통계와 MCP 서버 (`dish.py`, `server.py`, 127.0.0.1:8230)
- `python3 dish.py build` - 레시피 제목에서 음식 이름을 뽑아(`dish_of`: "두부감자조림 만드는방법…"→두부감자조림, "감자채 볶음"→감자채볶음) 음식마다
  레시피 수·지금/석 달 전 원가 **중앙값**·대표 재료 구성(레시피 40% 이상에 드는 품목과 g 중앙값)·주재료·난이도 분포를 `dish_cost`에 둔다.
  레시피 5건 미만인 음식은 뺀다(한두 건의 "통계"는 그 레시피를 보여주는 것과 같다). 2026-10-11: 음식 3,947개.
  **인분**: 레시피의 "N인분"으로 보통 인분(중앙값)과 1인분 원가를 같이 둔다 - 답은 "2인분 기준 4,836원(1인분 2,165원)".
  **분류**(소유자 2026-10-11, 이름의 낱말로): 끼니 = 식사·음료·디저트·간식·기타(반죽·육수·소스), 계통 = 한식·중식·일식·양식·아시아(동남아·인도)·기타.
  추천의 기본은 **식사**만("저렴한 음식 추천"에 포도주스가 나오지 않게) - 음료·디저트는 물어야 나온다. 규칙은 `dish.py`의 MEAL_RULES·CUISINE_RULES.
  **역할**(식사 안): 주식(밥·면)·메인(고기·생선 요리)·국찌개·반찬(무침·나물·작은 조림·볶음·샐러드)·간단(샌드위치·토스트·죽). 추천 기본은
  주식·메인·국찌개를 역할마다 고르게, `when`(아침·점심·저녁·야식)이 오면 그때에 맞는 역할로(아침=간단·국·주식, 저녁=메인·국·주식). 상위 후보 안에서
  조금 섞어 같은 질문에 늘 같은 답이 나오지 않게 한다(소유자 2026-10-11: 점심도 저녁도 양배추사과샐러드는 안 된다).
- `./start.sh`로 띄운다(의존성 없음, http.server). MOPAN "MCP 서버 관리"에 `http://127.0.0.1:8230/mcp`로 등록되어 있다(이름 "농산물 가격·레시피").
  사용자는 `+ → 대화 설정 → 도구 설정`에서 이 서버를 켜야 모델에게 보인다.
- 도구: `price_movers`(내린/오른 품목 + 내린 품목마다 싸진 음식 둘) → `cheaper_dishes`(난이도·주재료로 좁히기) → `dish_cost`(음식 이름으로 통계) ·
  `item_prices`(품목 가격) · `public_recipes`(공공 자료 레시피만 재료·링크 제공). **내린 원인**은 이 서버가 모른다 - MOPAN의 인터넷 조사 도구가 품목명으로 찾는다.
- 공공 레시피 코퍼스(public_recipes의 재료): `collect.py nongsaro`(농사로 이달의 음식, 키 있음 - 이 서버에서 호스트 막힘)와 `collect.py foodsafety`(식품안전나라 COOKRCP01,
  1,100여 건, 공공누리). 식품안전나라 호스트(116.67.90.152)는 이 서버에서 막혀 있어 다른 PC에서 받은 JSON을 `data/_incoming/foodsafety/`에 두고 돌린다:
  `http://openapi.foodsafetykorea.go.kr/api/<키>/COOKRCP01/json/1/1000` 과 `…/1001/2000` 두 번 저장. 키는 `data/.env`의 `FOODSAFETY_API_KEY`.
- 매일 06:30 크론: `daily.sh`(서울 가격 → 품목 원/kg → 레시피 원가 → 음식 통계). 로그 `data/daily.log`.

## 방침 (2026-10-11 소유자 결정)
- **민간 레시피 사이트(만개의레시피) 자료는 음식 단위 통계를 내는 데만 쓴다.** 개별 레시피·링크·원문 재료 목록은 밖으로 내보내지 않는다(도구에 없다).
  재료와 분량은 사실이라 저작물이 아니지만, 24만 건 수집은 데이터베이스제작자권(저작권법 93조)·약관 쟁점이 남는다 - 수집물을 그대로 제공하는
  모양을 만들지 않는 것이 이 방침의 뜻이다. 조리문·사진은 애초에 저장하지 않는다.
- **레시피 본문과 링크를 보여주는 건 공공 자료(농진청 농식품정보누리, 식품안전나라)뿐**(`public_recipes`). 이 코퍼스를 늘려 가는 것이 과제.
- 누가 물으면 사실대로: "민간 사이트 자료는 음식별 원가 통계에만 썼고 개별 레시피는 제공하지 않는다. 보여주는 레시피는 농진청·식약처 자료다."
- 대외 서비스로 나가면 운영사(이지콘텐츠) 제휴·API 사용 허락을 먼저 받는다.
