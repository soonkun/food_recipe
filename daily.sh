#!/bin/bash
# 매일 한 번: 가격 받기 → 품목 원/kg → 레시피 원가. crontab 예) 30 6 * * * /NHNHOME/WORKSPACE/26rda001_A/SAS/soonkun/mcp_server/food_recipe/daily.sh
cd "$(dirname "$0")" || exit 1
{
  echo "== $(date '+%F %T')"
  python3 prices.py seoul --since "$(date -d '-120 days' +%F)"
  grep -q '^MAFRA_API_KEY=' data/.env && python3 prices.py mafra --since "$(date -d '-120 days' +%F)"
  grep -q '^RDA_FOOD_API_KEY=' data/.env && python3 collect.py rda  # 농진청 레시피(공개): 호스트가 죽어 있으면 30초 타임아웃 후 넘어간다
  python3 cost.py prices && python3 cost.py recipes && python3 dish.py build
} >> data/daily.log 2>&1
