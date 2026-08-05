# Build the GUI as a Windows executable (onedir layout).
# Usage: powershell -ExecutionPolicy Bypass -File build.ps1
# Output: dist/HandWriteSim/

Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue

uv run --extra dev pyinstaller --noconfirm --clean HandWriteSim.spec

Write-Host ""
Write-Host "Build finished: dist/HandWriteSim/HandWriteSim.exe"
Write-Host "Distribute the whole dist/HandWriteSim folder."