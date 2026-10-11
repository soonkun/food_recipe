#!/bin/bash
# food_recipe MCP 서버 기동(127.0.0.1:8230). 재실행하면 다시 띄운다. MOPAN/start_local.sh가 부른다.
cd "$(dirname "$0")" || exit 1
pkill -f '^python3 server\.py --port 8230' ; sleep 1   # 앵커 필수 - 이 스크립트 자신이 잡히지 않게
nohup python3 server.py --port 8230 >> data/server.log 2>&1 &
for _ in $(seq 1 10); do curl -s -m 2 -o /dev/null http://127.0.0.1:8230/healthz && break; sleep 1; done
curl -s -m 5 -w ' /healthz %{http_code}\n' http://127.0.0.1:8230/healthz
