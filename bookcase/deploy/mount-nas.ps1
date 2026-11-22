# 在 Windows 上把联想个人云 T2 的 Samba 共享映射成网络驱动器（默认 Z:）。
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\deploy\mount-nas.ps1 -Server 192.168.1.x -User 设备UID
#   powershell -ExecutionPolicy Bypass -File .\deploy\mount-nas.ps1 -Server 192.168.1.x -Share "个人空间" -User 设备UID -Drive Z
# 密码不填会提示输入；联想默认密码通常是 123456。
param(
  [Parameter(Mandatory = $true)][string]$Server,
  [string]$Share = "",
  [Parameter(Mandatory = $true)][string]$User,
  [string]$Password = "",
  [string]$Drive = "Z"
)

$ErrorActionPreference = "Stop"
$driveRoot = "$Drive`:"
$remote = "\\$Server"
if ($Share) { $remote = "$remote\$Share" }

if (-not $Password) {
  $secure = Read-Host "Samba 密码（联想默认 123456）" -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try { $Password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

Write-Host "映射 $driveRoot -> $remote（用户 $User）..." -ForegroundColor Cyan

# 先清掉旧的同名映射，避免“设备已在使用”
try { Remove-SmbMapping -LocalPath $driveRoot -Force -ErrorAction SilentlyContinue } catch { }
cmd /c "net use $driveRoot /delete /y" 2>$null | Out-Null

$ok = $false
try {
  New-SmbMapping -LocalPath $driveRoot -RemotePath $remote -UserName $User -Password $Password -Persistent $false -ErrorAction Stop | Out-Null
  $ok = $true
} catch {
  Write-Host "New-SmbMapping 失败，改用 net use 再试一次..." -ForegroundColor Yellow
  cmd /c "net use $driveRoot `"$remote`" `"$Password`" /user:`"$User`" /persistent:no" | Out-Null
  if ($LASTEXITCODE -eq 0) { $ok = $true }
}

if (-not $ok -or -not (Test-Path "$driveRoot\")) {
  Write-Host "映射失败。检查：NAS 是否开机、IP 是否正确、Samba 是否在「数据守护者」里打开、UID/密码是否正确。" -ForegroundColor Red
  exit 1
}

Write-Host "成功：$driveRoot 已指向 $remote" -ForegroundColor Green
Write-Host "下一步：把 config.json 的 roots 改成 ${driveRoot}\电子书（或你的书库目录），再启动书库。"