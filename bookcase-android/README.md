# 书库 APK（bookcase-android）

把原来跑在 DSH / 终端里的「书库」Python 服务，打包成一个可以独立安装的 Android 应用：
点图标即用，不需要 DSH、不需要 Termux、不需要联网。

## 原理

```
┌────────────────────────── App 进程 ──────────────────────────┐
│  MainActivity (Java)                                          │
│    ├─ 申请「所有文件访问」权限                                  │
│    ├─ Python.start()  ← Chaquopy 提供 CPython 3.12 运行时      │
│    ├─ bootstrap.run(dataDir, 8765, roots)                     │
│    │      └─ scan.py / server.py 起 ThreadingHTTPServer        │
│    └─ WebView → http://127.0.0.1:8765                          │
└───────────────────────────────────────────────────────────────┘
```

- **Chaquopy**（`com.chaquo.python` 17.2.0，开源免费）把 CPython 3.12 编进 APK，
  纯标准库（sqlite3 / zipfile / http.server），没有 pip 依赖。
- Python 代码和 `static/` 前端资源都在 `app/src/main/python/`，Chaquopy 打包成
  `assets/chaquopy/app.imy`，首次启动解压到应用目录。
- 数据库 / 封面 / 配置不写代码目录（升级会被清），而是：
  - 有「所有文件访问」时：`/sdcard/Documents/Bookcase/`
  - 没有时：应用私有目录 `files/bookcase-data/`
- 服务只监听 `127.0.0.1`，对外不开放。
- **分享**：前端调 `window.BookcaseAndroid.share('book'|'list', id)` → Java 去
  `/api/share/book/<id>` 或 `/api/share/list/<id>` 拿真实路径（书单会先打成 zip）→
  `BookFileProvider` 把 `file://` 包成 `content://` → 系统分享面板。
  Android 7+ 不允许直接分享 `file://`，所以必须走这个 ContentProvider（`android:exported="false"`，
  仅通过 `FLAG_GRANT_READ_URI_PERMISSION` 授权给接收方）。
- **PDF 阅读**：前端用 pdf.js（`static/vendor/pdf.*`）在 WebView 里渲染，文件通过 `/api/file/<id>`
  的 Range 请求分片读取，不会把整本 PDF 读进内存。
- **单实例**：Activity 重建或退出后再打开时，先探测 `127.0.0.1:8765`，已经在跑就直接复用，
  避免 `Address already in use`（Python 侧 `port_open()` 也做了同样的兜底）。
- **返回手势**：`onBackPressed()` 先调网页 `window.__bcBack()`，由前端决定关哪一层；
  manifest 里的 `android:enableOnBackInvokedCallback="false"` 让 Android 13+ 的预测性返回也走这条路径。
  坑：`evaluateJavascript` 回传的是 JSON，字符串带引号（`"true"`），必须 `contains("true")`，不能 `equals("true")`。

## 目录

| 路径 | 说明 |
| --- | --- |
| `app/src/main/java/com/bookcase/app/MainActivity.java` | WebView 壳、权限、启停 Python、分享桥 |
| `app/src/main/java/com/bookcase/app/BookFileProvider.java` | 分享用的 ContentProvider |
| `app/src/main/python/bootstrap.py` | 把 scan.py 的 DB/配置路径改到可写目录，再起服务 |
| `app/src/main/python/{scan,server,lists}.py` | 书库后端（来自手机版 1.x） |
| `app/src/main/python/static/` | 前端单页 + epub.js / pdf.js / JSZip（离线） |
| `app/src/main/res/mipmap-*/ic_launcher.png` | 图标 |

> 发布用签名密钥（`app/keystore/*.jks`）和 `keystore.properties` **不入库**，见下方「签名」。

## 构建

需要：JDK 17、Android SDK（platform 34 + build-tools 34.0.0）、Gradle 8.9。

```bash
cd bookcase-android
echo "sdk.dir=/path/to/Android/Sdk" > local.properties   # Windows 写法：sdk.dir=C\:\\Android\\Sdk
./gradlew assembleRelease
# 产物：app/build/outputs/apk/release/app-release.apk
```

开发时用到的工具链版本：

- JDK：Temurin 17（17.0.21 验证过）
- Android SDK：platforms;android-34、build-tools;34.0.0
- Gradle：8.9（仓库自带 wrapper）
- AGP：8.7.3，Chaquopy：17.2.0，Python：3.12，abi：arm64-v8a

## 签名

仓库里**不含**签名密钥。要用自己的签名打 release 包，在 `bookcase-android/` 下新建
`keystore.properties`（已被 `.gitignore` 排除）：

```properties
storeFile=app/keystore/bookcase.jks
storePassword=你的口令
keyAlias=bookcase
keyPassword=你的口令
```

没有这个文件时，`assembleRelease` 仍能编译，只是产物未签名、装不上。
**覆盖升级必须沿用同一个 jks**，否则会因签名不一致而安装失败。

## 改功能

- 改界面/交互：`app/src/main/python/static/`（和手机版完全一致）。
- 改后端：`app/src/main/python/*.py`。
- 书库数据、扫描目录逻辑：`scan.py`；HTTP 路由：`server.py`。
- 改完重新 `assembleRelease`；只要签名（`app/keystore/bookcase.jks`）不变，可以直接覆盖安装，
  `/sdcard/Documents/Bookcase/` 里的数据会保留。

## 已知限制

- PDF 没有封面/页数：PyMuPDF 在 Android 上没有轮子，`scan.py` 会自动跳过（EPUB 不受影响）。
- 只打了 `arm64-v8a`，32 位老机型装不上；要支持模拟器在 `abiFilters` 里加 `x86_64`。
- 应用被杀掉后服务就停，重开 App 会重新起；数据在 SQLite 里，不会重扫整库。
