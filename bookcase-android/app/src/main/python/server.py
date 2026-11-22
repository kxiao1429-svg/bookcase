# -*- coding: utf-8 -*-
"""Bookcase：手机上的本地电子书库服务。

启动：python3 server.py [端口]
打开：http://127.0.0.1:8765
"""

import base64
import json
import shutil
import mimetypes
import os
import re
import shutil
import socketserver
import sys
import threading
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import scan as lib
import lists as listslib

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
PORT = 8765
VERSION = "1.12.0"

IMPORT_HOWTO = """这是「书库」分享出来的书单包。

用法（收到包的人）：
1. 把 zip 放到手机 Download 目录（微信/QQ 点“保存到手机”也在这里）。
2. 打开书库 → 设置 → 导入书单包 → 找到它 → 导入。
3. 导入后会自动解压到 Download/书库导入/ 并开始扫描，书架刷新就能看到。

没装书库也行：直接解压，把里面的 epub / pdf 放到任意目录用阅读器打开。
书单.txt 是这份书单的书目清单。
"""

MIME = {
    ".epub": "application/epub+zip",
    ".pdf": "application/pdf",
    ".mobi": "application/octet-stream",
    ".azw3": "application/octet-stream",
    ".azw": "application/octet-stream",
    ".fb2": "application/octet-stream",
    ".cbz": "application/vnd.comicbook+zip",
    ".djvu": "image/vnd.djvu",
}

SCAN_LOCK = threading.Lock()

BOOK_SELECT = ("SELECT books.*, (SELECT group_concat(list_id) FROM list_books "
               "WHERE book_id=books.id) AS list_ids FROM books")


def book_json(row, include_path=True):
    d = {
        "id": row["id"],
        "path": row["path"] if include_path else "",
        "format": row["format"],
        "title": row["title"],
        "author": row["author"],
        "publisher": row["publisher"],
        "year": row["year"],
        "description": row["description"],
        "pages": row["pages"],
        "size": row["size"],
        "sizeHuman": lib.human_size(row["size"] or 0),
        "cover": bool(row["cover"]),
        "status": row["status"] or "unread",
        "progress": row["progress"] or 0,
        "cfi": row["cfi"] or "",
        "favorite": bool(row["favorite"]),
        "junk": bool(row["junk"]),
        "broken": bool(row["broken"]),
        "mtime": row["mtime"],
        "addedAt": row["added_at"],
        "updatedAt": row["updated_at"],
    }
    try:
        d["tags"] = json.loads(row["tags"] or "[]")
    except Exception:
        d["tags"] = []
    try:
        raw_lists = row["list_ids"]
    except (IndexError, KeyError):
        raw_lists = None
    d["listIds"] = [int(x) for x in str(raw_lists).split(",")] if raw_lists else []
    stem = os.path.splitext(os.path.basename(row["path"]))[0]
    d["fileStem"] = stem
    d["dirName"] = os.path.dirname(row["path"])
    return d


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("BOOKCASE_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------ 响应工具

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, code, message):
        self.send_json({"ok": False, "error": message}, code)

    def read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if n <= 0 or n > 2 * 1024 * 1024:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def send_path(self, path, ctype, cache=None):
        try:
            size = os.path.getsize(path)
            f = open(path, "rb")
        except OSError:
            return self.send_error_json(404, "文件不存在")
        with f:
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            partial = False
            if rng:
                m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
                if m and (m.group(1) or m.group(2)):
                    if m.group(1):
                        start = int(m.group(1))
                        end = int(m.group(2)) if m.group(2) else size - 1
                    else:
                        start = max(0, size - int(m.group(2)))
                        end = size - 1
                    start = max(0, min(start, size - 1))
                    end = max(start, min(end, size - 1))
                    partial = True
            length = end - start + 1
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if cache:
                self.send_header("Cache-Control", cache)
            self.end_headers()
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # ------------------------------------------------------------ 路由

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        q = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/" or path == "/index.html":
                return self.serve_static("index.html")
            if path.startswith("/static/"):
                return self.serve_static(path[len("/static/"):])
            if path == "/api/books":
                return self.api_books(q)
            if path == "/api/stats":
                return self.api_stats()
            if path == "/api/tags":
                return self.api_tags()
            if path == "/api/scan":
                return self.send_json(lib.SCANNER.snapshot())
            if path == "/api/cleanup":
                return self.send_json(lib.cleanup_report())
            m = re.match(r"^/api/list-cover/(\d+)$", path)
            if m:
                return self.api_list_cover(int(m.group(1)))
            if path == "/api/lists/export":
                return self.send_json({"ok": True, **listslib.export_lists()})
            if path == "/api/lists":
                return self.send_json({"ok": True, "lists": listslib.list_lists()})
            m = re.match(r"^/api/lists/(\d+)$", path)
            if m:
                return self.api_list(int(m.group(1)))
            if path == "/api/config":
                settings = lib.load_settings()
                return self.send_json({"roots": settings["roots"], "port": PORT,
                                       "trashDir": settings["trashDir"],
                                       "version": VERSION})
            if path == "/api/roots_info":
                return self.api_roots_info()
            m = re.match(r"^/api/share/book/(\d+)$", path)
            if m:
                return self.api_share_book(int(m.group(1)))
            m = re.match(r"^/api/share/list/(\d+)$", path)
            if m:
                return self.api_share_list(int(m.group(1)))
            if path == "/api/import/candidates":
                return self.api_import_candidates()
            m = re.match(r"^/api/book/(\d+)$", path)
            if m:
                return self.api_book(int(m.group(1)))
            m = re.match(r"^/api/cover/(\d+)$", path)
            if m:
                return self.api_cover(int(m.group(1)))
            m = re.match(r"^/api/file/(\d+)$", path)
            if m:
                return self.api_file(int(m.group(1)))
            return self.send_error_json(404, "not found")
        except BrokenPipeError:
            pass
        except Exception as e:
            self.send_error_json(500, f"{type(e).__name__}: {e}")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        try:
            if path == "/api/scan":
                return self.api_start_scan()
            if path == "/api/cleanup":
                return self.api_cleanup_action()
            if path == "/api/lists/import":
                return self.api_lists_import()
            if path == "/api/lists":
                return self.api_list_create()
            m = re.match(r"^/api/book/(\d+)/cover/restore$", path)
            if m:
                return self.api_cover_restore(int(m.group(1)))
            m = re.match(r"^/api/book/(\d+)/cover$", path)
            if m:
                return self.api_cover_upload(int(m.group(1)))
            m = re.match(r"^/api/lists/(\d+)/cover/restore$", path)
            if m:
                return self.api_list_cover_restore(int(m.group(1)))
            m = re.match(r"^/api/lists/(\d+)/cover$", path)
            if m:
                return self.api_list_cover_upload(int(m.group(1)))
            m = re.match(r"^/api/lists/(\d+)/books$", path)
            if m:
                return self.api_list_books(int(m.group(1)))
            if path == "/api/config":
                return self.api_config_update()
            if path == "/api/import":
                return self.api_import()
            return self.send_error_json(404, "not found")
        except BrokenPipeError:
            pass
        except Exception as e:
            self.send_error_json(500, f"{type(e).__name__}: {e}")

    def do_PATCH(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        m = re.match(r"^/api/lists/(\d+)$", path)
        if m:
            return self.api_list_update(int(m.group(1)))
        m = re.match(r"^/api/book/(\d+)$", path)
        if not m:
            return self.send_error_json(404, "not found")
        try:
            return self.api_update_book(int(m.group(1)))
        except Exception as e:
            self.send_error_json(500, f"{type(e).__name__}: {e}")

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        try:
            m = re.match(r"^/api/lists/(\d+)/books/(\d+)$", path)
            if m:
                listslib.remove_book(int(m.group(1)), int(m.group(2)))
                return self.send_json({"ok": True})
            m = re.match(r"^/api/lists/(\d+)$", path)
            if m:
                ok, msg = listslib.delete_list(int(m.group(1)))
                return self.send_json({"ok": ok, "message": msg}, 200 if ok else 404)
            return self.send_error_json(404, "not found")
        except BrokenPipeError:
            pass
        except Exception as e:
            self.send_error_json(500, f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------ 静态文件

    def serve_static(self, rel):
        rel = rel or "index.html"
        full = os.path.normpath(os.path.join(STATIC, rel))
        if not full.startswith(STATIC + os.sep) and full != STATIC:
            return self.send_error_json(403, "forbidden")
        if not os.path.isfile(full):
            return self.send_error_json(404, "not found")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif full.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif full.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        cache = "no-cache"
        self.send_path(full, ctype, cache)

    # ------------------------------------------------------------ API

    def api_books(self, q):
        def arg(name, default=""):
            v = q.get(name)
            return v[0] if v else default

        where, params = [], []
        junk_param = arg("junk", "0")
        if junk_param == "1":
            where.append("junk=1")
        else:
            where.append("junk=0")
        term = arg("q").strip()
        if term:
            like = f"%{term}%"
            where.append("(title LIKE ? OR author LIKE ? OR path LIKE ?)")
            params += [like, like, like]
        fmt = arg("format")
        if fmt and fmt != "all":
            where.append("format=?")
            params.append(fmt)
        status = arg("status")
        if status and status != "all":
            where.append("status=?")
            params.append(status)
        tag = arg("tag")
        if tag:
            where.append("EXISTS (SELECT 1 FROM json_each(books.tags) WHERE value=?)")
            params.append(tag)
        if arg("favorite") == "1":
            where.append("favorite=1")

        sort = arg("sort", "added")
        order = {
            "added": "added_at DESC",
            "title": "title COLLATE NOCASE ASC",
            "author": "author COLLATE NOCASE ASC",
            "recent": "updated_at DESC",
            "size": "size DESC",
            "progress": "progress DESC",
        }.get(sort, "added_at DESC")

        list_arg = arg("list")
        if list_arg.isdigit():
            where.append("EXISTS (SELECT 1 FROM list_books lb WHERE lb.book_id=books.id AND lb.list_id=?)")
            params.append(int(list_arg))

        sql = BOOK_SELECT
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + order

        conn = lib.connect()
        rows = conn.execute(sql, params).fetchall()
        total = conn.execute("SELECT COUNT(*) c FROM books WHERE junk=0").fetchone()["c"]
        conn.close()
        self.send_json({
            "ok": True, "total": total,
            "books": [book_json(r) for r in rows],
            "scan": lib.SCANNER.snapshot(),
        })

    def api_stats(self):
        conn = lib.connect()
        total = conn.execute("SELECT COUNT(*) c FROM books WHERE junk=0").fetchone()["c"]
        junk = conn.execute("SELECT COUNT(*) c FROM books WHERE junk=1").fetchone()["c"]
        reading = conn.execute("SELECT COUNT(*) c FROM books WHERE status='reading' AND junk=0").fetchone()["c"]
        done = conn.execute("SELECT COUNT(*) c FROM books WHERE status='done' AND junk=0").fetchone()["c"]
        fav = conn.execute("SELECT COUNT(*) c FROM books WHERE favorite=1 AND junk=0").fetchone()["c"]
        broken = conn.execute("SELECT COUNT(*) c FROM books WHERE broken=1").fetchone()["c"]
        fmts = {r["format"]: r["c"] for r in conn.execute(
            "SELECT format, COUNT(*) c FROM books WHERE junk=0 GROUP BY format")}
        rows = conn.execute("SELECT size, progress, status FROM books WHERE junk=0").fetchall()
        conn.close()
        size = sum(r["size"] or 0 for r in rows)
        self.send_json({"ok": True, "total": total, "junk": junk, "reading": reading,
                        "done": done, "favorite": fav, "broken": broken, "formats": fmts,
                        "size": size, "sizeHuman": lib.human_size(size),
                        "scan": lib.SCANNER.snapshot()})

    def api_tags(self):
        conn = lib.connect()
        counts = {}
        for r in conn.execute("SELECT tags FROM books WHERE junk=0"):
            try:
                for t in json.loads(r["tags"] or "[]"):
                    counts[t] = counts.get(t, 0) + 1
            except Exception:
                pass
        conn.close()
        tags = [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]
        self.send_json({"ok": True, "tags": tags})

    def api_book(self, bid):
        conn = lib.connect()
        row = conn.execute(BOOK_SELECT + " WHERE books.id=?", (bid,)).fetchone()
        conn.close()
        if not row:
            return self.send_error_json(404, "书不存在")
        self.send_json({"ok": True, "book": book_json(row)})

    def api_cover_upload(self, bid):
        body = self.read_body()
        raw_data = body.get("data") or ""
        if "," in raw_data:
            raw_data = raw_data.split(",", 1)[1]
        try:
            raw = base64.b64decode(raw_data, validate=False)
        except Exception:
            return self.send_error_json(400, "图片数据无效")
        if len(raw) < 512:
            return self.send_error_json(400, "图片太小")
        if len(raw) > 3 * 1024 * 1024:
            return self.send_error_json(400, "图片太大，请压缩后再试")
        conn = lib.connect()
        row = conn.execute("SELECT id, cover, cover_orig FROM books WHERE id=?", (bid,)).fetchone()
        if not row:
            conn.close()
            return self.send_error_json(404, "书不存在")
        if row["cover"] and not row["cover_orig"]:
            current = os.path.join(lib.COVER_DIR, row["cover"])
            if os.path.isfile(current):
                ext = row["cover"].rsplit(".", 1)[-1]
                orig = f"orig-{bid}.{ext}"
                shutil.copy2(current, os.path.join(lib.COVER_DIR, orig))
                conn.execute("UPDATE books SET cover_orig=? WHERE id=?", (orig, bid))
                conn.commit()
        name = lib.save_cover_bytes(raw, bid)
        if not name:
            conn.close()
            return self.send_error_json(400, "不支持这种图片格式（请用 jpg/png/webp）")
        conn.execute("UPDATE books SET cover=?, updated_at=? WHERE id=?", (name, time.time(), bid))
        conn.commit()
        row = conn.execute(BOOK_SELECT + " WHERE books.id=?", (bid,)).fetchone()
        conn.close()
        self.send_json({"ok": True, "book": book_json(row)})

    def api_cover_restore(self, bid):
        conn = lib.connect()
        row = conn.execute("SELECT id, path, format, cover, cover_orig FROM books WHERE id=?", (bid,)).fetchone()
        if not row:
            conn.close()
            return self.send_error_json(404, "书不存在")
        restored = None
        if row["cover_orig"]:
            src = os.path.join(lib.COVER_DIR, row["cover_orig"])
            if os.path.isfile(src):
                ext = row["cover_orig"].rsplit(".", 1)[-1]
                target = f"{bid}.{ext}"
                shutil.copy2(src, os.path.join(lib.COVER_DIR, target))
                for e in ("jpg", "png", "webp", "gif"):
                    if e != ext:
                        try:
                            os.remove(os.path.join(lib.COVER_DIR, f"{bid}.{e}"))
                        except OSError:
                            pass
                restored = target
        if restored is None:
            # 没有备份时，尝试从原书重新提取
            fmt = row["format"]
            meta = {}
            if fmt == "epub":
                meta = lib.read_epub(row["path"])
            elif fmt == "pdf":
                meta = lib.read_pdf(row["path"])
            restored = lib.save_cover_bytes(meta.get("cover"), bid)
        conn.execute("UPDATE books SET cover=?, updated_at=? WHERE id=?", (restored or "", time.time(), bid))
        conn.commit()
        row = conn.execute(BOOK_SELECT + " WHERE books.id=?", (bid,)).fetchone()
        conn.close()
        self.send_json({"ok": True, "book": book_json(row)})

    def api_list_cover_upload(self, list_id):
        body = self.read_body()
        raw_data = body.get("data") or ""
        if "," in raw_data:
            raw_data = raw_data.split(",", 1)[1]
        try:
            raw = base64.b64decode(raw_data, validate=False)
        except Exception:
            return self.send_error_json(400, "图片数据无效")
        if len(raw) < 512:
            return self.send_error_json(400, "图片太小")
        if len(raw) > 3 * 1024 * 1024:
            return self.send_error_json(400, "图片太大，请压缩后再试")
        conn = lib.connect()
        if not conn.execute("SELECT id FROM lists WHERE id=?", (list_id,)).fetchone():
            conn.close()
            return self.send_error_json(404, "书单不存在")
        name = lib.save_cover_named(raw, f"list-{list_id}")
        if not name:
            conn.close()
            return self.send_error_json(400, "不支持这种图片格式")
        conn.execute("UPDATE lists SET cover=?, updated_at=? WHERE id=?", (name, time.time(), list_id))
        conn.commit()
        conn.close()
        self.send_json({"ok": True, "list": listslib.get_list(list_id)})

    def api_list_cover_restore(self, list_id):
        conn = lib.connect()
        if not conn.execute("SELECT id FROM lists WHERE id=?", (list_id,)).fetchone():
            conn.close()
            return self.send_error_json(404, "书单不存在")
        for e in ("jpg", "png", "webp", "gif"):
            try:
                os.remove(os.path.join(lib.COVER_DIR, f"list-{list_id}.{e}"))
            except OSError:
                pass
        conn.execute("UPDATE lists SET cover='', updated_at=? WHERE id=?", (time.time(), list_id))
        conn.commit()
        conn.close()
        self.send_json({"ok": True, "list": listslib.get_list(list_id)})

    def api_list_cover(self, list_id):
        conn = lib.connect()
        row = conn.execute("SELECT cover FROM lists WHERE id=?", (list_id,)).fetchone()
        conn.close()
        if not row or not row["cover"]:
            return self.send_error_json(404, "没有封面")
        full = os.path.join(lib.COVER_DIR, row["cover"])
        if not os.path.isfile(full):
            return self.send_error_json(404, "没有封面")
        ctype = mimetypes.guess_type(full)[0] or "image/jpeg"
        self.send_path(full, ctype, "no-cache")

    def api_update_book(self, bid):
        body = self.read_body()
        conn = lib.connect()
        row = conn.execute(BOOK_SELECT + " WHERE books.id=?", (bid,)).fetchone()
        if not row:
            conn.close()
            return self.send_error_json(404, "书不存在")
        fields, params = [], []
        if "tags" in body and isinstance(body["tags"], list):
            tags = []
            for t in body["tags"]:
                t = str(t).strip()[:24]
                if t and t not in tags:
                    tags.append(t)
            fields.append("tags=?")
            params.append(json.dumps(tags[:20], ensure_ascii=False))
        if "status" in body and body["status"] in ("unread", "reading", "done"):
            fields.append("status=?")
            params.append(body["status"])
        if "progress" in body:
            try:
                p = max(0, min(100, int(body["progress"])))
                fields.append("progress=?")
                params.append(p)
            except (TypeError, ValueError):
                pass
        if "cfi" in body:
            fields.append("cfi=?")
            params.append(str(body["cfi"])[:500])
        if "favorite" in body:
            fields.append("favorite=?")
            params.append(1 if body["favorite"] else 0)
        if "junk" in body:
            fields.append("junk=?")
            params.append(1 if body["junk"] else 0)
        if fields:
            fields.append("updated_at=?")
            params.append(time.time())
            params.append(bid)
            conn.execute(f"UPDATE books SET {', '.join(fields)} WHERE id=?", params)
            conn.commit()
        row = conn.execute(BOOK_SELECT + " WHERE books.id=?", (bid,)).fetchone()
        conn.close()
        self.send_json({"ok": True, "book": book_json(row)})

    def api_cover(self, bid):
        conn = lib.connect()
        row = conn.execute("SELECT cover FROM books WHERE id=?", (bid,)).fetchone()
        conn.close()
        if not row or not row["cover"]:
            return self.send_error_json(404, "没有封面")
        full = os.path.join(lib.COVER_DIR, row["cover"])
        if not os.path.isfile(full):
            return self.send_error_json(404, "没有封面")
        ctype = mimetypes.guess_type(full)[0] or "image/jpeg"
        self.send_path(full, ctype, "public, max-age=604800")

    def api_file(self, bid):
        conn = lib.connect()
        row = conn.execute("SELECT path, format FROM books WHERE id=?", (bid,)).fetchone()
        conn.close()
        if not row:
            return self.send_error_json(404, "书不存在")
        if not os.path.isfile(row["path"]):
            return self.send_error_json(410, "原文件已被移动或删除")
        ctype = MIME.get("." + (row["format"] or ""), "application/octet-stream")
        self.send_path(row["path"], ctype, "no-cache")

    def api_start_scan(self):
        if lib.SCANNER.running:
            return self.send_json({"ok": True, "started": False, "scan": lib.SCANNER.snapshot()})
        if not SCAN_LOCK.acquire(blocking=False):
            return self.send_json({"ok": True, "started": False, "scan": lib.SCANNER.snapshot()})

        def run():
            try:
                lib.scan()
            finally:
                SCAN_LOCK.release()

        threading.Thread(target=run, daemon=True).start()
        self.send_json({"ok": True, "started": True, "scan": lib.SCANNER.snapshot()})

    def api_list(self, list_id):
        d = listslib.get_list(list_id)
        if not d:
            return self.send_error_json(404, "书单不存在")
        self.send_json({"ok": True, "list": d})

    def api_lists_import(self):
        body = self.read_body()
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        res, err = listslib.import_lists(data)
        if err:
            return self.send_error_json(400, err)
        self.send_json({"ok": True, **res})

    def api_list_create(self):
        body = self.read_body()
        d, err = listslib.create_list(body.get("name"), body.get("note", ""))
        if err:
            return self.send_error_json(400, err)
        self.send_json({"ok": True, "list": d}, 201)

    def api_list_update(self, list_id):
        body = self.read_body()
        d, err = listslib.update_list(list_id, body.get("name"), body.get("note"))
        if err:
            return self.send_error_json(400, err)
        self.send_json({"ok": True, "list": d})

    def api_list_books(self, list_id):
        body = self.read_body()
        ids = body.get("bookIds")
        if ids is None and "bookId" in body:
            ids = [body.get("bookId")]
        ok, msg = listslib.set_books(list_id, ids or [], replace=bool(body.get("replace")))
        if not ok:
            return self.send_error_json(400, msg)
        self.send_json({"ok": True, "message": msg})

    def api_cleanup_action(self):
        body = self.read_body()
        action = body.get("action")
        # 批量重命名不带 id，必须在解析 id 之前处理，否则会被「缺少 id」挡掉
        if action == "renameAll":
            ok, msg = lib.rename_all_clean()
            return self.send_json({"ok": ok, "message": str(msg)})
        try:
            bid = int(body.get("id"))
        except (TypeError, ValueError):
            return self.send_error_json(400, "缺少 id")
        if action == "rename":
            ok, msg = lib.rename_clean(bid)
        elif action == "trash":
            ok, msg = lib.trash_book(bid)
        else:
            return self.send_error_json(400, "未知操作")
        self.send_json({"ok": ok, "message": str(msg)})

    def api_roots_info(self):
        """设置页的「目录检查」：每个扫描目录能不能读到、里面有多少本书，再推荐几个漏掉的目录。"""
        roots = lib.load_config()
        items = []
        for r in roots:
            exists = os.path.isdir(r)
            item = {"path": r, "exists": exists, "files": 0, "error": ""}
            if exists:
                try:
                    item["files"] = lib.count_books(r, limit=3000, seconds=2.5)
                except Exception as e:
                    item["error"] = "%s: %s" % (type(e).__name__, e)
            items.append(item)

        known = set(roots)
        skip = {"Android", "DCIM", "Movies", "Music", "Pictures", "Podcasts",
                "Ringtones", "Alarms", "Notifications", "com.android", "miui"}
        suggested = []
        for base in lib.storage_volumes():
            try:
                names = sorted(os.listdir(base))
            except OSError:
                continue
            for name in names:
                p = os.path.join(base, name)
                if p in known or name in skip or name.startswith("."):
                    continue
                if not os.path.isdir(p):
                    continue
                n = lib.count_books(p, limit=1500, seconds=1.2)
                if n:
                    suggested.append({"path": p, "files": n})
        suggested.sort(key=lambda x: -x["files"])
        self.send_json({"ok": True, "roots": items, "suggested": suggested[:20]})

    # ------------------------------------------------------------ 分享 / 导入

    def _data_dir(self):
        return os.path.dirname(os.path.abspath(lib.DB_PATH))

    def _share_dir(self):
        d = os.path.join(self._data_dir(), "分享")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        return d

    @staticmethod
    def _zip_entry(title, path, used):
        stem = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", title or
                      os.path.splitext(os.path.basename(path))[0]).strip() or "book"
        stem = stem[:80]
        ext = os.path.splitext(path)[1].lower()
        cand = stem + ext
        n = 2
        while cand.lower() in used:
            cand = "%s (%d)%s" % (stem, n, ext)
            n += 1
        used.add(cand.lower())
        return cand

    def api_share_book(self, bid):
        """详情页「分享」：把这本书的真实路径告诉 App，由它走系统分享。"""
        conn = lib.connect()
        try:
            row = conn.execute("SELECT * FROM books WHERE id=?", (bid,)).fetchone()
        finally:
            conn.close()
        if not row:
            return self.send_error_json(404, "没有这本书")
        path = row["path"]
        if not os.path.isfile(path):
            return self.send_error_json(410, "原文件已被移动或删除")
        ext = os.path.splitext(path)[1].lower()
        self.send_json({
            "ok": True,
            "path": path,
            "name": os.path.basename(path),
            "mime": MIME.get(ext, "application/octet-stream"),
        })

    def api_share_list(self, list_id):
        """书单分享：把书单里的书打成一个 zip，留在数据目录，交给 App 分享。"""
        lst = listslib.get_list(list_id)
        if not lst:
            return self.send_error_json(404, "没有这个书单")
        conn = lib.connect()
        try:
            rows = conn.execute(
                "SELECT b.* FROM books b JOIN list_books lb ON lb.book_id=b.id "
                "WHERE lb.list_id=? ORDER BY lb.added_at ASC, b.id ASC", (list_id,)).fetchall()
        finally:
            conn.close()
        rows = [r for r in rows if os.path.isfile(r["path"])]
        if not rows:
            return self.send_error_json(400, "书单里没有可分享的文件")

        name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", lst["name"]).strip()[:40] or "书单"
        share_dir = self._share_dir()
        # 顺手清掉一周前的旧分享包，别把手机塞满
        try:
            now = time.time()
            for fn in os.listdir(share_dir):
                p = os.path.join(share_dir, fn)
                if fn.lower().endswith(".zip") and now - os.path.getmtime(p) > 7 * 86400:
                    os.remove(p)
        except OSError:
            pass
        out = os.path.join(share_dir,
                           "%s-%d本-%s.zip" % (name, len(rows), time.strftime("%Y%m%d")))
        used = set()
        manifest = ["书单：%s" % lst["name"], "共 %d 本" % len(rows), ""]
        try:
            with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
                for r in rows:
                    arc = self._zip_entry(r["title"], r["path"], used)
                    z.write(r["path"], arc)
                    manifest.append("%s\t%s\t%s" % (r["title"] or "", r["author"] or "",
                                                    os.path.basename(r["path"])))
                z.writestr("书单.txt", "\n".join(manifest) + "\n")
                z.writestr("怎么用.txt", IMPORT_HOWTO)
        except (OSError, zipfile.BadZipFile) as e:
            return self.send_error_json(500, "打包失败：%s" % e)
        self.send_json({"ok": True, "path": out, "name": os.path.basename(out),
                        "mime": "application/zip", "count": len(rows),
                        "size": os.path.getsize(out)})

    def api_import_candidates(self):
        """列出扫描目录里能找到的 zip，供「导入书单包」选择。"""
        out = []
        seen = set()
        for root in lib.load_config():
            if not os.path.isdir(root):
                continue
            base_depth = root.rstrip("/").count("/")
            for dirpath, dirnames, filenames in os.walk(root):
                depth = dirpath.count("/") - base_depth
                if depth >= 3:
                    dirnames[:] = []
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in filenames:
                    if not fn.lower().endswith(".zip"):
                        continue
                    p = os.path.join(dirpath, fn)
                    if p in seen:
                        continue
                    seen.add(p)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    out.append({"path": p, "name": fn, "size": st.st_size, "mtime": st.st_mtime})
        out.sort(key=lambda x: -x["mtime"])
        self.send_json({"ok": True, "files": out[:30]})

    def api_import(self):
        """导入书单包：解压到 Download/书库导入/<包名>/，只取电子书，然后后台扫描。"""
        body = self.read_body()
        src = str(body.get("path") or "").strip()
        if not os.path.isfile(src) or not zipfile.is_zipfile(src):
            return self.send_error_json(400, "不是有效的 zip 文件")
        base_name = os.path.splitext(os.path.basename(src))[0]
        dest_root = "/sdcard/Download/书库导入"
        try:
            os.makedirs(dest_root, exist_ok=True)
        except OSError:
            dest_root = os.path.join(self._data_dir(), "导入")
            os.makedirs(dest_root, exist_ok=True)
        safe = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", base_name).strip()[:40] or "导入"
        dest = os.path.join(dest_root, safe)
        n = 2
        while os.path.exists(dest):
            dest = os.path.join(dest_root, "%s (%d)" % (safe, n))
            n += 1
        os.makedirs(dest, exist_ok=True)

        count = 0
        try:
            with zipfile.ZipFile(src) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    fn = os.path.basename(info.filename.replace("\\", "/"))
                    if not fn or fn.startswith("."):
                        continue
                    if os.path.splitext(fn)[1].lower() not in lib.FORMATS:
                        continue
                    target = os.path.join(dest, fn)
                    k = 2
                    stem, ext = os.path.splitext(fn)
                    while os.path.exists(target):
                        target = os.path.join(dest, "%s (%d)%s" % (stem, k, ext))
                        k += 1
                    with z.open(info) as r, open(target, "wb") as w:
                        shutil.copyfileobj(r, w)
                    count += 1
        except Exception as e:
            return self.send_error_json(500, "解压失败：%s" % e)

        if count:
            self._start_scan()
        self.send_json({"ok": True, "count": count, "dir": dest})

    def _start_scan(self):
        if lib.SCANNER.running or not SCAN_LOCK.acquire(blocking=False):
            return False

        def run():
            try:
                lib.scan()
            finally:
                SCAN_LOCK.release()

        threading.Thread(target=run, daemon=True).start()
        return True

    def api_config_update(self):
        body = self.read_body()
        roots = body.get("roots")
        if not isinstance(roots, list) or not roots:
            return self.send_error_json(400, "roots 需要是非空数组")
        clean = []
        for r in roots:
            r = str(r).strip()
            if r and os.path.isdir(r) and r not in clean:
                clean.append(r.rstrip("/") or "/")
        if not clean:
            return self.send_error_json(400, "没有可用的目录")
        with open(lib.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"roots": clean}, f, ensure_ascii=False, indent=2)
        self.send_json({"ok": True, "roots": clean})


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    port = PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    lib.init_db()
    httpd = Server(("127.0.0.1", port), Handler)
    print(f"Bookcase 已启动: http://127.0.0.1:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()