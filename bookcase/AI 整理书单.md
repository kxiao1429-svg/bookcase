# 让 DSH 帮你整理书库

## 先说结论

**DSH 不能「装到 NAS 上」。** 它是跑在你这台 Windows 电脑上的 agent；T2 是 2GB 内存的 ARM 盒子，官方又不支持 Docker，跑不动它。而且你的用法本来就是「按需开机」，不需要它常驻。

可行的是反过来：**把 NAS 挂给 DSH 用**。

```
T2（Samba 共享）  ←→  Windows 电脑（书库服务 + DSH）
```

- 书和归档文件放在 T2 上；
- 书库服务在电脑上按需启动；
- DSH 通过书库的 HTTP API 读目录、改标签/书单、归档重复书，也能直接看 `Z:` 里的文件。

这样我只在你**在家、NAS 在线、书库启动**的时候能操作；NAS 离线时我会明确说读不到。

---

## 一次性准备

### 1. 打开 T2 的 Samba

手机「数据守护者」→【更多应用】→【Samba】→ 打开。默认账号是设备 UID，默认密码 `123456`。

### 2. 在 Windows 上映射 Z:

```powershell
cd D:\bookcase
powershell -ExecutionPolicy Bypass -File .\deploy\mount-nas.ps1 -Server 192.168.1.x -User 设备UID
```

密码不填会提示输入。成功后资源管理器里会出现 `Z:`。

### 3. 配置 `config.json`

关键是 `trashDir` 也要指向 NAS，否则归档文件会落在电脑本地：

```json
{
  "roots": ["Z:\\电子书"],
  "uploadDir": "Z:\\电子书\\书库上传",
  "trashDir": "Z:\\电子书\\.bookcase-trash",
  "host": "0.0.0.0",
  "port": 8765,
  "token": "你的口令",
  "maxUploadMB": 4096,
  "autoScanMinutes": 10
}
```

### 4. 启动并验证

双击 `start-windows.bat`，然后：

```powershell
python tools\bc.py stats
```

看到「共 N 本」就说明 DSH 这条路通了。

---

## 我能帮你做什么

### 1. 归档重复书

我先只读目录，给出「每组保留哪本、归档哪几本」的方案；你确认后我再执行。

```powershell
python tools\bc.py dupes                  # 看重复组
python tools\bc.py archive-dupes          # 预演，不改任何东西
python tools\bc.py archive-dupes --apply  # 执行：每组保留文件名最干净的一本
```

归档不是删除：文件会移到

```
Z:\电子书\.bookcase-trash\重复归档\20261118\
```

按日期分目录，事后能直接搬回来。

### 2. 分类、标签、书单

我读取的是完整目录快照（书名、作者、格式、大小、标签、书单、进度、重复组）：

```powershell
python tools\bc.py catalog -o catalog.json
python tools\bc.py digest
```

然后可以：

```powershell
python tools\bc.py list-create "2026 冬 · 思维环"
python tools\bc.py list-add 3 12 15 18 21
python tools\bc.py tag 12 社会学 待读
```

实际使用时你不用记命令，直接跟我说：

- 「先把所有书按主题粗分，给我方案，别改数据。」
- 「把『社会学』相关的放进一个新书单，说明写一句为什么。」
- 「重复书里保留文件名干净的那本，其余归档。」

### 3. 按「思维环」给图书目录

这个需要你先告诉我：**你的「思维环」是哪几个环节？**

如果你没有固定说法，我可以先按一个通用的五段来试：

| 环节 | 作用 | 书单 |
| --- | --- | --- |
| 看见 | 现象、案例、问题定义 | 00 看见问题 |
| 解释 | 机制、模型、原理 | 01 解释机制 |
| 对照 | 历史、跨学科、反方观点 | 02 换视角 |
| 行动 | 方法、实验、实践 | 03 动手 |
| 复盘 | 输出、迁移、反思 | 04 复盘输出 |

你确认环节后，我会：

1. 先出一份「现有书 → 环节」的映射表，标出哪些书不适合、哪些环节是空的；
2. 给空环节列「待补书目」建议（不瞎编，会说明推荐理由）；
3. 你点头后再批量建书单、打标签，不动原文件。

如果「思维环」指的是双环学习、PDCA、第一性原理之类的具体框架，直接报名字，我按那个来。

### 4. 其他可以顺手做的

- 找残缺文件、脏文件名、PDF 报告类杂项；
- 按作者/主题/年代聚类，给阅读顺序；
- 把书单导出成 ZIP（书 + `书单.txt`）；
- 定期生成「待读 / 在读 / 读完」的节奏建议。

---

## 命令速查

| 目的 | 命令 |
| --- | --- |
| 看统计 | `python tools\bc.py stats` |
| 导出目录快照 | `python tools\bc.py catalog -o catalog.json` |
| 精简清单 | `python tools\bc.py digest` |
| 重复组 | `python tools\bc.py dupes` |
| 归档重复（预演） | `python tools\bc.py archive-dupes` |
| 归档重复（执行） | `python tools\bc.py archive-dupes --apply` |
| 书单列表 | `python tools\bc.py lists` |
| 新建书单 | `python tools\bc.py list-create "名字" --note "说明"` |
| 加书进书单 | `python tools\bc.py list-add 书单ID 书ID 书ID` |
| 打标签 | `python tools\bc.py tag 书ID --tags 标签1 标签2` |
| 单本归档 | `python tools\bc.py archive 书ID` |
| 搜索 | `python tools\bc.py search 关键词` |

所有命令默认读 `config.json` 的端口和口令，不用每次输。

---

## 安全与边界

- **归档 = 移动，不是删除**。位置在 `trashDir\重复归档\日期\`，可以手动搬回。
- `archive-dupes` 默认只预演，必须加 `--apply` 才动文件；我操作前也会先把方案给你看。
- 我只通过本机的书库 API 和 `Z:` 访问数据，不会把书目发到外部。
- NAS 离线、电脑没开、书库没启动时，我读不到书库，会直接告诉你，不会假装整理过。
- 电脑重装前，记得在设置页「导出整个书架（ZIP）」，`manifest.json` 里带着书单和进度。

---

## 最短的开始方式

回家后按顺序：

1. 开 T2、开 Samba；
2. 跑一次 `mount-nas.ps1`；
3. 启动 `start-windows.bat`；
4. 跟我说：**「读一下书库，先只报告，不要改。」**

我会先给你一份现状报告（多少本、多少重复、书单分布、明显缺口），再谈怎么整理。