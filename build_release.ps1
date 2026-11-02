$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Create .venv and install requirements.txt first.' }
$ffmpegExe = Join-Path $PSScriptRoot 'ffmpeg\bin\ffmpeg.exe'
if (-not (Test-Path -LiteralPath $ffmpegExe)) { throw 'Missing bundled FFmpeg.' }
& $ffmpegExe -v error -f lavfi -i 'sine=frequency=440:duration=0.05' -codec:a libmp3lame -f mp3 NUL -y
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg encoder check failed.' }
& $pythonExe -m PyInstaller --noconfirm --clean tankloong-backend.spec
if ($LASTEXITCODE -ne 0) { throw 'Python service build failed.' }
& npm.cmd run pack
if ($LASTEXITCODE -ne 0) { throw 'Electron packaging failed.' }
$releaseExe = Join-Path $PSScriptRoot 'release\win-unpacked\TankLoong.exe'
if (-not (Test-Path -LiteralPath $releaseExe)) { throw 'Release executable missing.' }
Write-Host "Electron release ready: $releaseExe"
Write-Host 'Distribute the entire release/win-unpacked folder.'
