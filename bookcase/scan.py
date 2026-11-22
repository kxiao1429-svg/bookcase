# -*- coding: utf-8 -*-
"""扫描手机存储里的电子书，提取元数据与封面，写入 SQLite 书库。

只读文件内容，不改动、不移动任何原始电子书。
"""

import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import sqlite3
import time
import zipfile
import xml.etree.ElementTree as ET

VERSION = "2.0.0"

BASE = os.path.dirname(os.path.abspath(__file__))
# 数据目录：部署到 NAS / Docker 时用 BOOKCASE_DATA 指到持久化卷，程序和书分开存
DATA_DIR = os.path.abspath(os.path.expanduser(os.environ.get("BOOKCASE_DATA") or BASE))
DB_PATH = os.path.join(DATA_DIR, "library.db")
COVER_DIR = os.path.join(DATA_DIR, "covers")
CONFIG_PATH = os.path.abspath(os.path.expanduser(
    os.environ.get("BOOKCASE_CONFIG") or os.path.join(BASE, "config.json")))
TRASH_DIR = "/sdcard/Download/.bookcase-trash"

DEFAULT_ROOTS = ["/sdcard/Download", "/sdcard/Books", "/sdcard/Documents"]
FORMATS = {
    ".epub": "epub", ".pdf": "pdf", ".mobi": "mobi", ".azw3": "azw3",
    ".azw": "azw", ".fb2": "fb2", ".cbz": "cbz", ".djvu": "djvu",
}
SKIP_DIRS = {
    "Android", ".thumbnails", ".trash", ".bookcase-trash", "node_modules",
    ".git", "LOST.DIR", "DCIM", ".cache",
}
DC = "http://purl.org/dc/elements/1.1/"

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT UNIQUE NOT NULL,
  format TEXT,
  title TEXT,
  author TEXT,
  publisher TEXT,
  year TEXT,
  description TEXT,
  pages INTEGER DEFAULT 0,
  size INTEGER DEFAULT 0,
  file_hash TEXT,
  cover TEXT,
  mtime REAL DEFAULT 0,
  added_at REAL DEFAULT 0,
  updated_at REAL DEFAULT 0,
  status TEXT DEFAULT 'unread',
  progress INTEGER DEFAULT 0,
  cfi TEXT DEFAULT '',
  favorite INTEGER DEFAULT 0,
  junk INTEGER DEFAULT 0,
  broken INTEGER DEFAULT 0,
  tags TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
CREATE INDEX IF NOT EXISTS idx_books_hash ON books(file_hash);
CREATE TABLE IF NOT EXISTS lists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  note TEXT DEFAULT '',
  created_at REAL DEFAULT 0,
  updated_at REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS list_books (
  list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  added_at REAL DEFAULT 0,
  PRIMARY KEY (list_id, book_id)
);
CREATE INDEX IF NOT EXISTS idx_list_books_book ON list_books(book_id);
"""


# ---------------------------------------------------------------- 基础工具

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8765,
    "token": "",
    "maxUploadMB": 4096,
    "uploadDir": "",
}


def _env(name):
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else ""


def _covered(path, roots):
    for r in roots:
        try:
            if os.path.commonpath([path, r]) == r:
                return True
        except ValueError:  # Windows 上不同盘符
            continue
    return False


def load_settings():
    """读取 config.json + 环境变量；返回服务需要的全部配置。

    优先级：环境变量 > config.json > 默认值。环境变量是给 Docker / NAS 用的，
    普通电脑上直接改 config.json 就行。
    """
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception:
            cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}

    roots = cfg.get("roots") if isinstance(cfg.get("roots"), list) and cfg["roots"] else list(DEFAULT_ROOTS)
    env_roots = _env("BOOKCASE_ROOTS")
    if env_roots:
        roots = [r for r in env_roots.split(os.pathsep) if r.strip()]
    explicit_roots = bool(env_roots) or bool(cfg.get("roots"))
    roots = [str(r).strip() for r in roots if str(r).strip()]

    trash = str(cfg.get("trashDir") or TRASH_DIR)
    if _env("BOOKCASE_TRASH_DIR"):
        trash = _env("BOOKCASE_TRASH_DIR")

    host = _env("BOOKCASE_HOST") or str(cfg.get("host") or DEFAULTS["host"])
    try:
        port = int(_env("BOOKCASE_PORT") or cfg.get("port") or DEFAULTS["port"])
    except (TypeError, ValueError):
        port = DEFAULTS["port"]
    token = _env("BOOKCASE_TOKEN") or str(cfg.get("token") or "")
    try:
        max_mb = int(cfg.get("maxUploadMB") or DEFAULTS["maxUploadMB"])
    except (TypeError, ValueError):
        max_mb = DEFAULTS["maxUploadMB"]
    max_mb = int(os.environ.get("BOOKCASE_MAX_UPLOAD_MB") or max_mb)

    try:
        auto_min = float(_env("BOOKCASE_AUTO_SCAN_MIN") or cfg.get("autoScanMinutes") or 0)
    except (TypeError, ValueError):
        auto_min = 0
    auto_min = max(0.0, min(auto_min, 7 * 24 * 60))

    upload_dir = _env("BOOKCASE_UPLOAD_DIR") or str(cfg.get("uploadDir") or "")

    raw_roots = list(roots)
    roots = [r for r in raw_roots if os.path.isdir(r)]
    if not roots:
        if explicit_roots:
            # 配置里写明的目录暂时不可用（网络盘没挂载等）：保留配置，
            # 别偷偷退到当前目录，否则会把项目目录当书库、上传也落到错误位置
            roots = raw_roots
        else:
            # 电脑上通常没有 /sdcard：退到 ~/Books 或当前目录
            fallback = os.path.expanduser("~/Books")
            roots = [fallback if os.path.isdir(fallback) else os.getcwd()]

    seen, out = set(), []
    for r in roots:
        r = os.path.abspath(os.path.expanduser(r)).rstrip("/") or "/"
        if r not in seen:
            seen.add(r)
            out.append(r)
    roots = out

    if not upload_dir:
        upload_dir = os.path.join(roots[0], "书库上传")
    upload_dir = os.path.abspath(os.path.expanduser(upload_dir)).rstrip("/") or "/"

    # 上传目录必须在扫描范围内，否则传完了书架看不见
    if not _covered(upload_dir, roots):
        roots = roots + [upload_dir]

    return {
        "roots": roots,
        "trashDir": os.path.expanduser(trash),
        "host": host,
        "port": port,
        "token": token,
        "maxUploadMB": max_mb,
        "autoScanMinutes": auto_min,
        "uploadDir": upload_dir,
        "dataDir": DATA_DIR,
        "configPath": CONFIG_PATH,
        "authRequired": bool(token),
        "version": VERSION,
    }


def load_config():
    return load_settings()["roots"]


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    os.makedirs(COVER_DIR, exist_ok=True)
    conn = connect()
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(books)")}
    if "broken" not in cols:
        conn.execute("ALTER TABLE books ADD COLUMN broken INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


def clean_name(stem):
    """把脏文件名收拾成可读标题。"""
    s = stem
    s = re.sub(r"\s*[\(\[（【][^\)\]）】]*z-?lib(?:rary)?[^\)\]）】]*[\)\]）】]", "", s, flags=re.I)
    s = re.sub(r"\s*[\(\[（【](?:Libgen|Anna'?s? Archive|标准版|精校版)[^\)\]）】]*[\)\]）】]", "", s, flags=re.I)
    s = re.sub(r"\s*[\(\[（【]\s*(?:epub|pdf|mobi|azw3|txt)\s*[\)\]）】]", "", s, flags=re.I)
    s = re.sub(r"\s*[-_]\s*\d+$", "", s)
    s = re.sub(r"\s*\(\d+\)$", "", s)
    s = re.sub(r"\s+", " ", s).strip(" ._-—")
    return s or stem


BAD_TITLES = {"untitled", "unknown", "document", "title", "无标题", "未知", ""}


def good_title(title, stem):
    t = (title or "").strip()
    if t.lower() in BAD_TITLES:
        return clean_name(stem)
    return t


def strip_html(text):
    if not text:
        return ""
    t = html.unescape(str(text))
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.I | re.S)
    t = re.sub(r"<br\s*/?>|</p>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\u3000]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def file_hash(path, cap=80 * 1024 * 1024):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            cap -= len(chunk)
            if cap <= 0:
                break
    return h.hexdigest()


def save_cover_bytes(data, book_id):
    """按图片魔数决定扩展名，落盘到 covers/。"""
    if not data:
        return None
    if data[:3] == b"\xff\xd8\xff":
        ext = "jpg"
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ext = "webp"
    elif data[:3] == b"GIF":
        ext = "gif"
    else:
        return None
    name = f"{book_id}.{ext}"
    with open(os.path.join(COVER_DIR, name), "wb") as f:
        f.write(data)
    for old in ("jpg", "png", "webp", "gif"):
        if old != ext:
            try:
                os.remove(os.path.join(COVER_DIR, f"{book_id}.{old}"))
            except OSError:
                pass
    return name


# ---------------------------------------------------------------- EPUB 解析

def _find_local(base_dir, href):
    href = href.split("#")[0]
    if "://" in href:
        return None
    path = posixpath.normpath(posixpath.join(base_dir, href))
    return path.lstrip("/")


def read_epub(path):
    info = {"title": "", "author": "", "publisher": "", "year": "",
            "description": "", "cover": None, "broken": False}
    if not zipfile.is_zipfile(path):
        info["broken"] = True
        return info
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            opf_path = None
            if "META-INF/container.xml" in names:
                try:
                    root = ET.fromstring(z.read("META-INF/container.xml"))
                    for el in root.iter():
                        if el.tag.endswith("rootfile"):
                            opf_path = el.get("full-path")
                            break
                except Exception:
                    pass
            if not opf_path:
                opf_path = next((n for n in names if n.lower().endswith(".opf")), None)
            if not opf_path:
                return info
            opf = ET.fromstring(z.read(opf_path))
            base_dir = posixpath.dirname(opf_path)

            def dc_text(tag):
                out = []
                for el in opf.iter():
                    if el.tag == f"{{{DC}}}{tag}" and (el.text or "").strip():
                        out.append(el.text.strip())
                return out

            titles = dc_text("title")
            authors = dc_text("creator")
            info["title"] = titles[0] if titles else ""
            info["author"] = " / ".join(dict.fromkeys(authors)) if authors else ""
            pub = dc_text("publisher")
            info["publisher"] = pub[0] if pub else ""
            date = dc_text("date")
            if date:
                m = re.search(r"\d{4}", date[0])
                if m and 1000 <= int(m.group(0)) <= 2100:
                    info["year"] = m.group(0)
            desc = dc_text("description")
            info["description"] = strip_html(desc[0])[:1500] if desc else ""

            # 找封面：先是 properties="cover-image"，再是 meta[name=cover]
            items = []
            for el in opf.iter():
                if el.tag.endswith("item") and el.get("href"):
                    items.append({
                        "id": el.get("id", ""),
                        "href": el.get("href", ""),
                        "type": el.get("media-type", ""),
                        "props": el.get("properties", "") or "",
                    })
            cover_href = ""
            for it in items:
                if "cover-image" in it["props"]:
                    cover_href = it["href"]
                    break
            if not cover_href:
                cover_id = ""
                for el in opf.iter():
                    if el.tag.endswith("meta") and (el.get("name") or "").lower() == "cover":
                        cover_id = el.get("content", "")
                if cover_id:
                    for it in items:
                        if it["id"] == cover_id:
                            cover_href = it["href"]
                            break
            if not cover_href:
                for it in items:
                    if "image" in it["type"] and "cover" in it["href"].lower():
                        cover_href = it["href"]
                        break
            if cover_href:
                p = _find_local(base_dir, cover_href)
                if p and p in names:
                    info["cover"] = z.read(p)
    except Exception:
        pass
    return info


# ---------------------------------------------------------------- PDF 解析

def read_pdf(path):
    info = {"title": "", "author": "", "publisher": "", "year": "",
            "description": "", "cover": None, "pages": 0, "broken": False}
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            # 没装 PyMuPDF 也不影响索引：按文件名收录，只是没有封面和页数
            return info
    try:
        doc = fitz.open(path)
    except Exception:
        info["broken"] = True
        return info
    if doc.needs_pass:
        info["broken"] = True
        doc.close()
        return info
    try:
        md = doc.metadata or {}
        info["title"] = (md.get("title") or "").strip()
        info["author"] = (md.get("author") or "").strip()
        info["publisher"] = (md.get("subject") or "").strip()
        info["pages"] = doc.page_count
        info["description"] = strip_html(md.get("subject") or "")[:800]
        if doc.page_count:
            page = doc.load_page(0)
            w = page.rect.width or 600
            zoom = min(1.5, 520.0 / w)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            info["cover"] = pix.tobytes("png")
    except Exception:
        pass
    finally:
        doc.close()
    return info


# ---------------------------------------------------------------- 杂项判定

STRONG_JUNK = re.compile(
    r"学籍|在线验证|学历证书|身份证|发票|简历|成绩单|账单|申请表|录取通知|"
    r"报告单|体检|截图|screenshot|扫描件|报名表|缴费|流水", re.I)
WEAK_JUNK = re.compile(r"报告|证明|证书|通知|合同|填表", re.I)


def detect_junk(name, title, fmt, pages, size):
    blob = f"{name} {title or ''}"
    if size < 18 * 1024:
        return 1
    if fmt == "pdf":
        if STRONG_JUNK.search(blob):
            return 1
        if pages and pages <= 3 and WEAK_JUNK.search(blob):
            return 1
        if pages and pages <= 1 and size < 420 * 1024:
            return 1
    return 0


# ---------------------------------------------------------------- 扫描

class Scanner:
    def __init__(self):
        self.running = False
        self.phase = "idle"
        self.done = 0
        self.total = 0
        self.current = ""
        self.started_at = 0
        self.finished_at = 0
        self.last_result = None
        self.error = ""

    def snapshot(self):
        return {
            "running": self.running, "phase": self.phase, "done": self.done,
            "total": self.total, "current": self.current,
            "startedAt": self.started_at, "finishedAt": self.finished_at,
            "lastResult": self.last_result, "error": self.error,
        }


SCANNER = Scanner()


def collect_files(roots):
    files = []
    for root in roots:
        base_depth = root.rstrip("/").count("/")
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath.count("/") - base_depth
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not d.startswith(".") and depth < 6
            ]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext in FORMATS:
                    files.append(os.path.join(dirpath, fn))
    return sorted(files)


def index_file(conn, path, old=None):
    """解析单个文件并写进 books 表。old 是同一路径的旧行，用于增量更新。

    返回 book_id；调用方负责 commit。上传接口也直接复用它，保证
    “网页上传的书”和“扫描出来的书”走完全一样的元数据流程。
    """
    st = os.stat(path)
    fmt = FORMATS[os.path.splitext(path)[1].lower()]
    stem = os.path.splitext(os.path.basename(path))[0]
    meta = {"title": "", "author": "", "publisher": "", "year": "",
            "description": "", "cover": None, "pages": 0, "broken": False}
    if fmt == "epub":
        meta = read_epub(path)
    elif fmt == "pdf":
        meta = read_pdf(path)

    title = good_title(meta.get("title"), stem)
    now = time.time()
    junk = detect_junk(os.path.basename(path), title, fmt, meta.get("pages") or 0, st.st_size)
    broken = 1 if meta.get("broken") else 0
    try:
        fh = file_hash(path)
    except OSError:
        fh = ""

    if old:
        conn.execute(
            """UPDATE books SET format=?, title=?, author=?, publisher=?, year=?,
               description=?, pages=?, size=?, file_hash=?, mtime=?, updated_at=?, junk=?,
               broken=?
               WHERE id=?""",
            (fmt, title, meta.get("author") or "", meta.get("publisher") or "",
             meta.get("year") or "", meta.get("description") or "",
             meta.get("pages") or 0, st.st_size, fh, st.st_mtime, now, junk, broken,
             old["id"]))
        book_id = old["id"]
    else:
        cur = conn.execute(
            """INSERT INTO books (path, format, title, author, publisher, year,
               description, pages, size, file_hash, mtime, added_at, updated_at, junk, broken)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (path, fmt, title, meta.get("author") or "", meta.get("publisher") or "",
             meta.get("year") or "", meta.get("description") or "",
             meta.get("pages") or 0, st.st_size, fh, st.st_mtime, now, now, junk, broken))
        book_id = cur.lastrowid

    if meta.get("cover"):
        cover_name = save_cover_bytes(meta["cover"], book_id)
        if cover_name:
            conn.execute("UPDATE books SET cover=? WHERE id=?", (cover_name, book_id))
    return book_id


def index_uploaded(path):
    """网页刚上传完的文件：立刻建索引，返回 book_id。"""
    init_db()
    conn = connect()
    try:
        old = conn.execute("SELECT * FROM books WHERE path=?", (path,)).fetchone()
        book_id = index_file(conn, path, old)
        conn.commit()
        return book_id
    finally:
        conn.close()


def find_duplicate(size, digest):
    """按大小 + 内容哈希找已经收过的书。"""
    if not digest or not size:
        return None
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM books WHERE size=? AND file_hash=? ORDER BY id LIMIT 1",
            (size, digest)).fetchone()
    finally:
        conn.close()
    if row and os.path.isfile(row["path"]):
        return row
    return None


def find_by_name_size(name, size):
    """按文件名 + 大小判断云端是否已有（上传前的秒级预检）。"""
    base = os.path.basename(str(name or "").replace("\\", "/")).lower()
    if not base or not size:
        return None
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM books WHERE size=?", (int(size),)).fetchall()
    finally:
        conn.close()
    for row in rows:
        if os.path.basename(row["path"]).lower() == base and os.path.isfile(row["path"]):
            return row
    return None


def scan(progress_cb=None):
    init_db()
    roots = load_config()
    SCANNER.running = True
    SCANNER.phase = "collect"
    SCANNER.done, SCANNER.total, SCANNER.error = 0, 0, ""
    SCANNER.started_at = time.time()
    SCANNER.finished_at = 0
    SCANNER.current = ""
    if progress_cb:
        progress_cb(SCANNER.snapshot())

    added = updated = removed = 0
    failed = 0
    try:
        files = collect_files(roots)
        SCANNER.total = len(files)
        SCANNER.phase = "parse"
        conn = connect()
        existing = {r["path"]: r for r in conn.execute("SELECT * FROM books")}
        seen = set()

        for idx, path in enumerate(files, 1):
            seen.add(path)
            SCANNER.done = idx
            SCANNER.current = os.path.basename(path)
            try:
                st = os.stat(path)
            except OSError:
                continue
            old = existing.get(path)
            fmt = FORMATS[os.path.splitext(path)[1].lower()]
            if old:
                cover_ok = (not old["cover"]) or os.path.exists(
                    os.path.join(COVER_DIR, old["cover"]))
                # epub/pdf 第一次扫出封面之前要重扫；mobi 这类没有封面的别反复算哈希
                needs_cover = fmt in ("epub", "pdf") and not old["cover"]
                unchanged = (old["size"] == st.st_size
                             and abs((old["mtime"] or 0) - st.st_mtime) < 1
                             and cover_ok and not needs_cover)
            else:
                unchanged = False
            if unchanged:
                if progress_cb:
                    progress_cb(SCANNER.snapshot())
                continue

            try:
                index_file(conn, path, old)
                conn.commit()
            except Exception as e:
                # 单个文件坏了不该拖垮整次扫描
                failed += 1
                SCANNER.error = f"{os.path.basename(path)}: {type(e).__name__}: {e}"
                try:
                    conn.rollback()
                except Exception:
                    pass
                if progress_cb:
                    progress_cb(SCANNER.snapshot())
                continue

            if old:
                updated += 1
            else:
                added += 1
            if progress_cb:
                progress_cb(SCANNER.snapshot())

        for path, row in existing.items():
            if path not in seen and os.path.exists(path) is False:
                conn.execute("DELETE FROM books WHERE id=?", (row["id"],))
                removed += 1
            elif path not in seen and row["junk"]:
                pass
        conn.commit()
        conn.close()
        SCANNER.phase = "done"
        SCANNER.last_result = {
            "added": added, "updated": updated, "removed": removed, "failed": failed,
            "total": len(files), "roots": roots,
            "seconds": round(time.time() - SCANNER.started_at, 1),
        }
    except Exception as e:  # 扫描失败要能看见原因
        SCANNER.phase = "error"
        SCANNER.error = f"{type(e).__name__}: {e}"
    finally:
        SCANNER.running = False
        SCANNER.finished_at = time.time()
        if progress_cb:
            progress_cb(SCANNER.snapshot())


# ---------------------------------------------------------------- 清理建议

def normalize_title_key(title):
    t = clean_name(title or "")
    t = re.sub(r"[\s·:：,，.。!！?？\-—_/\\()（）\[\]【】]", "", t)
    return t.lower()


def cleanup_report():
    conn = connect()
    rows = [dict(r) for r in conn.execute("SELECT * FROM books ORDER BY title")]
    conn.close()
    by_hash = {}
    for r in rows:
        if r["file_hash"]:
            by_hash.setdefault(r["file_hash"], []).append(r)
    duplicates = []
    for h, group in by_hash.items():
        if len(group) > 1:
            group.sort(key=lambda x: (x["size"], x["path"]))
            duplicates.append({"hash": h[:12], "books": group})

    by_title = {}
    for r in rows:
        if r["junk"] or not r["title"]:
            continue
        by_title.setdefault(normalize_title_key(r["title"]), []).append(r)
    same_title = [
        {"key": k, "books": g} for k, g in by_title.items()
        if len(g) > 1 and len({b["file_hash"] for b in g}) > 1
    ]

    junk = [r for r in rows if r["junk"] and not r["broken"]]
    broken = [r for r in rows if r["broken"]]
    dirty = []
    for r in rows:
        stem = os.path.splitext(os.path.basename(r["path"]))[0]
        cleaned = clean_name(stem)
        if cleaned != stem:
            dirty.append({"book": r, "newName": cleaned + os.path.splitext(r["path"])[1]})
    return {"duplicates": duplicates, "sameTitle": same_title,
            "junk": junk, "broken": broken, "dirtyNames": dirty}


def rename_clean(book_id):
    conn = connect()
    row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    conn.close()
    if not row:
        return False, "书不存在"
    stem, ext = os.path.splitext(os.path.basename(row["path"]))
    new_stem = clean_name(stem)
    if new_stem == stem:
        return False, "文件名已经干净"
    new_path = os.path.join(os.path.dirname(row["path"]), new_stem + ext)
    if os.path.exists(new_path):
        return False, "目标文件名已存在"
    os.rename(row["path"], new_path)
    conn = connect()
    conn.execute("UPDATE books SET path=? WHERE id=?", (new_path, book_id))
    conn.commit()
    conn.close()
    return True, new_path


def rename_all_clean():
    """把所有带垃圾后缀的文件名统一整理，冲突的跳过。"""
    conn = connect()
    rows = [dict(r) for r in conn.execute("SELECT * FROM books WHERE junk=0")]
    done = skipped = 0
    for r in rows:
        stem, ext = os.path.splitext(os.path.basename(r["path"]))
        new_stem = clean_name(stem)
        if new_stem == stem:
            continue
        target = os.path.join(os.path.dirname(r["path"]), new_stem + ext)
        if os.path.exists(target):
            skipped += 1
            continue
        try:
            os.rename(r["path"], target)
        except OSError:
            skipped += 1
            continue
        conn.execute("UPDATE books SET path=? WHERE id=?", (target, r["id"]))
        done += 1
    conn.commit()
    conn.close()
    msg = f"已重命名 {done} 个文件"
    if skipped:
        msg += f"，{skipped} 个因重名跳过"
    return True, msg


def _writable_trash_dir():
    d = load_settings().get("trashDir") or TRASH_DIR
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".write-test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return d
    except OSError:
        alt = os.path.join(BASE, "trash")
        os.makedirs(alt, exist_ok=True)
        return alt


def trash_book(book_id):
    conn = connect()
    row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    conn.close()
    if not row:
        return False, "书不存在"
    trash_dir = _writable_trash_dir()
    os.makedirs(trash_dir, exist_ok=True)
    base = os.path.basename(row["path"])
    target = os.path.join(trash_dir, base)
    n = 1
    while os.path.exists(target):
        stem, ext = os.path.splitext(base)
        target = os.path.join(trash_dir, f"{stem}-{n}{ext}")
        n += 1
    shutil.move(row["path"], target)
    conn = connect()
    conn.execute("DELETE FROM books WHERE id=?", (book_id,))
    conn.commit()
    conn.close()
    return True, target


def archive_book(book_id, reason="重复归档"):
    """把书移进回收站下的归档目录（默认 回收站/重复归档/日期/），并从书库删除。

    和直接删除不同：文件还在，按日期分目录，事后能找回来。
    """
    conn = connect()
    row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    conn.close()
    if not row:
        return False, "书不存在"
    if not os.path.isfile(row["path"]):
        return False, "原文件不在了"

    root = _writable_trash_dir()
    dest_dir = os.path.join(root, reason, time.strftime("%Y%m%d"))
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.basename(row["path"])
    target = os.path.join(dest_dir, base)
    n = 1
    while os.path.exists(target):
        stem, ext = os.path.splitext(base)
        target = os.path.join(dest_dir, f"{stem}-{n}{ext}")
        n += 1
    try:
        shutil.move(row["path"], target)
    except OSError as e:
        return False, f"移动到归档目录失败：{e}"
    conn = connect()
    conn.execute("DELETE FROM books WHERE id=?", (book_id,))
    conn.commit()
    conn.close()
    return True, target


if __name__ == "__main__":
    def cli_progress(s):
        if s["phase"] == "parse" and s["done"] % 10 == 0:
            print(f"  {s['done']}/{s['total']} {s['current'][:40]}")
    print("扫描中…")
    scan(cli_progress)
    print(json.dumps(SCANNER.last_result, ensure_ascii=False, indent=2))
