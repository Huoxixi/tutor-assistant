from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / 'app/static'), 'app/static'),
         (str(root / 'app/assets'), 'app/assets'),
         (str(root / 'README.md'), '.'),
         (str(root / 'LICENSE'), '.'),
         (str(root / 'THIRD_PARTY_NOTICES.md'), '.')]
datas += copy_metadata('pywechat127')
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=['uvicorn.logging', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
        'uvicorn.lifespan.off', 'pythoncom', 'win32crypt', 'pyweixin', 'pyweixin.Uielements'],
    hookspath=[str(root / 'packaging/hooks')], runtime_hooks=[],
    excludes=['pytest', 'IPython', 'matplotlib', 'pandas', 'scipy', 'notebook'],
    noarchive=False, optimize=0)
pyz = PYZ(a.pure)
# Sounddevice's ASIO driver is unused by this read-only product. Do not ship
# optional Steinberg-ASIO binaries with separate redistribution conditions.
a.binaries = [entry for entry in a.binaries if '-asio' not in entry[0].lower()]
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='TutorAssistant', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='TutorAssistant')
