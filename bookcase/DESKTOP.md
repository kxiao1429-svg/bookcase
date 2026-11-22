# 在电脑上运行 / 继续开发

这本书库是 Python 标准库 + SQLite 的本地服务，手机和电脑是同一套代码。

## 跑起来

```bash
cd bookcase
python3 -m pip install -r requirements.txt   # 解析 PDF 需要 PyMuPDF
cp config.example.json config.json           # 改成你自己的书目录
python3 server.py                            # 默认 http://127.0.0.1:8765
```

打开浏览器访问 <http://127.0.0.1:8765>，点右上角刷新按钮开始扫描。

也可以 `./start.sh` 后台启动（macOS 没有 `setsid` 时会自动前台/普通后台运行），`./stop.sh` 停止。

`config.json` 里：

- `roots`：要扫描的电子书目录（只读，不会移动文件）
- `trashDir`：清理时「移入回收站」的目标目录；不可写时会退到项目内 `trash/`
- `uploadDir`：网页上传的落点，默认是第一个书库目录下的「书库上传」
- `host` / `port`：默认 `127.0.0.1:8765`；手机访问就改 `0.0.0.0`，并设 `token`
- `token`：访问口令，留空不校验（只在本机用时可以留空）
- `maxUploadMB`：单文件上传上限

没写 `config.json` 时，程序会找 `~/Books`，没有就用当前目录。

## 和私有云版的区别

- 同一套代码。电脑上默认只监听 `127.0.0.1`、不设口令；NAS 上改 `host` 并设 `token`。
- 上传、下载、口令这些功能电脑上一样能用，U 盘/移动硬盘里的书也能扫描。
- 数据位置可用 `BOOKCASE_DATA` 改；Docker 部署时对应 `/data` 和 `/books` 两个卷。
- 手机端快捷方式 / App 图标是手机上的事，和项目代码无关。
- 其余功能一致：扫描、封面、搜索筛选、书单、标签、进度、清理。

## 数据

- `library.db`：书、标签、书单、阅读进度
- `covers/`：从 epub/pdf 提取的封面缓存
- 两个都可以删，删了只是重新扫描/重新生成

手机上的库数据在 `bookcase/library.db`，但里面记的是手机路径（`/sdcard/...`），
搬到电脑或 NAS 建议重新扫描，不要直接套用。

## 开发

```bash
python3 scan.py      # 命令行扫一遍，看元数据提取结果
python3 verify.py    # 无头浏览器界面回归（需要 pip install playwright && playwright install chromium）
```

前端没有构建步骤：`static/` 下的 HTML/CSS/JS 直接改，刷新页面即可。
