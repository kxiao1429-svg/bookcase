package com.bookcase.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.ConsoleMessage;
import android.webkit.DownloadListener;
import android.webkit.JsPromptResult;
import android.webkit.JsResult;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;

import org.json.JSONObject;

/**
 * 书库 APK 的壳：申请存储权限 -> 进程内起 Python 服务（127.0.0.1:8765）-> WebView 打开它。
 * 前端和 Python 代码都打进 APK，不联网、不依赖 DSH / Termux。
 */
public class MainActivity extends Activity {

    private static final String TAG = "Bookcase";
    private static final int PORT = 8765;
    private static final String BASE_URL = "http://127.0.0.1:" + PORT + "/";
    private static final int REQ_STORAGE = 1001;
    private static final int REQ_FILE_CHOOSER = 1003;

    private final Handler ui = new Handler(Looper.getMainLooper());

    /** WebView 里 <input type="file"> 的回调；换封面用 */
    private ValueCallback<Uri[]> filePathCallback;

    private FrameLayout root;
    private WebView webView;
    private LinearLayout overlay;
    private ProgressBar spinner;
    private TextView status;
    private Button retry;

    private volatile boolean starting = false;
    private volatile boolean ready = false;

    // ------------------------------------------------------------------ 生命周期

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();

        if (hasStorageAccess()) {
            startServer();
        } else {
            askForStorage();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
        if (!ready && !starting && hasStorageAccess()) startServer();
    }

    @Override
    protected void onPause() {
        if (webView != null) webView.onPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            root.removeView(webView);
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    /**
     * 返回键/侧滑先交给网页处理：网页的 __bcBack() 会从最上层开始关
     * （删除确认 → 新书 → 选书 → 书单编辑 → PDF/EPUB 阅读器 → 详情 → 其它标签页 → 书单筛选），
     * 只有它说“没东西可关”时才退出。
     */
    @Override
    public void onBackPressed() {
        final WebView wv = webView;
        if (wv == null) {
            super.onBackPressed();
            return;
        }
        wv.evaluateJavascript(
                "(window.__bcBack && window.__bcBack()) ? 'true' : 'false'",
                value -> {
                    // evaluateJavascript 回传的是 JSON：字符串带引号（"true"），布尔才是 true，
                    // 所以只能 contains，不能 equals("true")
                    if (value != null && value.contains("true")) return;
                    if (wv.canGoBack()) wv.goBack();
                    else finish();
                });
    }

    // ------------------------------------------------------------------ 界面

    private void buildUi() {
        root = new FrameLayout(this);

        webView = new WebView(this);
        configureWebView(webView);
        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        overlay = new LinearLayout(this);
        overlay.setOrientation(LinearLayout.VERTICAL);
        overlay.setGravity(Gravity.CENTER);
        overlay.setBackgroundColor(0xFFF7F5F0);

        spinner = new ProgressBar(this);
        overlay.addView(spinner, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        status = new TextView(this);
        status.setText("正在启动书库…");
        status.setTextSize(16f);
        status.setGravity(Gravity.CENTER);
        status.setPadding(dp(24), dp(16), dp(24), dp(8));
        status.setTextColor(0xFF333333);
        overlay.addView(status);

        retry = new Button(this);
        retry.setText("重试");
        retry.setVisibility(View.GONE);
        retry.setOnClickListener(v -> {
            retry.setVisibility(View.GONE);
            spinner.setVisibility(View.VISIBLE);
            status.setText("正在重新启动…");
            starting = false;
            if (hasStorageAccess()) startServer();
            else askForStorage();
        });
        overlay.addView(retry);

        root.addView(overlay, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        setContentView(root);
    }

    private int dp(int value) {
        return Math.round(getResources().getDisplayMetrics().density * value);
    }

    private void configureWebView(WebView view) {
        WebSettings s = view.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setTextZoom(100);

        view.addJavascriptInterface(new Object() {
            @android.webkit.JavascriptInterface
            public void flash(String message) {
                ui.post(() -> Toast.makeText(MainActivity.this, message, Toast.LENGTH_SHORT).show());
            }

            /** 前端调用：share('book', id) 或 share('list', id)，由 App 走系统分享面板。 */
            @android.webkit.JavascriptInterface
            public void share(String kind, String id) {
                ui.post(() -> shareAsync(kind, id));
            }
        }, "BookcaseAndroid");

        view.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest request) {
                return handleUrl(request.getUrl().toString());
            }

            @Override
            @SuppressWarnings("deprecation")
            public boolean shouldOverrideUrlLoading(WebView v, String url) {
                return handleUrl(url);
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest request, android.webkit.WebResourceError error) {
                if (request.isForMainFrame()) {
                    Log.e(TAG, "webview error: " + error.getDescription());
                }
            }
        });

        view.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(ConsoleMessage msg) {
                Log.i(TAG, "js: " + msg.message());
                return true;
            }

            /** 详情页「换封面」用的是 <input type="file" accept="image/*">，WebView 默认点不开选择器。 */
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                }
                filePathCallback = callback;
                Intent intent;
                try {
                    intent = params.createIntent();
                } catch (Exception e) {
                    intent = new Intent(Intent.ACTION_GET_CONTENT);
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    intent.setType("image/*");
                }
                try {
                    startActivityForResult(intent, REQ_FILE_CHOOSER);
                    return true;
                } catch (Exception e) {
                    Log.e(TAG, "file chooser failed", e);
                    filePathCallback = null;
                    return false;
                }
            }

            @Override
            public boolean onJsAlert(WebView v, String url, String message, final JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setPositiveButton(android.R.string.ok, (d, w) -> result.confirm())
                        .setCancelable(false)
                        .show();
                return true;
            }

            @Override
            public boolean onJsConfirm(WebView v, String url, String message, final JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setPositiveButton(android.R.string.ok, (d, w) -> result.confirm())
                        .setNegativeButton(android.R.string.cancel, (d, w) -> result.cancel())
                        .setOnCancelListener(d -> result.cancel())
                        .show();
                return true;
            }

            @Override
            public boolean onJsPrompt(WebView v, String url, String message, String defaultValue,
                                      final JsPromptResult result) {
                final EditText input = new EditText(MainActivity.this);
                input.setText(defaultValue == null ? "" : defaultValue);
                input.setSelection(input.getText().length());
                new AlertDialog.Builder(MainActivity.this)
                        .setMessage(message)
                        .setView(input)
                        .setPositiveButton(android.R.string.ok, (d, w) -> result.confirm(input.getText().toString()))
                        .setNegativeButton(android.R.string.cancel, (d, w) -> result.cancel())
                        .setOnCancelListener(d -> result.cancel())
                        .show();
                return true;
            }
        });

        // PDF 在 WebView 里显示不了，交给系统下载器，落到 Download 目录再用阅读器打开
        view.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String contentDisposition,
                                        String mimeType, long contentLength) {
                try {
                    DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
                    request.setMimeType(mimeType);
                    request.addRequestHeader("User-Agent", userAgent);
                    request.setNotificationVisibility(
                            DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                    request.setTitle("书库下载");
                    DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
                    if (dm != null) dm.enqueue(request);
                    Toast.makeText(MainActivity.this, "已开始下载，完成后在 Download 目录", Toast.LENGTH_LONG).show();
                } catch (Exception e) {
                    Log.e(TAG, "download failed", e);
                    Toast.makeText(MainActivity.this, "下载失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
                }
            }
        });
    }

    /** true 表示已经在外部 App 打开，不再交给 WebView。 */
    private boolean handleUrl(String url) {
        if (url == null) return false;
        if (url.startsWith("http://127.0.0.1:" + PORT) || url.startsWith("http://localhost:" + PORT)) {
            return false;
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (Exception e) {
            Log.w(TAG, "cannot open " + url, e);
        }
        return true;
    }

    // ------------------------------------------------------------------ 分享

    private void shareAsync(String kind, String id) {
        final boolean isList = "list".equals(kind);
        Toast.makeText(this, isList ? "正在打包书单…" : "正在准备分享…", Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            try {
                String url = BASE_URL + (isList ? "api/share/list/" : "api/share/book/") + id;
                JSONObject res = new JSONObject(httpGet(url, 600000));
                if (!res.optBoolean("ok", false)) {
                    throw new RuntimeException(res.optString("error", "分享失败"));
                }
                File file = new File(res.getString("path"));
                if (!file.isFile()) {
                    throw new RuntimeException("文件不在了：" + file.getName());
                }
                String mime = res.optString("mime", "application/octet-stream");
                if (mime == null || mime.isEmpty()) mime = "application/octet-stream";
                final String fMime = mime;
                ui.post(() -> startShare(file, fMime));
            } catch (Throwable t) {
                Log.e(TAG, "share failed", t);
                ui.post(() -> Toast.makeText(this, "分享失败：" + t.getMessage(), Toast.LENGTH_LONG).show());
            }
        }, "bookcase-share").start();
    }

    private void startShare(File file, String mime) {
        Uri uri = BookFileProvider.uriFor(file);
        Intent send = new Intent(Intent.ACTION_SEND);
        send.setType(mime);
        send.putExtra(Intent.EXTRA_STREAM, uri);
        send.putExtra(Intent.EXTRA_SUBJECT, file.getName());
        send.putExtra(Intent.EXTRA_TITLE, file.getName());
        send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            startActivity(Intent.createChooser(send, "分享 " + file.getName()));
        } catch (Exception e) {
            Log.e(TAG, "no share target", e);
            Toast.makeText(this, "没有可用的分享方式", Toast.LENGTH_LONG).show();
        }
    }

    private String httpGet(String url, int readTimeoutMs) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        try {
            conn.setConnectTimeout(3000);
            conn.setReadTimeout(readTimeoutMs);
            int code = conn.getResponseCode();
            InputStream in = (code >= 200 && code < 300) ? conn.getInputStream() : conn.getErrorStream();
            StringBuilder sb = new StringBuilder();
            if (in != null) {
                BufferedReader br = new BufferedReader(new InputStreamReader(in, "UTF-8"));
                try {
                    char[] buf = new char[8192];
                    int n;
                    while ((n = br.read(buf)) > 0) sb.append(buf, 0, n);
                } finally {
                    br.close();
                }
            }
            String body = sb.toString();
            if (code < 200 || code >= 300) {
                String msg = body;
                try {
                    msg = new JSONObject(body).optString("error", body);
                } catch (Exception ignored) {
                    // 用原始 body
                }
                throw new RuntimeException(msg.isEmpty() ? ("HTTP " + code) : msg);
            }
            return body;
        } finally {
            conn.disconnect();
        }
    }

    // ------------------------------------------------------------------ 权限

    private boolean hasStorageAccess() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            return Environment.isExternalStorageManager();
        }
        return checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void askForStorage() {
        overlay.setVisibility(View.VISIBLE);
        spinner.setVisibility(View.GONE);
        retry.setVisibility(View.GONE);
        status.setText("书库需要读取手机存储里的电子书。\n请授予「所有文件访问」权限。");

        new AlertDialog.Builder(this)
                .setTitle("需要存储权限")
                .setMessage("书库要扫描 Download / Documents 里的 epub、pdf，需要「所有文件访问」权限。\n\n" +
                        "在接下来的设置页里找到「书库」，打开开关后返回即可。")
                .setPositiveButton("去授权", (d, w) -> openStorageSettings())
                .setNegativeButton("退出", (d, w) -> finish())
                .setCancelable(false)
                .show();
    }

    private void openStorageSettings() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                Intent intent = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION);
                intent.setData(Uri.parse("package:" + getPackageName()));
                startActivity(intent);
            } else {
                requestPermissions(new String[]{
                        Manifest.permission.READ_EXTERNAL_STORAGE,
                        Manifest.permission.WRITE_EXTERNAL_STORAGE
                }, REQ_STORAGE);
            }
        } catch (Exception e) {
            try {
                startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
            } catch (Exception e2) {
                Log.e(TAG, "cannot open storage settings", e2);
                Toast.makeText(this, "请在系统设置里手动授予存储权限", Toast.LENGTH_LONG).show();
            }
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_STORAGE) {
            if (hasStorageAccess()) startServer();
            else askForStorage();
        }
    }

    /** 换封面：把系统选择器选中的图片交回网页的 <input type="file">。 */
    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_FILE_CHOOSER) {
            if (filePathCallback == null) return;
            Uri[] results = null;
            if (resultCode == RESULT_OK && data != null) {
                if (data.getClipData() != null) {
                    int count = data.getClipData().getItemCount();
                    results = new Uri[count];
                    for (int i = 0; i < count; i++) {
                        results[i] = data.getClipData().getItemAt(i).getUri();
                    }
                } else if (data.getData() != null) {
                    results = new Uri[]{data.getData()};
                }
            }
            filePathCallback.onReceiveValue(results);
            filePathCallback = null;
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    // ------------------------------------------------------------------ 启动 Python 服务

    private File dataDir() {
        File dir;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && Environment.isExternalStorageManager()) {
            dir = new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS),
                    "Bookcase");
        } else {
            dir = new File(getFilesDir(), "bookcase-data");
        }
        //noinspection ResultOfMethodCallIgnored
        dir.mkdirs();
        return dir;
    }

    private String[] roots() {
        File ext = Environment.getExternalStorageDirectory();
        List<String> out = new ArrayList<>();
        for (String name : new String[]{"Download", "Documents", "Books"}) {
            File f = new File(ext, name);
            if (f.isDirectory()) out.add(f.getAbsolutePath());
        }
        if (out.isEmpty()) {
            out.add(new File(ext, "Download").getAbsolutePath());
        }
        return out.toArray(new String[0]);
    }

    private void startServer() {
        if (starting || ready) return;
        starting = true;
        overlay.setVisibility(View.VISIBLE);
        spinner.setVisibility(View.VISIBLE);
        retry.setVisibility(View.GONE);
        status.setText("正在启动书库…");

        final File dataDir = dataDir();
        final String[] roots = roots();

        new Thread(() -> {
            try {
                // Activity 重建 / 上次退出后进程还活着时，服务可能已经在 8765 上跑着；
                // 这时直接复用，别再起第二个实例（否则 OSError: Address already in use）
                if (pingHttp()) {
                    Log.i(TAG, "bookcase server already running, reuse it");
                    ui.post(this::showWeb);
                    return;
                }
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplicationContext()));
                }
                Python.getInstance().getModule("bootstrap")
                        .callAttr("run", dataDir.getAbsolutePath(), PORT, roots);
            } catch (Throwable t) {
                Log.e(TAG, "python server failed", t);
                ui.post(() -> showError(t));
            }
        }, "bookcase-server").start();

        waitForServer();
    }

    private void waitForServer() {
        new Thread(() -> {
            long deadline = System.currentTimeMillis() + 120_000L;
            while (System.currentTimeMillis() < deadline) {
                if (pingHttp()) {
                    ui.post(this::showWeb);
                    return;
                }
                try {
                    Thread.sleep(400);
                } catch (InterruptedException e) {
                    return;
                }
            }
            ui.post(() -> {
                if (!ready) {
                    starting = false;
                    overlay.setVisibility(View.VISIBLE);
                    spinner.setVisibility(View.GONE);
                    retry.setVisibility(View.VISIBLE);
                    status.setText("书库没能在两分钟内启动。\n可以点重试；还不行就把日志发我。");
                }
            });
        }, "bookcase-wait").start();
    }

    /** 服务是否已经在跑：/api/stats，1 秒超时（启动前探测和启动后等待都用它）。 */
    private boolean pingHttp() {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(BASE_URL + "api/stats").openConnection();
            conn.setConnectTimeout(1000);
            conn.setReadTimeout(1000);
            conn.setInstanceFollowRedirects(false);
            return conn.getResponseCode() == 200;
        } catch (Exception e) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private void showWeb() {
        if (ready) return;
        ready = true;
        starting = false;
        overlay.setVisibility(View.GONE);
        webView.loadUrl(BASE_URL);
    }

    private void showError(Throwable t) {
        starting = false;
        overlay.setVisibility(View.VISIBLE);
        spinner.setVisibility(View.GONE);
        retry.setVisibility(View.VISIBLE);
        String msg = t.getMessage();
        if (msg == null || msg.isEmpty()) msg = t.getClass().getSimpleName();
        status.setText("书库启动失败：\n" + msg);
    }
}
