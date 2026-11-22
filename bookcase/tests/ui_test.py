# -*- coding: utf-8 -*-
"""用本机 Edge/Chrome 跑一遍书库界面：登录 → 上传 → 书架 → 详情。

需要：pip install playwright（不用 playwright install，直接用系统 Edge）。
用法：python tests/ui_test.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_api import build_epub  # noqa: E402

PORT = 8898
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "ui-test-token"
SHOTS = os.path.join(ROOT, "..", ".shots")


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
    tmp = tempfile.mkdtemp(prefix="bookcase-ui-")
    books = os.path.join(tmp, "books")
    data = os.path.join(tmp, "data")
    os.makedirs(books)
    os.makedirs(data)
    epub = os.path.join(tmp, "云上的测试书.epub")
    build_epub(epub, title="云上的测试书", author="私有云")

    cfg = {
        "roots": [books],
        "uploadDir": os.path.join(books, "上传"),
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
    os.makedirs(SHOTS, exist_ok=True)
    errors = []
    try:
        if not wait_health():
            raise SystemExit("服务没起来")
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844},
                                    device_scale_factor=2)
            page.on("console", lambda m: errors.append("console: " + m.text)
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))

            page.goto(BASE, wait_until="networkidle")
            assert "访问口令" in page.content(), "没进登录页"
            page.screenshot(path=os.path.join(SHOTS, "01-login.png"))

            page.fill("#token", TOKEN)
            page.click("#loginBtn")
            page.wait_for_url(BASE + "/", timeout=15000)
            page.wait_for_selector("#grid", state="attached", timeout=15000)
            page.wait_for_timeout(1200)
            page.screenshot(path=os.path.join(SHOTS, "02-shelf-empty.png"))

            page.click("#uploadBtn")
            page.wait_for_selector("#uploadSheet:not([hidden])", timeout=5000)
            page.screenshot(path=os.path.join(SHOTS, "03-upload-empty.png"))

            page.set_input_files("#pickFiles", epub)
            page.wait_for_selector(".up-state.done, .up-state.skip", timeout=40000)
            page.wait_for_timeout(600)
            page.screenshot(path=os.path.join(SHOTS, "04-upload-done.png"))

            page.click("#upDone")
            page.wait_for_selector("#uploadSheet", state="hidden", timeout=5000)
            page.wait_for_selector(".card", timeout=15000)
            page.wait_for_timeout(800)
            page.screenshot(path=os.path.join(SHOTS, "05-shelf-one.png"))

            page.click(".card")
            page.wait_for_selector("#detail:not([hidden])", timeout=5000)
            page.wait_for_timeout(400)
            page.screenshot(path=os.path.join(SHOTS, "06-detail.png"))
            href = page.get_attribute("a[href*='?dl=1']", "href")
            assert href and "?dl=1" in href, href

            page.locator("#detail .sheet-bg").click(position={"x": 20, "y": 20})
            page.wait_for_timeout(300)

            # 书单 + 打包下载入口
            page.click(".navbtn[data-view='lists']")
            page.click("#newListBtn")
            page.fill("#listName", "打包测试书单")
            page.fill("#listNote", "验证书单下载入口")
            page.click("#listSaveBtn")
            page.wait_for_selector(".lcard", timeout=10000)
            page.click(".lcard")
            page.wait_for_timeout(500)
            page.click("#lbAdd")
            page.wait_for_selector("#picker:not([hidden])", timeout=5000)
            page.click(".pick-row")
            page.click("#pickerSave")
            page.wait_for_selector("#lbDownload:not([hidden])", timeout=10000)
            dl_href = page.get_attribute("#lbDownload", "href")
            assert dl_href and dl_href.endswith("/download"), dl_href
            page.screenshot(path=os.path.join(SHOTS, "08-list-download.png"))

            with page.expect_download(timeout=30000) as dl_info:
                page.click("#lbDownload")
            download = dl_info.value
            assert download.suggested_filename.endswith(".zip"), download.suggested_filename
            with zipfile.ZipFile(download.path()) as z:
                names = z.namelist()
            assert any(n.endswith(".epub") for n in names), names

            page.click(".navbtn[data-view='settings']")
            page.wait_for_timeout(700)
            page.screenshot(path=os.path.join(SHOTS, "07-settings.png"))
            assert page.is_visible("#backupBtn")
            assert "未设置" not in page.inner_text("#cloudInfo") or "已开启" in page.inner_text("#cloudInfo")

            browser.close()

        if errors:
            print("浏览器报错：")
            for e in errors:
                print("  " + e)
            raise SystemExit(1)
        print("UI TEST PASSED")
        print("截图目录：" + os.path.abspath(SHOTS))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()