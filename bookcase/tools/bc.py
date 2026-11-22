# -*- coding: utf-8 -*-
"""Bookcase 命令行：给人（和 AI）用的书库操作接口。

依赖只有 Python 标准库。默认读项目根目录的 config.json 拿端口/口令。

常用：
  python tools/bc.py stats
  python tools/bc.py catalog -o catalog.json
  python tools/bc.py dupes
  python tools/bc.py archive-dupes            # 只打印计划
  python tools/bc.py archive-dupes --apply    # 每组保留第一本，其余归档
  python tools/bc.py lists
  python tools/bc.py list-create "2026 冬 · 思维环"
  python tools/bc.py list-add 3 12 15 18
  python tools/bc.py tag 12 社会学 待读
  python tools/bc.py digest
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.json")


def load_conf():
    server = os.environ.get("BOOKCASE_SERVER", "")
    token = os.environ.get("BOOKCASE_TOKEN", "")
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception:
            cfg = {}
        if not server:
            server = "http://127.0.0.1:%s" % (cfg.get("port") or 8765)
        if not token:
            token = str(cfg.get("token") or "")
    return server or "http://127.0.0.1:8765", token


def api(args, method, path, body=None):
    url = args.server.rstrip("/") + path
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    if args.token:
        req.add_header("Authorization", "Bearer " + args.token)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
        except Exception:
            msg = str(e)
        raise SystemExit("接口错误 %s: %s" % (e.code, msg))
    except urllib.error.URLError as e:
        raise SystemExit("连不上书库（%s）：%s\n先启动 start-windows.bat，或检查 --server。"
                         % (url, e.reason))


def fmt_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.0f %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def keep_key(b):
    """重复书里优先保留“文件名最干净”的那本，而不是碰巧排在前面的副本。"""
    name = os.path.basename(b.get("path") or "").lower()
    dirty = sum(1 for s in ("副本", "copy", "拷贝", "备份", "(1)", "(2)", "-1.") if s in name)
    return (dirty, len(name), name)


def print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_stats(args):
    s = api(args, "GET", "/api/stats")
    print("共 %s 本 · %s · 在读 %s · 读完 %s · 重复/杂项待清理"
          % (s.get("total"), s.get("sizeHuman"), s.get("reading"), s.get("done")))
    print("格式：%s" % ", ".join("%s %s" % (k, v) for k, v in (s.get("formats") or {}).items()))


def cmd_catalog(args):
    c = api(args, "GET", "/api/catalog")
    out = args.output or os.path.join(os.getcwd(), "catalog.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    print("已导出 %s：%s 本，%s 个书单，%s 组重复"
          % (out, len(c.get("books") or []), len(c.get("lists") or []),
             len((c.get("cleanup") or {}).get("duplicates") or [])))


def cmd_digest(args):
    c = api(args, "GET", "/api/catalog")
    lists = {l["id"]: l["name"] for l in (c.get("lists") or [])}
    lines = []
    for b in c.get("books") or []:
        if b.get("junk"):
            continue
        tags = ",".join(b.get("tags") or [])
        lnames = ",".join(lists.get(i, str(i)) for i in (b.get("listIds") or []))
        lines.append("#%s | %s | %s | %s | %s | %s%% | %s | %s" % (
            b["id"], b.get("title") or "", b.get("author") or "未知作者",
            (b.get("format") or "").upper(), b.get("status") or "unread",
            b.get("progress") or 0, tags or "-", lnames or "-"))
    print("\n".join(lines))


def cmd_dupes(args):
    c = api(args, "GET", "/api/catalog")
    groups = (c.get("cleanup") or {}).get("duplicates") or []
    if args.json:
        print_json(groups)
        return
    if not groups:
        print("没有完全重复的书。")
        return
    for i, g in enumerate(groups, 1):
        print("组 %s（hash %s）" % (i, g.get("hash")))
        books = sorted(g.get("books") or [], key=keep_key)
        for j, b in enumerate(books):
            mark = "保留" if j == 0 else "可归档"
            print("  [%s] #%s %s | %s | %s" % (mark, b["id"], b.get("title"),
                                               fmt_size(b.get("size")), b.get("path")))


def cmd_archive_dupes(args):
    c = api(args, "GET", "/api/catalog")
    groups = (c.get("cleanup") or {}).get("duplicates") or []
    plan = []
    for g in groups:
        books = sorted(g.get("books") or [], key=keep_key)
        if len(books) < 2:
            continue
        plan.append((books[0], books[1:]))
    if not plan:
        print("没有需要归档的重复书。")
        return
    total = sum(len(x[1]) for x in plan)
    for keep, rest in plan:
        print("保留 #%s %s" % (keep["id"], keep.get("title")))
        for b in rest:
            print("  归档 #%s %s（%s）" % (b["id"], b.get("title"), fmt_size(b.get("size"))))
    if not args.apply:
        print("\n共 %s 本待归档。这是预演；确认后加 --apply 执行。" % total)
        return
    ok = fail = 0
    for keep, rest in plan:
        for b in rest:
            r = api(args, "POST", "/api/cleanup", {"action": "archive", "id": b["id"]})
            if r.get("ok"):
                ok += 1
                print("已归档 #%s -> %s" % (b["id"], r.get("message")))
            else:
                fail += 1
                print("失败 #%s：%s" % (b["id"], r.get("message")))
    print("\n完成：归档 %s 本，失败 %s 本。" % (ok, fail))


def cmd_lists(args):
    r = api(args, "GET", "/api/lists")
    for l in r.get("lists") or []:
        print("#%s %s（%s 本，平均 %s%%）%s" % (
            l["id"], l.get("name"), l.get("count"), l.get("avgProgress"),
            (" - " + l["note"]) if l.get("note") else ""))


def cmd_list_create(args):
    r = api(args, "POST", "/api/lists", {"name": args.name, "note": args.note or ""})
    print("已创建书单 #%s %s" % (r["list"]["id"], r["list"]["name"]))


def _ids(values):
    out = []
    for v in values:
        for part in str(v).replace(",", " ").split():
            if part:
                out.append(int(part))
    return out


def cmd_list_add(args):
    ids = _ids(args.ids)
    r = api(args, "POST", "/api/lists/%s/books" % args.list_id,
            {"bookIds": ids, "replace": bool(args.replace)})
    print(r.get("message") or "已更新")


def cmd_list_remove(args):
    for bid in _ids(args.ids):
        api(args, "DELETE", "/api/lists/%s/books/%s" % (args.list_id, bid))
    print("已从书单移除 %s 本" % len(_ids(args.ids)))


def cmd_tag(args, remove=False):
    for bid in _ids(args.ids):
        cur = api(args, "GET", "/api/book/%s" % bid)["book"]
        tags = list(cur.get("tags") or [])
        for t in args.tags:
            t = t.strip()
            if not t:
                continue
            if remove and t in tags:
                tags.remove(t)
            elif not remove and t not in tags:
                tags.append(t)
        api(args, "PATCH", "/api/book/%s" % bid, {"tags": tags})
        print("#%s tags=%s" % (bid, ",".join(tags)))


def cmd_cleanup(args, action):
    for bid in _ids(args.ids):
        r = api(args, "POST", "/api/cleanup", {"action": action, "id": bid})
        print("#%s %s %s" % (bid, "OK" if r.get("ok") else "FAIL", r.get("message")))


def cmd_show(args):
    print_json(api(args, "GET", "/api/book/%s" % args.id)["book"])


def cmd_search(args):
    q = urllib.parse.quote(args.term)
    r = api(args, "GET", "/api/books?q=%s&sort=title" % q)
    for b in r.get("books") or []:
        print("#%s %s | %s | %s" % (b["id"], b["title"], b.get("author") or "",
                                    (b.get("format") or "").upper()))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Bookcase 命令行")
    p.add_argument("--server", default="", help="如 http://127.0.0.1:8765")
    p.add_argument("--token", default="", help="访问口令（默认读 config.json）")
    p.add_argument("--json", action="store_true", help="机器可读输出")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats").set_defaults(func=cmd_stats)

    c = sub.add_parser("catalog")
    c.add_argument("-o", "--output")
    c.set_defaults(func=cmd_catalog)

    sub.add_parser("digest").set_defaults(func=cmd_digest)
    sub.add_parser("dupes").set_defaults(func=cmd_dupes)

    a = sub.add_parser("archive-dupes")
    a.add_argument("--apply", action="store_true")
    a.set_defaults(func=cmd_archive_dupes)

    sub.add_parser("lists").set_defaults(func=cmd_lists)

    c = sub.add_parser("list-create")
    c.add_argument("name")
    c.add_argument("--note", default="")
    c.set_defaults(func=cmd_list_create)

    c = sub.add_parser("list-add")
    c.add_argument("list_id")
    c.add_argument("ids", nargs="+")
    c.add_argument("--replace", action="store_true")
    c.set_defaults(func=cmd_list_add)

    c = sub.add_parser("list-set")
    c.add_argument("list_id")
    c.add_argument("ids", nargs="+")
    c.set_defaults(func=lambda a: cmd_list_add(argparse.Namespace(
        server=a.server, token=a.token, list_id=a.list_id, ids=a.ids, replace=True)))

    c = sub.add_parser("list-remove")
    c.add_argument("list_id")
    c.add_argument("ids", nargs="+")
    c.set_defaults(func=cmd_list_remove)

    c = sub.add_parser("tag")
    c.add_argument("ids", nargs="+")
    c.add_argument("--tags", nargs="+", required=True)
    c.set_defaults(func=lambda a: cmd_tag(a, remove=False))

    c = sub.add_parser("untag")
    c.add_argument("ids", nargs="+")
    c.add_argument("--tags", nargs="+", required=True)
    c.set_defaults(func=lambda a: cmd_tag(a, remove=True))

    c = sub.add_parser("trash")
    c.add_argument("ids", nargs="+")
    c.set_defaults(func=lambda a: cmd_cleanup(a, "trash"))

    c = sub.add_parser("archive")
    c.add_argument("ids", nargs="+")
    c.set_defaults(func=lambda a: cmd_cleanup(a, "archive"))

    c = sub.add_parser("show")
    c.add_argument("id")
    c.set_defaults(func=cmd_show)

    c = sub.add_parser("search")
    c.add_argument("term")
    c.set_defaults(func=cmd_search)

    args = p.parse_args()
    server, token = load_conf()
    args.server = args.server or server
    args.token = args.token or token
    args.func(args)


if __name__ == "__main__":
    main()