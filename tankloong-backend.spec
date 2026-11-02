# Python sidecar only. Electron owns all UI. Keep paths portable.
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_all
root = Path(SPECPATH)
build_data = root / 'artifacts/build-runtime'
build_data.mkdir(parents=True, exist_ok=True)
os.environ['WIN_PD_OVERRIDE_LOCAL_APPDATA'] = str(build_data)
datas, binaries, hiddenimports = [], [(str(root / 'ffmpeg/bin/ffmpeg.exe'), 'ffmpeg/bin')], []
for package in ('musicdl', 'curl_cffi'):
    package_data, package_bins, imports = collect_all(package)
    datas += package_data
    binaries += package_bins
    hiddenimports += imports
a = Analysis([str(root / 'src/backend.py')], pathex=[str(root / 'src')],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=['PySide6', 'PyQt6', 'tkinter', 'av', 'av.libs'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='tankloong-backend',
          debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='tankloong-backend')
