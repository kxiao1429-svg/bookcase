# -*- coding: utf-8 -*-
"""APK 入口：把原来在 DSH / 终端里跑的书库服务，搬进应用进程内。

Java 侧只做三件事：申请存储权限、起线程调 run()、把 WebView 指到 127.0.0.1:8765。
数据库、封面、配置放在应用可写目录（/sdcard/Documents/Bookcase），不写进 APK 解压出来的代码目录
（那里升级时会被清掉）。
"""

import json
import os
import socket
import sys
import threading
import time
import traceback

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
TRASH = "/sdcard/Download/.bookcase-trash"


def _mkdir(path):
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        print("mkdir %s failed: %s" % (path, e), file=sys.stderr)


def prepare(data_dir, roots):
    """把 scan.py 的模块级路径改到可写的数据目录，返回这个模块。"""
    data_dir = os.path.abspath(data_dir)
    _mkdir(data_dir)
    _mkdir(os.path.join(data_dir, "covers"))

    if APP_ROOT not in sys.path:
        sys.path.insert(0, APP_ROOT)

    import scan as lib

    lib.DB_PATH = os.path.join(data_dir, "library.db")
    lib.CONFIG_PATH = os.path.join(data_dir, "config.json")
    lib.COVER_DIR = os.path.join(data_dir, "covers")
    lib.TRASH_DIR = TRASH

    if not os.path.exists(lib.CONFIG_PATH):
        usable = [r for r in roots if r and os.path.isdir(r)]
        cfg = {"roots": usable or list(roots), "trashDir": TRASH}
        try:
            with open(lib.CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print("write config failed: %s" % e, file=sys.stderr)

    print("bookcase data dir: %s" % data_dir, flush=True)
    return lib


def find_static():
    """Chaquopy 会把带数据的模块解压到 HOME 下；找不到时兜底找一次 static/。"""
    direct = os.path.join(APP_ROOT, "static")
    if os.path.isdir(direct) and os.path.isfile(os.path.join(direct, "index.html")):
        return direct
    home = os.environ.get("HOME") or ""
    if not home or not os.path.isdir(home):
        return None
    base = home.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(home):
        if dirpath.count(os.sep) - base > 6:
            dirnames[:] = []
            continue
        if "index.html" in filenames and "app.js" in filenames and "style.css" in filenames:
            return dirpath
    return None


def autoscan(lib, server):
    """每次启动后台扫一遍：首次是建库，之后是增量（只比对大小/修改时间），新书自动出现。"""
    if lib.SCANNER.running:
        return
    if not server.SCAN_LOCK.acquire(blocking=False):
        return

    def work():
        try:
            time.sleep(1.5)  # 先让首屏和书架渲染出来
            lib.scan()
        except Exception:
            traceback.print_exc()
        finally:
            server.SCAN_LOCK.release()

    threading.Thread(target=work, daemon=True).start()
    print("bookcase: background scan started", flush=True)


def port_open(port):
    """8765 是否已经有人在监听（多半是上一次 Activity 留下的同一个服务）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.35)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def run(data_dir, port=8765, roots=None):
    """启动 HTTP 服务，阻塞到进程结束。"""
    roots = [str(r) for r in (roots or [])]
    port = int(port)
    try:
        if port_open(port):
            print("bookcase: 127.0.0.1:%d 已在监听，复用现有服务" % port, flush=True)
            return
        lib = prepare(data_dir, roots)
        lib.init_db()
        sys.argv = ["server.py", str(port)]
        import server
        server.PORT = port
        static = find_static()
        if static:
            server.STATIC = static
            print("bookcase static dir: %s" % static, flush=True)
        else:
            print("warning: static dir not found", file=sys.stderr)
        autoscan(lib, server)
        server.main()
    except OSError as e:
        # 端口刚好被抢：说明服务已经起来了，不算失败
        if port_open(port):
            print("bookcase: bind failed (%s) but server is up, reuse it" % e, flush=True)
            return
        traceback.print_exc()
        raise
    except Exception:
        traceback.print_exc()
        raise
