#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
if [ -f server.pid ] && kill -0 "$(cat server.pid)" 2>/dev/null; then
  kill "$(cat server.pid)" && rm -f server.pid && echo "书库服务已停止"
else
  echo "书库服务没有在运行"
  rm -f server.pid
fi
