"""用无头 Chromium 渲染书库页面，检查布局、交互和控制台报错。"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8765"
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(SHOTS, exist_ok=True)


async def main():
    problems = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        ctx = await browser.new_context(
            viewport={"width": 412, "height": 900},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            locale="zh-CN",
        )
        page = await ctx.new_page()
        def on_console(m):
            if m.type != "error":
                return
            if "Permissions policy violation" in m.text:
                return  # 浏览器对 unload 的警告，与本书库无关
            problems.append(f"console.{m.type}: {m.text}")

        page.on("console", on_console)
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))

        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(900)
        cards = await page.locator(".card").count()
        print(f"书架卡片数: {cards}")

        # 封面是否真的加载
        covers = await page.evaluate("""() => {
            const imgs = [...document.querySelectorAll('.card img')];
            return {total: imgs.length, loaded: imgs.filter(i => i.naturalWidth > 0).length};
        }""")
        print(f"封面图: {covers}")
        cols = await page.evaluate("""() => {
            const g = document.querySelector('.grid');
            return getComputedStyle(g).gridTemplateColumns.split(' ').length;
        }""")
        print(f"网格列数: {cols}")
        await page.screenshot(path=os.path.join(SHOTS, "shelf.png"))

        # 搜索
        await page.fill("#search", "刘慈欣")
        await page.wait_for_timeout(800)
        found = await page.locator(".card").count()
        print(f"搜索「刘慈欣」: {found} 本")
        if found == 0:
            problems.append("搜索没有结果")
        await page.screenshot(path=os.path.join(SHOTS, "search.png"))
        await page.fill("#search", "")
        await page.wait_for_timeout(700)

        # 详情 + 改状态
        first = page.locator(".card").first
        book_id = await first.get_attribute("data-id")
        await first.click()
        await page.wait_for_timeout(700)
        visible = await page.locator("#detail").is_visible()
        print(f"详情面板: {visible}")
        if not visible:
            problems.append("详情没有打开")
        await page.screenshot(path=os.path.join(SHOTS, "detail.png"))
        await page.locator('[data-set-status="reading"]').click()
        await page.wait_for_timeout(600)
        status = await page.evaluate(
            "async id => (await (await fetch('/api/book/'+id)).json()).book.status", book_id)
        print(f"改成在读后的状态: {status}")
        if status != "reading":
            problems.append("状态没有保存")
        await page.locator('[data-set-status="unread"]').click()
        await page.wait_for_timeout(500)
        await page.locator(".sheet-bg").click(position={"x": 12, "y": 12})
        await page.wait_for_timeout(400)

        # 清理页
        await page.click('.navbtn[data-view="clean"]')
        await page.wait_for_timeout(1200)
        summary = await page.locator("#cleanSummary").inner_text()
        print("清理概览:", " / ".join(summary.split("\n")))
        await page.screenshot(path=os.path.join(SHOTS, "clean.png"), full_page=True)

        # 设置页
        await page.click('.navbtn[data-view="settings"]')
        await page.wait_for_timeout(600)
        await page.screenshot(path=os.path.join(SHOTS, "settings.png"), full_page=True)

        # 阅读 EPUB
        await page.click('.navbtn[data-view="shelf"]')
        await page.wait_for_timeout(700)
        epub_card = page.locator('.card:has(.fmt:text-is("epub"))').first
        if await epub_card.count():
            await epub_card.click()
            await page.wait_for_timeout(700)
            await page.locator('[data-act="read"]').click()
            await page.wait_for_timeout(4000)
            reader_visible = await page.locator("#reader").is_visible()
            iframes = await page.locator("#viewer iframe").count()
            pct = await page.locator("#readerPct").inner_text()
            print(f"阅读器: 显示={reader_visible} iframe={iframes} 进度={pct}")
            if not reader_visible or not iframes:
                problems.append("EPUB 阅读器没有渲染")
            await page.screenshot(path=os.path.join(SHOTS, "reader.png"))
            await page.locator("#nextPage").click()
            await page.wait_for_timeout(2500)
            pct2 = await page.locator("#readerPct").inner_text()
            print(f"翻页后进度: {pct2}")
            await page.locator("#readerBack").click()
            await page.wait_for_timeout(1600)
            saved = await page.evaluate(
                "async id => (await (await fetch('/api/book/'+id)).json()).book",
                await epub_card.get_attribute("data-id"))
            print(f"保存的进度: {saved['progress']}% cfi={'有' if saved['cfi'] else '无'} 状态={saved['status']}")
            if not saved["cfi"]:
                problems.append("阅读位置没有保存")
            await page.screenshot(path=os.path.join(SHOTS, "shelf_progress.png"))
        else:
            problems.append("没有找到 epub 卡片")

        print("\n问题清单:" if problems else "\n没有控制台错误")
        for x in problems:
            print(" -", x)
        await browser.close()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
