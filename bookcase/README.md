# 书库（Bookcase）2.0 · 私有云版

电子书库：扫一遍目录里的 epub / pdf / mobi / azw3，生成封面墙，支持搜索筛选、标签、书单、阅读进度和清理建议。

2.0 起可以挂到自己的 NAS / 私有云上：

- **手机上传备份**：浏览器里选文件或整个文件夹，把手机里的书传到云上，云端已有的自动跳过；
- **从云下载**：详情页一键下载原文件，EPUB 也可以直接在书库里读；
- **访问口令**：对局域网开放时用口令保护，登录一次记 30 天；
- **Docker 部署**：一条 `docker compose up -d --build` 跑起来，数据库和书分卷持久化。

## 本机跑

```bash
cd bookcase
python3 -m pip install -r requirements.txt   # 解析 PDF 需要 PyMuPDF
cp config.example.json config.json           # 改成你自己的书目录
python3 server.py                            # 默认 http://127.0.0.1:8765
```

也可以 `./start.sh` 后台启动，`./stop.sh` 停止。

`config.json`：

```json
{
  "roots": ["/Users/你/Books"],
  "uploadDir": "/Users/你/Books/书库上传",
  "host": "127.0.0.1",
  "port": 8765,
  "token": "",
  "maxUploadMB": 4096,
  "autoScanMinutes": 0
}
```

- `roots`：要扫描的电子书目录（只读，不会移动文件）；上传目录会自动加进扫描范围。
- `uploadDir`：网页上传的书落在这里，默认是第一个书库目录下的「书库上传」。
- `host` / `port`：默认只监听本机；手机要访问就改 `0.0.0.0`，并设置 `token`。
- `token`：访问口令；留空表示不校验（只建议在本机用）。
- `maxUploadMB`：单文件上限。
- `autoScanMinutes`：后台自动扫描间隔，0 关闭；配合 FolderSync / Syncthing 把书同步进目录时很有用。

环境变量优先级更高，Docker 部署用得上：`BOOKCASE_HOST`、`BOOKCASE_PORT`、`BOOKCASE_TOKEN`、`BOOKCASE_ROOTS`（用系统路径分隔符）、`BOOKCASE_UPLOAD_DIR`、`BOOKCASE_DATA`、`BOOKCASE_CONFIG`、`BOOKCASE_MAX_UPLOAD_MB`、`BOOKCASE_AUTO_SCAN_MIN`。

## 部署到私有云 / NAS

完整步骤（联想个人云三条路线：Docker、SSH、Samba）见 **[部署到联想个人云.md](部署到联想个人云.md)**，T2 用户另有 **[T2 回家部署清单.md](<T2 回家部署清单.md>)**。

> **联想个人云 T2（非 Pro）注意**：官方明确回复 T2 因 ARM 架构不支持 Docker（T2 Pro 才是 Intel x86）。T2 请直接看路线 C（电脑 + Samba，最稳）或路线 B（SSH + Python），不要套用 T2 Pro 的 Docker 教程。

最短的 Docker 路径（T2 Pro / x86 机型）：

```bash
cp .env.example .env      # 改口令、书目录、数据目录
docker compose up -d --build
```

然后手机打开 `http://NAS的IP:8765`，输口令，右上角 ＋ 上传。

## 功能

- **书架**：三列封面墙，按格式 / 状态 / 标签筛选，按添加时间、书名、作者、大小排序。
- **上传**：右上角 ＋，支持多选文件和整个文件夹；先按「同名同大小」预检，服务端再按 SHA1 去重，不会存两份。
- **整批备份**：`deploy/upload-folder.ps1` 一条命令把电脑上的整个书库文件夹传上去，自动跳过已有，断了重跑即可。
- **下载**：详情页「下载到本机」；EPUB 可在应用内阅读并记住位置。
- **书单打包**：书单页「下载书单（N）」把整张书单的书打成一个 ZIP，附 `书单.txt` 清单。
- **整库导出**：设置页「导出整个书架（ZIP）」，书 + 书单/标签/进度 `manifest.json` 一次带走。
- **标签**：详情面板里自由增删，标签可用于筛选。
- **书单**：按阶段/主题建独立书架，一本书可进多个书单。
- **清理**：重复、打不开、PDF 报告类杂项、`(Z-Library)` 脏文件名，可逐条或批量处理；重复书可以「归档」到 `回收站/重复归档/日期/`，不直接删除。
- **扫描**：设置里增删扫描目录，随时重扫；已有书增量更新，单个坏文件不会拖垮整次扫描。

## 让 AI 整理书单

书库提供完整的目录快照接口（`/api/catalog`）和命令行工具 `tools/bc.py`，DSH 可以读全量书目、归档重复书、批量建书单/打标签。前提是 NAS 已挂到电脑、书库已启动。

- 用法与示例：**[AI 整理书单.md](<AI 整理书单.md>)**
- 核心命令：`python tools/bc.py stats|dupes|archive-dupes|list-create|list-add|tag`
- 归档不是删除：文件进 `trashDir/重复归档/日期/`，可手动搬回。

## 数据在哪

| 内容 | 位置 |
| --- | --- |
| 数据库 | `<数据目录>/library.db` |
| 封面缓存 | `<数据目录>/covers/` |
| 原书 | 你的 `roots` 目录 |
| 回收站 | `trashDir`，默认 `/sdcard/Download/.bookcase-trash` |
| 日志 | `/tmp/bookcase.log`（start.sh） |

**原书一个字节都不会被改**。重命名只改文件名；「移入回收站」只是把文件搬走，可以手动搬回来。

## 技术

- Python 3 标准库 HTTP 服务 + SQLite，无前端框架。
- EPUB 元数据/封面：`zipfile` + OPF 解析；PDF：PyMuPDF（可选，装不上不影响 EPUB）。
- 前端单页应用，EPUB 渲染用 epub.js + JSZip（`static/vendor/`，可离线）。
- 默认只监听 `127.0.0.1`；对外时设 `token`，支持 cookie / Bearer / `?token=` 三种方式。

## 测试

```bash
python tests/test_api.py    # 接口回归：登录、上传、去重、下载、鉴权
python tests/ui_test.py     # 界面回归：登录 → 上传 → 书架 → 详情（需要 playwright + 本机 Edge）
```

`ui_test.py` 只装 Python 包即可，用系统 Edge：`python -m pip install playwright`。