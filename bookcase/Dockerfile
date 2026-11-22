# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    BOOKCASE_HOST=0.0.0.0 \
    BOOKCASE_PORT=8765 \
    BOOKCASE_DATA=/data \
    BOOKCASE_ROOTS=/books \
    BOOKCASE_UPLOAD_DIR=/books/上传 \
    BOOKCASE_AUTO_SCAN_MIN=10

WORKDIR /app

COPY requirements.txt ./
# PyMuPDF 只影响 PDF 封面/页数；个别 ARM 固件装不上也不该让构建失败
RUN pip install --no-cache-dir -r requirements.txt \
    || echo "PyMuPDF 安装失败：PDF 将只按文件名索引，EPUB 不受影响"

COPY . .

RUN mkdir -p /data /books

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)"

VOLUME ["/data"]
CMD ["python3", "server.py"]