@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo 没有找到 Python。请先安装 Python 3.8+，安装时勾选 "Add python.exe to PATH"。
  echo.
  pause
  exit /b 1
)

if not exist config.json (
  echo.
  echo 还没有 config.json。
  echo 请先复制 config.example.json 为 config.json，并把 roots 改成你的书库目录。
  echo 例如： "roots": ["Z:\\电子书"]
  echo.
  pause
  exit /b 1
)

echo 安装依赖（第一次需要联网，之后可跳过）...
python -m pip install -r requirements.txt

echo.
echo 启动书库。手机连同一个 Wi-Fi，打开本机局域网 IP 的 8765 端口。
echo 按 Ctrl+C 停止。
echo.
python server.py
pause