# -*- coding: utf-8 -*-
"""书单：按阶段/主题组织的独立书架。

数据在 lists（书单）和 list_books（书单-书 关联）两张表里，
删书单只删关联，不动书；删书（移入回收站）会通过外键自动清掉关联。
"""

import os
import re
import time

import scan as lib


def _clean(name, note):
    name = (name or "").strip()[:40]
    note = (note or "").strip()[:2000]
    return name, note


def _list_stats(conn, list_id):
    rows = conn.execute(
        """SELECT b.id, b.title, b.cover, b.progress, b.status
           FROM list_books lb JOIN books b ON b.id = lb.book_id
           WHERE lb.list_id = ?
           ORDER BY lb.added_at ASC, b.id ASC""",
        (list_id,),
    ).fetchall()
    n = len(rows)
    avg = round(sum((r["progress"] or 0) for r in rows) / n) if n else 0
    return {
        "count": n,
        "avgProgress": avg,
        "reading": sum(1 for r in rows if r["status"] == "reading"),
        "done": sum(1 for r in rows if r["status"] == "done"),
        "covers": [r["id"] for r in rows if r["cover"]][:4],
        "bookIds": [r["id"] for r in rows],
    }


def list_lists():
    conn = lib.connect()
    out = []
    for r in conn.execute("SELECT * FROM lists ORDER BY created_at ASC, id ASC"):
        d = dict(r)
        d.update(_list_stats(conn, d["id"]))
        out.append(d)
    conn.close()
    return out


def get_list(list_id):
    conn = lib.connect()
    row = conn.execute("SELECT * FROM lists WHERE id=?", (list_id,)).fetchone()
    if not row:
        conn.close()
        return None
    d = dict(row)
    d.update(_list_stats(conn, list_id))
    conn.close()
    return d


def create_list(name, note=""):
    name, note = _clean(name, note)
    if not name:
        return None, "书单需要一个名字"
    now = time.time()
    conn = lib.connect()
    cur = conn.execute(
        "INSERT INTO lists (name, note, created_at, updated_at) VALUES (?,?,?,?)",
        (name, note, now, now),
    )
    conn.commit()
    list_id = cur.lastrowid
    conn.close()
    return get_list(list_id), None


def update_list(list_id, name=None, note=None):
    conn = lib.connect()
    row = conn.execute("SELECT * FROM lists WHERE id=?", (list_id,)).fetchone()
    if not row:
        conn.close()
        return None, "书单不存在"
    new_name = row["name"] if name is None else _clean(name, "")[0]
    new_note = row["note"] if note is None else (note or "").strip()[:2000]
    if not new_name:
        conn.close()
        return None, "书单名不能为空"
    conn.execute(
        "UPDATE lists SET name=?, note=?, updated_at=? WHERE id=?",
        (new_name, new_note, time.time(), list_id),
    )
    conn.commit()
    conn.close()
    return get_list(list_id), None


def delete_list(list_id):
    conn = lib.connect()
    row = conn.execute("SELECT id FROM lists WHERE id=?", (list_id,)).fetchone()
    if not row:
        conn.close()
        return False, "书单不存在"
    conn.execute("DELETE FROM lists WHERE id=?", (list_id,))
    conn.commit()
    conn.close()
    for e in ("jpg", "png", "webp", "gif"):
        try:
            os.remove(os.path.join(lib.COVER_DIR, f"list-{list_id}.{e}"))
        except OSError:
            pass
    return True, "已删除书单"


def set_books(list_id, book_ids, replace=False):
    """把书加进书单；replace=True 时以传入列表为准做全量替换。"""
    conn = lib.connect()
    if not conn.execute("SELECT id FROM lists WHERE id=?", (list_id,)).fetchone():
        conn.close()
        return False, "书单不存在"
    ids = []
    for b in book_ids or []:
        try:
            b = int(b)
        except (TypeError, ValueError):
            continue
        if b not in ids:
            ids.append(b)
    if replace:
        conn.execute("DELETE FROM list_books WHERE list_id=?", (list_id,))
    now = time.time()
    added = 0
    for b in ids:
        exists = conn.execute("SELECT 1 FROM books WHERE id=?", (b,)).fetchone()
        if not exists:
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO list_books (list_id, book_id, added_at) VALUES (?,?,?)",
            (list_id, b, now),
        )
        added += cur.rowcount
    conn.execute("UPDATE lists SET updated_at=? WHERE id=?", (time.time(), list_id))
    conn.commit()
    conn.close()
    return True, f"已更新书单（新增 {added} 本）"


def remove_book(list_id, book_id):
    conn = lib.connect()
    conn.execute("DELETE FROM list_books WHERE list_id=? AND book_id=?", (list_id, book_id))
    conn.execute("UPDATE lists SET updated_at=? WHERE id=?", (time.time(), list_id))
    conn.commit()
    conn.close()
    return True, "已从书单移除"


def _norm(s):
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def export_lists():
    """导出所有书单，书用 哈希/书名/作者 标识，跨设备可用。"""
    conn = lib.connect()
    out = []
    for l in conn.execute("SELECT * FROM lists ORDER BY created_at ASC, id ASC"):
        books = []
        for b in conn.execute(
            """SELECT b.file_hash, b.title, b.author, b.format
               FROM list_books lb JOIN books b ON b.id = lb.book_id
               WHERE lb.list_id = ? ORDER BY lb.added_at ASC""",
            (l["id"],),
        ):
            books.append({
                "hash": b["file_hash"] or "",
                "title": b["title"] or "",
                "author": b["author"] or "",
                "format": b["format"] or "",
            })
        out.append({"name": l["name"], "note": l["note"] or "", "books": books})
    conn.close()
    return {"version": 1, "exportedAt": time.time(), "lists": out}


def import_lists(data):
    """把导出的书单写回本机；书按 哈希 → 书名+作者 → 书名 匹配。"""
    if not isinstance(data, dict) or not isinstance(data.get("lists"), list):
        return None, "书单格式不对"
    conn = lib.connect()
    by_hash, by_ta, by_title = {}, {}, {}
    for b in conn.execute("SELECT id, file_hash, title, author FROM books WHERE junk=0"):
        if b["file_hash"]:
            by_hash.setdefault(b["file_hash"], []).append(b["id"])
        by_ta.setdefault((_norm(b["title"]), _norm(b["author"])), []).append(b["id"])
        by_title.setdefault(_norm(b["title"]), []).append(b["id"])

    created = merged = matched = missing = 0
    miss_titles = []
    for item in data["lists"]:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()[:40]
        if not name:
            continue
        note = (item.get("note") or "").strip()[:2000]
        row = conn.execute("SELECT id FROM lists WHERE name=?", (name,)).fetchone()
        if row:
            list_id = row["id"]
            merged += 1
        else:
            cur = conn.execute(
                "INSERT INTO lists (name, note, created_at, updated_at) VALUES (?,?,?,?)",
                (name, note, time.time(), time.time()),
            )
            list_id = cur.lastrowid
            created += 1
        for bk in item.get("books") or []:
            if not isinstance(bk, dict):
                continue
            bid = None
            h = bk.get("hash") or ""
            if h and h in by_hash:
                bid = by_hash[h][0]
            if bid is None:
                ids = by_ta.get((_norm(bk.get("title")), _norm(bk.get("author"))), [])
                if len(ids) == 1:
                    bid = ids[0]
            if bid is None:
                ids = by_title.get(_norm(bk.get("title")), [])
                if len(ids) == 1:
                    bid = ids[0]
            if bid is None:
                missing += 1
                miss_titles.append((bk.get("title") or "")[:40])
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO list_books (list_id, book_id, added_at) VALUES (?,?,?)",
                (list_id, bid, time.time()),
            )
            matched += cur.rowcount
        conn.execute("UPDATE lists SET updated_at=? WHERE id=?", (time.time(), list_id))
    conn.commit()
    conn.close()
    return {
        "created": created, "merged": merged,
        "matched": matched, "missing": missing,
        "missingTitles": miss_titles[:30],
    }, ""
