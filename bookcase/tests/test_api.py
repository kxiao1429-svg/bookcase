# -*- coding: utf-8 -*-
"""Bookcase 私有云版接口回归测试（只依赖标准库 + PyMuPDF，可选）。

用法：python tests/test_api.py
会在临时目录里建一个隔离的书库，起一个端口 8899 的服务，跑完自动清理。
"""

import http.cookiejar
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8899
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "test-token-123"

PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def build_epub(path, title="测试书", author="测试作者"):
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>zh-CN</dc:language>
    <dc:publisher>测试出版社</dc:publisher>
    <dc:date>2026</dc:date>
    <dc:description>一本用来测试上传的样书。</dc:description>
  </metadata>
  <manifest>
    <item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/>
    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""
    chapter = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第一章</title></head>
<body><h1>第一章</h1><p>这是样书正文。</p><p>%s</p></body></html>""" % ("测试内容 " * 2400)
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/chapter1.xhtml", chapter)
        z.writestr("OEBPS/cover.png", PNG_1PX)


def build_pdf(path):
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Bookcase upload test")
    doc.save(path)
    doc.close()


def open_json(opener, url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with opener.open(req, timeout=20) as r:
        return r.status, json.loads(r.read().decode("utf-8")), dict(r.headers)


def wait_health(timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def main():
    tmp = tempfile.mkdtemp(prefix="bookcase-test-")
    books = os.path.join(tmp, "books")
    data = os.path.join(tmp, "data")
    os.makedirs(books)
    os.makedirs(data)
    epub = os.path.join(tmp, "测试书.epub")
    epub2 = os.path.join(tmp, "待归档.epub")
    pdf = os.path.join(tmp, "样张.pdf")
    build_epub(epub)
    build_epub(epub2, title="待归档", author="归档测试")
    try:
        build_pdf(pdf)
        pdf_ok = True
    except ImportError:
        pdf_ok = False

    cfg = {
        "roots": [books],
        "uploadDir": os.path.join(books, "上传"),
        "trashDir": os.path.join(tmp, "trash"),
        "host": "127.0.0.1",
        "port": PORT,
        "token": TOKEN,
        "maxUploadMB": 64,
    }
    cfg_path = os.path.join(tmp, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)

    env = os.environ.copy()
    env["BOOKCASE_CONFIG"] = cfg_path
    env["BOOKCASE_DATA"] = data
    proc = subprocess.Popen([sys.executable, "server.py", str(PORT)], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        if not wait_health():
            out = proc.stdout.read() if proc.stdout else ""
            raise SystemExit("服务没起来：\n" + out)

        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        # 1. 未登录访问 API
        try:
            opener.open(BASE + "/api/stats", timeout=10)
            raise AssertionError("未登录竟然能读数据")
        except urllib.error.HTTPError as e:
            assert e.code == 401, e.code

        # 2. 登录页
        with opener.open(BASE + "/", timeout=10) as r:
            html = r.read().decode("utf-8")
        assert "访问口令" in html, "未登录首页应该是登录页"

        # 3. 错误口令
        try:
            open_json(opener, BASE + "/api/login", json.dumps({"token": "wrong"}).encode(),
                      {"Content-Type": "application/json"})
            raise AssertionError("错误口令也能登录")
        except urllib.error.HTTPError as e:
            assert e.code == 401, e.code

        # 4. 正确口令
        status, body, _ = open_json(opener, BASE + "/api/login",
                                    json.dumps({"token": TOKEN}).encode(),
                                    {"Content-Type": "application/json"})
        assert status == 200 and body["ok"]

        status, stats, _ = open_json(opener, BASE + "/api/stats")
        assert status == 200 and stats["total"] == 0, stats

        # 5. 上传 EPUB
        content = open(epub, "rb").read()
        name = urllib.parse.quote("测试书.epub")
        req = urllib.request.Request(BASE + "/api/upload?name=" + name, data=content,
                                     headers={"Content-Type": "application/octet-stream"})
        with opener.open(req, timeout=30) as r:
            up = json.loads(r.read().decode("utf-8"))
        assert up["ok"] and not up["duplicate"], up
        book = up["book"]
        assert book["title"] == "测试书", book["title"]
        assert book["author"] == "测试作者", book["author"]
        assert book["cover"] is True, "封面没提取出来"
        book_id = book["id"]

        # 6. 同一本再传一次 → 去重
        req = urllib.request.Request(BASE + "/api/upload?name=" + name, data=content,
                                     headers={"Content-Type": "application/octet-stream"})
        with opener.open(req, timeout=30) as r:
            dup = json.loads(r.read().decode("utf-8"))
        assert dup["duplicate"] and dup["book"]["id"] == book_id, dup

        # 7. 预检
        status, chk, _ = open_json(opener, BASE + "/api/upload/check",
                                   json.dumps({"files": [{"name": "测试书.epub", "size": len(content)},
                                                         {"name": "新书.epub", "size": 12345}]}).encode(),
                                   {"Content-Type": "application/json"})
        assert "测试书.epub" in chk["existing"], chk
        assert "新书.epub" in chk["missing"], chk

        # 8. 下载（附件头）
        with opener.open(BASE + f"/api/file/{book_id}?dl=1", timeout=20) as r:
            disp = r.headers.get("Content-Disposition", "")
            got = r.read()
        assert "attachment" in disp and "UTF-8" in disp, disp
        assert got == content, "下载内容和原文件不一致"

        # 9. 非法扩展名
        try:
            req = urllib.request.Request(BASE + "/api/upload?name=bad.exe", data=b"x" * 10,
                                         headers={"Content-Type": "application/octet-stream"})
            opener.open(req, timeout=10)
            raise AssertionError("exe 也传上去了")
        except urllib.error.HTTPError as e:
            assert e.code == 415, e.code

        # 10. 配置接口
        status, cfg_out, _ = open_json(opener, BASE + "/api/config")
        assert cfg_out["authRequired"] is True
        assert cfg_out["maxUploadMB"] == 64
        assert cfg_out["uploadDir"] == os.path.join(books, "上传") or cfg_out["uploadDir"].endswith("上传")

        # 11. PDF（有 PyMuPDF 才测）
        if pdf_ok:
            data_pdf = open(pdf, "rb").read()
            req = urllib.request.Request(
                BASE + "/api/upload?name=" + urllib.parse.quote("样张.pdf"), data=data_pdf,
                headers={"Content-Type": "application/octet-stream"})
            with opener.open(req, timeout=30) as r:
                up_pdf = json.loads(r.read().decode("utf-8"))
            assert up_pdf["ok"] and up_pdf["book"]["format"] == "pdf", up_pdf
            assert up_pdf["book"]["pages"] == 1, up_pdf["book"]

        # 11.5 归档（不是删除：文件要移到 trashDir/重复归档/日期/）
        content2 = open(epub2, "rb").read()
        req = urllib.request.Request(
            BASE + "/api/upload?name=" + urllib.parse.quote("待归档.epub"), data=content2,
            headers={"Content-Type": "application/octet-stream"})
        with opener.open(req, timeout=30) as r:
            up2 = json.loads(r.read().decode("utf-8"))
        assert up2["ok"], up2
        status, arch, _ = open_json(
            opener, BASE + "/api/cleanup",
            json.dumps({"action": "archive", "id": up2["book"]["id"]}).encode(),
            {"Content-Type": "application/json"})
        assert arch["ok"] and os.path.isfile(arch["message"]), arch
        assert "重复归档" in arch["message"], arch["message"]

        # 12. 书单打包下载
        status, body, _ = open_json(
            opener, BASE + "/api/lists",
            json.dumps({"name": "测试书单", "note": "打包用"}).encode(),
            {"Content-Type": "application/json"})
        assert status == 201, body
        list_id = body["list"]["id"]
        open_json(opener, BASE + f"/api/lists/{list_id}/books",
                  json.dumps({"bookIds": [book_id]}).encode(),
                  {"Content-Type": "application/json"})
        with opener.open(BASE + f"/api/lists/{list_id}/download", timeout=30) as r:
            assert r.headers.get("Content-Type") == "application/zip", r.headers
            zip_bytes = r.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            names = z.namelist()
            assert any(n.endswith(".epub") for n in names), names
            txt = [n for n in names if n.endswith("书单.txt")]
            assert txt, names
            assert "测试书单" in z.read(txt[0]).decode("utf-8-sig")

        # 13. 整库备份
        with opener.open(BASE + "/api/backup", timeout=30) as r:
            assert r.headers.get("Content-Type") == "application/zip", r.headers
            backup_bytes = r.read()
        with zipfile.ZipFile(io.BytesIO(backup_bytes)) as z:
            names = z.namelist()
            mf = [n for n in names if n.endswith("manifest.json")]
            assert mf, names
            meta = json.loads(z.read(mf[0]).decode("utf-8"))
            assert meta["totalBooks"] >= 1, meta
            assert meta["lists"], meta
            assert any(n.endswith(".epub") for n in names), names

        # 14. AI 目录快照
        status, cat, _ = open_json(opener, BASE + "/api/catalog")
        assert cat["ok"] and len(cat["books"]) >= 1, cat.get("ok")
        assert cat["lists"], cat
        assert "cleanup" in cat and "duplicates" in cat["cleanup"], cat.keys()

        # 15. 扫描后书还在
        status, scan, _ = open_json(opener, BASE + "/api/scan", method="GET")
        status, stats, _ = open_json(opener, BASE + "/api/stats")
        assert stats["total"] >= 1, stats

        print("ALL TESTS PASSED")
        print(f"  - 上传目录: {cfg['uploadDir']}")
        print(f"  - 数据库:   {os.path.join(data, 'library.db')}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()