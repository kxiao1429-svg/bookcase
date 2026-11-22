# -*- coding: utf-8 -*-
"""Bookcase：手机上的本地电子书库服务。

启动：python3 server.py [端口]
打开：http://127.0.0.1:8765
"""

import hashlib
import hmac
import json
import mimetypes
import os
import re
import shutil
import socket
import socketserver
import sys
import threading
import time
import urllib.parse
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import scan as lib
import lists as listslib

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
PORT = 8765
VERSION = lib.VERSION
SETTINGS = lib.load_settings()
COOKIE_NAME = "bookcase_token"
PUBLIC_API = ("/api/login", "/api/session", "/api/health")

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


# ---------------------------------------------------------------- 通用工具

def safe_filename(raw):
    """只留下文件名本身：去掉路径、控制字符和 Windows 非法字符。"""
    name = urllib.parse.unquote(str(raw or ""))
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', "", name).strip().strip(".")
    stem, ext = os.path.splitext(name)
    stem = stem.strip()[:120] or "未命名"
    ext = ext.lower()[:10]
    if not re.match(r"^\.[a-z0-9]+$", ext):
        ext = ""
    return stem + ext


def unique_path(dirpath, name):
    base, ext = os.path.splitext(name)
    cand = os.path.join(dirpath, name)
    n = 2
    while os.path.exists(cand):
        cand = os.path.join(dirpath, f"{base} ({n}){ext}")
        n += 1
    return cand


def unique_arcname(name, used):
    """ZIP 内部重名处理：同名文件加 (2)、(3) 后缀。"""
    base = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", os.path.basename(name)).strip() or "book"
    stem, ext = os.path.splitext(base)
    cand = base
    n = 2
    while cand.lower() in used:
        cand = f"{stem} ({n}){ext}"
        n += 1
    used.add(cand.lower())
    return cand


class ChunkedWriter:
    """给 zipfile 用的 HTTP chunked 输出流：边打包边发，不在服务器落临时大文件。"""

    def __init__(self, wfile):
        self.wfile = wfile
        self.buf = bytearray()

    def write(self, data):
        self.buf.extend(data)
        if len(self.buf) >= 65536:
            self.flush()
        return len(data)

    def flush(self):
        if not self.buf:
            return
        chunk = bytes(self.buf)
        self.buf.clear()
        self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")

    def close(self):
        self.flush()
        self.wfile.write(b"0\r\n\r\n")


def cookie_value(header, name):
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


def local_ip():
    """猜一下局域网 IP，用于启动时打印访问地址（失败就算了）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("223.5.5.5", 53))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return ""


def book_row(book_id):
    conn = lib.connect()
    try:
        return conn.execute(BOOK_SELECT + " WHERE books.id=?", (book_id,)).fetchone()
    finally:
        conn.close()



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
    _set_cookie = None

    def log_message(self, fmt, *args):
        if os.environ.get("BOOKCASE_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------ 口令

    def check_auth(self):
        """没设口令时一律放行；设了就要 cookie / Bearer / ?token= 三选一。"""
        token = SETTINGS.get("token") or ""
        if not token:
            return True
        if hmac.compare_digest(cookie_value(self.headers.get("Cookie"), COOKIE_NAME), token):
            return True
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            if hmac.compare_digest(auth[7:].strip(), token):
                return True
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        for v in q.get("token", []):
            if hmac.compare_digest(v, token):
                self._set_cookie = (f"{COOKIE_NAME}={token}; Path=/; Max-Age=2592000; "
                                    f"HttpOnly; SameSite=Lax")
                return True
        return False

    def api_guard(self):
        if self.check_auth():
            return True
        self.send_error_json(401, "需要访问口令")
        return False

    def end_headers(self):
        if self._set_cookie:
            self.send_header("Set-Cookie", self._set_cookie)
            self._set_cookie = None
        super().end_headers()

    # ------------------------------------------------------------ 响应工具

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "close_connection", False):
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, code, message):
        # POST 出错时请求体可能还没读完；直接关连接，避免下一个请求解析错位
        if self.command in ("POST", "PATCH", "DELETE"):
            self.close_connection = True
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

    def send_path(self, path, ctype, cache=None, download_name=None):
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
            if download_name:
                quoted = urllib.parse.quote(download_name, safe="")
                self.send_header("Content-Disposition",
                                 f"attachment; filename*=UTF-8''{quoted}")
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

    def send_zip(self, filename, entries, extra_files=None):
        """流式发送 ZIP（chunked），不把整包写到临时文件。"""
        quoted = urllib.parse.quote(filename, safe="")
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition",
                         f"attachment; filename*=UTF-8''{quoted}")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        writer = ChunkedWriter(self.wfile)
        try:
            with zipfile.ZipFile(writer, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
                for arc, path in entries:
                    try:
                        zf.write(path, arc)
                    except OSError:
                        continue
                for arc, data in extra_files or []:
                    zf.writestr(arc, data)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            try:
                writer.close()
            except OSError:
                pass


    # ------------------------------------------------------------ 路由

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        q = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/" or path == "/index.html":
                if not self.check_auth():
                    return self.serve_static("login.html")
                return self.serve_static("index.html")
            if path.startswith("/static/"):
                return self.serve_static(path[len("/static/"):])
            if path.startswith("/api/"):
                if path not in PUBLIC_API and not self.api_guard():
                    return
                if path == "/api/session":
                    return self.api_session()
                if path == "/api/health":
                    return self.send_json({"ok": True, "version": VERSION})
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
                if path == "/api/lists":
                    return self.send_json({"ok": True, "lists": listslib.list_lists()})
                if path == "/api/config":
                    return self.api_config()
                if path == "/api/catalog":
                    return self.api_catalog()
                if path == "/api/backup":
                    return self.api_backup()
                m = re.match(r"^/api/lists/(\d+)/download$", path)
                if m:
                    return self.api_list_download(int(m.group(1)))
                m = re.match(r"^/api/lists/(\d+)$", path)
                if m:
                    return self.api_list(int(m.group(1)))
                m = re.match(r"^/api/book/(\d+)$", path)
                if m:
                    return self.api_book(int(m.group(1)))
                m = re.match(r"^/api/cover/(\d+)$", path)
                if m:
                    return self.api_cover(int(m.group(1)))
                m = re.match(r"^/api/file/(\d+)$", path)
                if m:
                    return self.api_file(int(m.group(1)), q)
            return self.send_error_json(404, "not found")
        except BrokenPipeError:
            pass
        except Exception as e:
            self.send_error_json(500, f"{type(e).__name__}: {e}")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        try:
            if path == "/api/login":
                return self.api_login()
            if path.startswith("/api/") and not self.api_guard():
                return
            if path == "/api/upload":
                return self.api_upload()
            if path == "/api/upload/check":
                return self.api_upload_check()
            if path == "/api/scan":
                return self.api_start_scan()
            if path == "/api/cleanup":
                return self.api_cleanup_action()
            if path == "/api/lists":
                return self.api_list_create()
            m = re.match(r"^/api/lists/(\d+)/books$", path)
            if m:
                return self.api_list_books(int(m.group(1)))
            if path == "/api/config":
                return self.api_config_update()
            return self.send_error_json(404, "not found")
        except BrokenPipeError:
            pass
        except Exception as e:
            self.send_error_json(500, f"{type(e).__name__}: {e}")

    def do_PATCH(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        if not self.api_guard():
            return
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
        if not self.api_guard():
            return
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

    # ------------------------------------------------------------ 登录 / 会话

    def api_login(self):
        body = self.read_body()
        given = str(body.get("token") or "")
        token = SETTINGS.get("token") or ""
        if not token or hmac.compare_digest(given, token):
            if token:
                self._set_cookie = (f"{COOKIE_NAME}={token}; Path=/; Max-Age=2592000; "
                                    f"HttpOnly; SameSite=Lax")
            return self.send_json({"ok": True, "authRequired": bool(token)})
        time.sleep(0.4)
        return self.send_error_json(401, "口令不对")

    def api_session(self):
        token = SETTINGS.get("token") or ""
        authed = self.check_auth() if token else True
        self.send_json({"ok": True, "authRequired": bool(token), "authed": bool(authed),
                        "version": VERSION})

    # ------------------------------------------------------------ 上传 / 下载

    def api_upload(self):
        """原始二进制上传（一次一个文件），文件名放在 ?name= 里。

        流式落盘 + SHA1 去重，不把整个文件读进内存，手机传几百 MB 也不会炸。
        """
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        raw_name = (q.get("name") or [""])[0]
        name = safe_filename(raw_name)
        ext = os.path.splitext(name)[1].lower()
        if not name:
            return self.send_error_json(400, "缺少文件名（?name=书名.epub）")
        if ext not in lib.FORMATS:
            return self.send_error_json(415, "只支持 " + " / ".join(sorted(lib.FORMATS)))

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return self.send_error_json(411, "需要 Content-Length")
        limit = int(SETTINGS["maxUploadMB"]) * 1024 * 1024
        if length > limit:
            return self.send_error_json(413, f"单个文件不能超过 {SETTINGS['maxUploadMB']} MB")

        upload_dir = SETTINGS["uploadDir"]
        try:
            os.makedirs(upload_dir, exist_ok=True)
        except OSError as e:
            return self.send_error_json(500, f"上传目录不可写：{e}")
        try:
            if length + 64 * 1024 * 1024 > shutil.disk_usage(upload_dir).free:
                return self.send_error_json(507, "云端空间不足")
        except OSError:
            pass

        incoming = os.path.join(upload_dir, ".incoming")
        os.makedirs(incoming, exist_ok=True)
        tmp = os.path.join(incoming, uuid.uuid4().hex + ".part")
        digest = hashlib.sha1()
        hash_left = 80 * 1024 * 1024  # 和 scan.file_hash 的 80MB 上限保持一致
        got = 0
        try:
            with open(tmp, "wb") as f:
                while got < length:
                    chunk = self.rfile.read(min(262144, length - got))
                    if not chunk:
                        break
                    f.write(chunk)
                    if hash_left > 0:
                        digest.update(chunk)
                        hash_left -= len(chunk)
                    got += len(chunk)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return self.send_error_json(500, f"写入失败：{e}")

        if got != length:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return self.send_error_json(400, "上传中断，请重试")

        dup = lib.find_duplicate(length, digest.hexdigest())
        if dup:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return self.send_json({"ok": True, "duplicate": True, "book": book_json(dup),
                                   "message": "云端已经有同一本书"})

        final = unique_path(upload_dir, name)
        try:
            os.replace(tmp, final)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return self.send_error_json(500, f"保存失败：{e}")

        try:
            book_id = lib.index_uploaded(final)
            row = book_row(book_id)
            return self.send_json({"ok": True, "duplicate": False,
                                   "book": book_json(row) if row else None,
                                   "message": "已备份到云端"})
        except Exception as e:
            # 文件已经落盘，索引失败也不丢书，重新扫描就能补上
            return self.send_json({"ok": True, "duplicate": False, "book": None,
                                   "message": f"文件已保存，索引失败：{e}"})

    def api_upload_check(self):
        """上传前预检：云端已经有的（同名同大小）就不再传。"""
        body = self.read_body()
        files = body.get("files") if isinstance(body.get("files"), list) else []
        existing, missing = [], []
        for item in files[:3000]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            if lib.find_by_name_size(name, size):
                existing.append(name)
            else:
                missing.append(name)
        self.send_json({"ok": True, "existing": existing, "missing": missing,
                        "total": len(files)})

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

    def api_file(self, bid, q=None):
        conn = lib.connect()
        row = conn.execute("SELECT path, format FROM books WHERE id=?", (bid,)).fetchone()
        conn.close()
        if not row:
            return self.send_error_json(404, "书不存在")
        if not os.path.isfile(row["path"]):
            return self.send_error_json(410, "原文件已被移动或删除")
        ctype = MIME.get("." + (row["format"] or ""), "application/octet-stream")
        dl = bool(q and (q.get("dl") or q.get("download")))
        self.send_path(row["path"], ctype, "no-cache",
                       download_name=os.path.basename(row["path"]) if dl else None)

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

    # ------------------------------------------------------------ 打包下载

    def api_list_download(self, list_id):
        d = listslib.get_list(list_id)
        if not d:
            return self.send_error_json(404, "书单不存在")
        rows = listslib.list_book_rows(list_id)
        if not rows:
            return self.send_error_json(400, "这个书单还是空的")

        folder = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", d["name"]).strip()[:60] or "书单"
        used, entries, lines, missing = set(), [], [], []
        for i, r in enumerate(rows, 1):
            if not os.path.isfile(r["path"]):
                missing.append(r)
                continue
            arc = unique_arcname(os.path.basename(r["path"]), used)
            entries.append((f"{folder}/{arc}", r["path"]))
            lines.append(f"{i}. {r['title']} — {r['author'] or '未知作者'}"
                         f" · {(r['format'] or '').upper()} · {lib.human_size(r['size'] or 0)}")
        if not entries:
            return self.send_error_json(400, "书单里的文件都不在原路径了，先重新扫描一下")

        header = [
            f"书单：{d['name']}",
            f"说明：{d['note']}" if d.get("note") else "",
            f"导出时间：{time.strftime('%Y-%m-%d %H:%M')}",
            f"共 {len(rows)} 本，实际打包 {len(entries)} 本" +
            (f"，{len(missing)} 本文件缺失" if missing else ""),
            "",
        ]
        text = "\ufeff" + "\n".join([x for x in header if x is not None] + lines) + "\n"
        extra = [(f"{folder}/书单.txt", text.encode("utf-8"))]
        if missing:
            miss = "\ufeff以下文件在原路径找不到了：\n\n" + "\n".join(
                f"- {r['title']}（{r['path']}）" for r in missing) + "\n"
            extra.append((f"{folder}/缺失文件.txt", miss.encode("utf-8")))
        self.send_zip(f"{folder}.zip", entries, extra)

    def api_catalog(self):
        """给 AI / 外部脚本用的完整目录快照：书、书单、重复组、清理建议。"""
        conn = lib.connect()
        try:
            rows = conn.execute(BOOK_SELECT + " ORDER BY books.id").fetchall()
        finally:
            conn.close()
        self.send_json({
            "ok": True,
            "version": VERSION,
            "exportedAt": time.time(),
            "exportedAtText": time.strftime("%Y-%m-%d %H:%M:%S"),
            "books": [book_json(r) for r in rows],
            "lists": listslib.list_lists(),
            "cleanup": lib.cleanup_report(),
            "scan": lib.SCANNER.snapshot(),
        })

    def api_backup(self):
        conn = lib.connect()
        try:
            books = conn.execute(BOOK_SELECT + " WHERE books.junk=0 ORDER BY books.id").fetchall()
            lists = conn.execute(
                """SELECT l.*, (SELECT group_concat(book_id) FROM list_books
                   WHERE list_id=l.id) AS book_ids
                   FROM lists l ORDER BY l.id""").fetchall()
        finally:
            conn.close()

        stamp = time.strftime("%Y%m%d-%H%M")
        folder = f"bookcase-backup-{stamp}"
        used, entries, manifest_books, missing = set(), [], [], []
        title_by_id = {}
        for b in books:
            title_by_id[b["id"]] = b["title"]
            arc = None
            if os.path.isfile(b["path"]):
                arc = "books/" + unique_arcname(os.path.basename(b["path"]), used)
                entries.append((f"{folder}/{arc}", b["path"]))
            else:
                missing.append({"id": b["id"], "title": b["title"], "path": b["path"]})
            try:
                tags = json.loads(b["tags"] or "[]")
            except Exception:
                tags = []
            manifest_books.append({
                "id": b["id"], "title": b["title"], "author": b["author"],
                "format": b["format"], "size": b["size"], "pages": b["pages"],
                "status": b["status"], "progress": b["progress"],
                "favorite": bool(b["favorite"]), "tags": tags,
                "originalPath": b["path"], "archive": arc,
            })

        lists_out = []
        for l in lists:
            ids = [int(x) for x in str(l["book_ids"] or "").split(",") if x]
            lists_out.append({
                "name": l["name"], "note": l["note"],
                "bookIds": ids,
                "bookTitles": [title_by_id.get(i, "") for i in ids],
            })

        manifest = {
            "app": "bookcase", "version": VERSION,
            "exportedAt": time.time(),
            "exportedAtText": time.strftime("%Y-%m-%d %H:%M:%S"),
            "totalBooks": len(books), "packedBooks": len(entries),
            "missingFiles": len(missing),
            "books": manifest_books,
            "lists": lists_out,
            "missing": missing,
        }
        readme = (
            "\ufeff书库整库备份\n"
            f"导出时间：{manifest['exportedAtText']}\n"
            f"书：{len(books)} 本，实际打包 {len(entries)} 本，缺失 {len(missing)} 本\n\n"
            "怎么恢复：\n"
            "1. 把 books/ 里的文件拷回书库目录，打开书库重新扫描；\n"
            "2. manifest.json 里有每本书的书名、作者、标签、阅读进度，以及书单和书单说明；\n"
            "3. 书单需要按 manifest.json 里的 lists 手动重建（或写脚本导入）。\n\n"
            "说明：原文件没有加密，ZIP 只是打包，方便整体拷走。\n"
        )
        extra = [
            (f"{folder}/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")),
            (f"{folder}/说明.txt", readme.encode("utf-8")),
        ]
        self.send_zip(f"{folder}.zip", entries, extra)

    def api_cleanup_action(self):
        body = self.read_body()
        action = body.get("action")
        try:
            bid = int(body.get("id"))
        except (TypeError, ValueError):
            return self.send_error_json(400, "缺少 id")
        if action == "rename":
            ok, msg = lib.rename_clean(bid)
        elif action == "trash":
            ok, msg = lib.trash_book(bid)
        elif action == "archive":
            ok, msg = lib.archive_book(bid)
        elif action == "renameAll":
            ok, msg = lib.rename_all_clean()
        else:
            return self.send_error_json(400, "未知操作")
        self.send_json({"ok": ok, "message": str(msg)})

    def api_config(self):
        settings = lib.load_settings()
        host = self.headers.get("Host") or f"127.0.0.1:{settings['port']}"
        self.send_json({
            "roots": settings["roots"],
            "missingRoots": [r for r in settings["roots"] if not os.path.isdir(r)],
            "port": settings["port"],
            "trashDir": settings["trashDir"],
            "uploadDir": settings["uploadDir"],
            "dataDir": settings["dataDir"],
            "maxUploadMB": settings["maxUploadMB"],
            "autoScanMinutes": settings.get("autoScanMinutes", 0),
            "authRequired": bool(settings["token"]),
            "host": settings["host"],
            "origin": f"http://{host}",
            "version": VERSION,
        })

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
        # 只改 roots，保留 host / token / uploadDir 等其他配置
        cfg = {}
        if os.path.exists(lib.CONFIG_PATH):
            try:
                with open(lib.CONFIG_PATH, encoding="utf-8") as f:
                    cfg = json.load(f) or {}
            except Exception:
                cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["roots"] = clean
        with open(lib.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self.send_json({"ok": True, "roots": clean})


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def auto_scan_worker(minutes):
    """后台定时扫描：给「FolderSync / Syncthing 自动把书同步进 NAS」的场景用。"""
    interval = max(60.0, float(minutes) * 60.0)
    while True:
        time.sleep(interval)
        if lib.SCANNER.running or not SCAN_LOCK.acquire(blocking=False):
            continue
        try:
            lib.scan()
        except Exception:
            pass
        finally:
            SCAN_LOCK.release()


def main():
    port = SETTINGS["port"]
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    lib.init_db()
    try:
        os.makedirs(SETTINGS["uploadDir"], exist_ok=True)
    except OSError as e:
        print(f"上传目录创建失败：{e}", file=sys.stderr)
    host = SETTINGS["host"]
    httpd = Server((host, port), Handler)
    shown = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    print(f"Bookcase {VERSION} 已启动: http://{shown}:{port}", flush=True)
    if host in ("0.0.0.0", "::", ""):
        lan = local_ip()
        if lan:
            print(f"局域网访问: http://{lan}:{port}", flush=True)
    if not SETTINGS["token"] and host not in ("127.0.0.1", "localhost"):
        print("警告：服务对局域网开放但没设访问口令，config.json 里加 \"token\" 更安全。",
              file=sys.stderr)
    print(f"书库目录: {', '.join(SETTINGS['roots'])}", flush=True)
    print(f"上传目录: {SETTINGS['uploadDir']}", flush=True)
    auto_min = SETTINGS.get("autoScanMinutes") or 0
    if auto_min:
        threading.Thread(target=auto_scan_worker, args=(auto_min,), daemon=True).start()
        print(f"自动扫描: 每 {auto_min:g} 分钟", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
