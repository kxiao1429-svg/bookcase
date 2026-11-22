# 在 Windows（Docker Desktop）上构建指定架构的镜像，导出 tar 给联想个人云导入。
# 用法：
#   T2 Pro（Intel x86_64）:
#     .\deploy\build-arm64-image.ps1 -Platform linux/amd64 -Tag bookcase:amd64 -Out bookcase-amd64.tar
#   ARM64 机型（Docker 能跑的话）:
#     .\deploy\build-arm64-image.ps1 -Platform linux/arm64 -Tag bookcase:arm64 -Out bookcase-arm64.tar
param(
  [string]$Tag = "bookcase:arm64",
  [string]$Out = "bookcase-arm64.tar",
  [string]$Platform = "linux/arm64"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
  Write-Host "构建 $Tag（$Platform）..." -ForegroundColor Cyan
  docker buildx build --platform $Platform -t $Tag --load .
  if ($LASTEXITCODE -ne 0) { throw "docker buildx 构建失败" }

  Write-Host "导出 $Out ..." -ForegroundColor Cyan
  docker save $Tag -o $Out
  if ($LASTEXITCODE -ne 0) { throw "docker save 失败" }

  $full = Join-Path $root $Out
  Write-Host "完成：$full" -ForegroundColor Green
  Write-Host "把它拷到 NAS，执行：docker load -i $Out"
} finally {
  Pop-Location
}