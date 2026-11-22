#!/bin/sh
# 联想个人云 T2 / A1 / T1 环境检查：回家后 SSH 进去跑一遍，把输出发出来。
# 用法：sh deploy/t2-check.sh
echo "========== Bookcase 环境检查 =========="
echo "时间: $(date 2>/dev/null)"
echo "内核: $(uname -a 2>/dev/null)"
echo "架构: $(uname -m 2>/dev/null)"
echo
echo "--- Python ---"
if command -v python3 >/dev/null 2>&1; then
  python3 -V 2>&1
  python3 -c "import sqlite3, ssl; print('sqlite3/ssl OK')" 2>&1
  python3 -c "import pymupdf; print('pymupdf OK')" 2>&1 | head -1
else
  echo "python3: 没有"
fi
echo
echo "--- Docker ---"
if command -v docker >/dev/null 2>&1; then
  docker version 2>&1 | head -6
  docker image ls 2>&1 | head -3
else
  echo "docker: 没有"
fi
echo
echo "--- 系统 ---"
id 2>/dev/null
ldd --version 2>&1 | head -1
echo
echo "--- 磁盘 ---"
df -h 2>/dev/null | head -12
echo
echo "--- 数据目录 ---"
ls -ld /sata /sata/.storage /sata/.storage/docker 2>/dev/null
ls /sata/.storage 2>/dev/null | head -20
echo
echo "--- 内存/CPU ---"
free -m 2>/dev/null | head -3
grep -c ^processor /proc/cpuinfo 2>/dev/null
echo "========== 检查结束 =========="