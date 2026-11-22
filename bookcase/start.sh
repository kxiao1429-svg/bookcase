#!/usr/bin/env bash
# 启动书库服务（幂等：已在运行则直接返回）
cd "$(dirname "$0")" || exit 1

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "没有找到 python3。请先装 Python 3.8+，或改用 Docker 部署。"
  exit 1
fi

PORT=$("$PY" -c "import scan; print(scan.load_settings()['port'])" 2>/dev/null || echo 8765)
HEALTH="http://127.0.0.1:${PORT}/api/health"

if "$PY" -c "import urllib.request; urllib.request.urlopen('${HEALTH}', timeout=2)" 2>/dev/null; then
  echo "书库已在运行：http://127.0.0.1:${PORT}"
  exit 0
fi

rm -f server.pid
# NAS 上降优先级，扫描时不抢 Samba/系统服务的 CPU
RUN=""
if command -v nice >/dev/null 2>&1; then
  RUN="nice -n 19"
fi
if command -v setsid >/dev/null 2>&1; then
  setsid $RUN nohup "$PY" server.py > /tmp/bookcase.log 2>&1 < /dev/null &
else
  $RUN nohup "$PY" server.py > /tmp/bookcase.log 2>&1 < /dev/null &
fi
echo $! > server.pid
sleep 1.2

if "$PY" -c "import urllib.request; urllib.request.urlopen('${HEALTH}', timeout=2)" 2>/dev/null; then
  echo "书库已启动：http://127.0.0.1:${PORT}"
  echo "局域网地址写在日志开头：/tmp/bookcase.log"
else
  echo "启动失败，日志："; cat /tmp/bookcase.log; exit 1
fi
