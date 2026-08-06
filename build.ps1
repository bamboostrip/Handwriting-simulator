# Build the GUI as a Windows executable (onefile layout).
# Usage: powershell -ExecutionPolicy Bypass -File build.ps1
# Output: dist/HandWriteSim.exe (single file, copy it anywhere)

Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue

uv run --extra dev pyinstaller --noconfirm --clean HandWriteSim.spec

Write-Host ""
Write-Host "Build finished: dist/HandWriteSim.exe (single file)"
Write-Host "Copy the exe anywhere - no extra folders needed."