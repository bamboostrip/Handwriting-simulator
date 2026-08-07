# Build the GUI as a Windows executable (onefile) and package a portable zip.
# Usage: powershell -ExecutionPolicy Bypass -File build.ps1
# Output:
#   dist/HandWriteSim.exe                 (single file, copy it anywhere)
#   dist/HandWriteSim-windows-x86_64.zip  (portable bundle:
#                                          exe + presets + backgrounds + fonts dir,
#                                          same layout as the GitHub Actions artifact)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$dist = Join-Path $root "dist"
$staging = Join-Path $root "staging"

Remove-Item -Recurse -Force $dist, (Join-Path $root "build") -ErrorAction SilentlyContinue

uv run --extra dev pyinstaller --noconfirm --clean HandWriteSim.spec

# 组装便携包：exe + 预设 + 背景 + fonts 目录（与 Actions 产物结构一致）
# 本地打包额外携带 fonts/ 下的字体（自用，版权字体不入库不上传）
Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$staging\fonts", "$staging\backgrounds", "$staging\presets" | Out-Null
# 背景压缩后组装（jpg quality 80，源素材保持原样），控制 zip 体积在 100MB（蓝奏云上限）以内
uv run python (Join-Path $root "packaging\compress_backgrounds.py") "$staging\backgrounds\"
Copy-Item -Recurse (Join-Path $root "presets\*") "$staging\presets\"
Copy-Item (Join-Path $dist "HandWriteSim.exe") "$staging\"
# 本地字体目录（存在则全部携带，供自用）
if (Test-Path (Join-Path $root "fonts")) {
    Copy-Item -Recurse (Join-Path $root "fonts\*") "$staging\fonts\" -Force
}
Copy-Item (Join-Path $root "packaging\fonts-README.txt") "$staging\fonts\README.txt"

$zip = Join-Path $dist "HandWriteSim-windows-x86_64.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "$staging\*" -DestinationPath $zip -Force
Remove-Item -Recurse -Force $staging

Write-Host ""
Write-Host "Build finished:"
Write-Host "  $dist\HandWriteSim.exe                 (single file, $(("{0:N1}" -f ((Get-Item (Join-Path $dist 'HandWriteSim.exe')).Length / 1MB))) MB)"
Write-Host "  $zip  (portable bundle)"
Write-Host "Copy the zip anywhere and extract - no extra steps needed."