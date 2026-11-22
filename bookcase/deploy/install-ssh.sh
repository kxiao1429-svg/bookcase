#!/bin/sh
# 在联想个人云（已开 SSH + 有 python3）上安装书库。
# 用法：
#   sh deploy/install-ssh.sh /opt/bookcase [访问口令]
#   BOOKS_DIR=/sata/.storage/books sh deploy/install-ssh.sh /sata/.storage/bookcase
#   INSTALL_PDF=0 sh deploy/install-ssh.sh /sata/.storage/bookcase   # 轻量模式，不装 PyMuPDF
set -e
TARGET="${1:-/opt/bookcase}"
TOKEN="${2:-}"
BOOKS_DIR="${BOOKS_DIR:-$TARGET/books}"

if [ ! -f "$TARGET/server.py" ]; then
  echo "用法：sh install-ssh.sh /opt/bookcase [口令]"
  echo "先把整个 bookcase 目录拷到 $TARGET"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "这台设备没有 python3。可以改用 Docker 部署，或把书库跑在电脑上（路线 C）。"
  exit 1
fi
python3 -c 'import sys; print("Python %d.%d.%d" % sys.version_info[:3])'

cd "$TARGET"
mkdir -p data "$BOOKS_DIR"

if [ -z "$TOKEN" ] && [ -f config.json ]; then
  TOKEN=$(python3 -c "import json;print(json.load(open('config.json')).get('token',''))" 2>/dev/null || true)
fi
if [ -z "$TOKEN" ]; then
  TOKEN=$(od -An -N8 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n' || date +%s)
fi

if [ ! -f config.json ]; then
  cat > config.json <<EOF
{
  "roots": ["$BOOKS_DIR"],
  "uploadDir": "$BOOKS_DIR/上传",
  "trashDir": "$BOOKS_DIR/.bookcase-trash",
  "host": "0.0.0.0",
  "port": 8765,
  "token": "$TOKEN",
  "maxUploadMB": 4096,
  "autoScanMinutes": 0
}
EOF
  echo "已生成 config.json"
fi

echo "---------------------------------------------"
echo " 访问口令：$TOKEN"
echo " 书库目录：$BOOKS_DIR"
echo " 想换目录：改 config.json 的 roots 后重启"
echo "---------------------------------------------"

if [ "${INSTALL_PDF:-1}" = "0" ]; then
  echo "轻量模式：跳过 PyMuPDF。PDF 只按文件名收录（能下载、能读），EPUB 封面不受影响。"
else
  python3 -m pip install --user -q -r requirements.txt 2>/dev/null \
    || echo "提示：PyMuPDF 没装上，PDF 只有文件名信息，EPUB 不受影响"
fi

sh start.sh