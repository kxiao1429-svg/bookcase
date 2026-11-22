# 把本地一个文件夹里的电子书整批备份到书库（跳过云端已有的）。
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\deploy\upload-folder.ps1 -Folder "D:\我的电子书"
#   powershell -ExecutionPolicy Bypass -File .\deploy\upload-folder.ps1 -Folder "D:\我的电子书" -DryRun
# 服务端先启动（start-windows.bat），脚本会自动读项目里的 config.json 拿端口和口令。
param(
  [Parameter(Mandatory = $true)][string]$Folder,
  [string]$Server = "",
  [string]$Token = "",
  [switch]$DryRun,
  [int]$ChunkSize = 500
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exts = @(".epub", ".pdf", ".mobi", ".azw3", ".azw", ".fb2", ".cbz", ".djvu")

if (-not (Test-Path -LiteralPath $Folder)) {
  Write-Host "目录不存在：$Folder" -ForegroundColor Red
  exit 1
}

$cfgPath = Join-Path $root "config.json"
if (Test-Path -LiteralPath $cfgPath) {
  try { $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $cfg = $null }
  if (-not $Server -and $cfg -and $cfg.port) { $Server = "http://127.0.0.1:$($cfg.port)" }
  if (-not $Token -and $cfg -and $cfg.token) { $Token = [string]$cfg.token }
}
if (-not $Server) { $Server = "http://127.0.0.1:8765" }
$Server = $Server.TrimEnd("/")

$headers = @{}
if ($Token) { $headers["Authorization"] = "Bearer $Token" }

Write-Host "扫描 $Folder ..." -ForegroundColor Cyan
$files = @(Get-ChildItem -LiteralPath $Folder -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $exts -contains $_.Extension.ToLower() })
if (-not $files.Count) {
  Write-Host "这个目录里没有支持的电子书（epub/pdf/mobi/azw3/azw/fb2/cbz/djvu）。" -ForegroundColor Yellow
  exit 0
}
$totalBytes = ($files | Measure-Object -Property Length -Sum).Sum
Write-Host ("找到 {0} 个文件，共 {1:N1} MB" -f $files.Count, ($totalBytes / 1MB)) -ForegroundColor Cyan

function Test-Health {
  try {
    $r = Invoke-RestMethod -Uri "$Server/api/health" -TimeoutSec 5
    return [bool]$r.ok
  } catch { return $false }
}
if (-not (Test-Health)) {
  Write-Host "连不上 $Server。先启动书库（start-windows.bat），再运行本脚本。" -ForegroundColor Red
  exit 1
}

# 预检：分批发 name+size，服务端返回云端还没有的
$missing = New-Object 'System.Collections.Generic.HashSet[string]'
$i = 0
while ($i -lt $files.Count) {
  $chunk = $files[$i..([Math]::Min($i + $ChunkSize - 1, $files.Count - 1))]
  $payload = @{ files = @($chunk | ForEach-Object { @{ name = $_.Name; size = $_.Length } }) } |
    ConvertTo-Json -Depth 5 -Compress
  try {
    $res = Invoke-RestMethod -Uri "$Server/api/upload/check" -Method Post -Body $payload `
      -ContentType "application/json; charset=utf-8" -Headers $headers -TimeoutSec 120
  } catch {
    Write-Host "预检失败：$($_.Exception.Message)" -ForegroundColor Red
    if ($_.Exception.Response.StatusCode.value__ -eq 401) { Write-Host "口令不对，检查 config.json 的 token。" }
    exit 1
  }
  foreach ($m in @($res.missing)) { if ($m) { [void]$missing.Add([string]$m) } }
  $i += $ChunkSize
  Write-Host ("预检 {0}/{1}" -f [Math]::Min($i, $files.Count), $files.Count)
}

$todo = @($files | Where-Object { $missing.Contains($_.Name) })
Write-Host ("云端已有 {0} 个，需要上传 {1} 个。" -f ($files.Count - $todo.Count), $todo.Count) -ForegroundColor Cyan
if ($DryRun) {
  $todo | Select-Object -First 200 | ForEach-Object { Write-Host ("  " + $_.FullName) }
  if ($todo.Count -gt 200) { Write-Host "  ..." }
  exit 0
}
if (-not $todo.Count) { Write-Host "全部已备份，不用传。" -ForegroundColor Green; exit 0 }

$uploaded = 0; $failed = 0; $sentBytes = 0
foreach ($f in $todo) {
  $n = $uploaded + $failed + 1
  Write-Progress -Activity "备份到云端" -Status ("{0}/{1} {2}" -f $n, $todo.Count, $f.Name) `
    -PercentComplete ([Math]::Min(100, [int](100 * ($n - 1) / $todo.Count)))
  $uri = "$Server/api/upload?name=" + [uri]::EscapeDataString($f.Name)
  try {
    $raw = Invoke-WebRequest -Uri $uri -Method Post -InFile $f.FullName `
      -ContentType "application/octet-stream" -Headers $headers -TimeoutSec 3600 -UseBasicParsing
    $resp = $raw.Content | ConvertFrom-Json
    if ($resp.ok) {
      if ($resp.duplicate) {
        Write-Host ("  跳过（内容重复）：{0}" -f $f.Name) -ForegroundColor DarkGray
      } else {
        $uploaded++; $sentBytes += $f.Length
        Write-Host ("  已上传：{0}" -f $f.Name) -ForegroundColor Green
      }
    } else {
      $failed++; Write-Host ("  失败：{0} - {1}" -f $f.Name, $resp.error) -ForegroundColor Red
    }
  } catch {
    $failed++
    Write-Host ("  失败：{0} - {1}" -f $f.Name, $_.Exception.Message) -ForegroundColor Red
    if ($_.Exception.Response.StatusCode.value__ -eq 401) {
      Write-Host "口令不对，后面的先停了。检查 config.json 的 token。" -ForegroundColor Red
      break
    }
  }
}
Write-Progress -Activity "备份到云端" -Completed
Write-Host ""
Write-Host ("完成：上传 {0} 个（{1:N1} MB），失败 {2} 个，跳过 {3} 个。" -f `
  $uploaded, ($sentBytes / 1MB), $failed, ($files.Count - $todo.Count)) -ForegroundColor Cyan
if ($failed) { exit 2 }