# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Media Downloader
# Run:  pyinstaller MediaDownloader.spec
#
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_all

block_cipher = None

# ── Package data files ─────────────────────────────────────────────────────────
datas = []
datas += collect_data_files('sv_ttk')     # Sun Valley theme TCL/TK files
datas += collect_data_files('f2')         # f2 language / config files
datas += [('helpers', 'helpers')]         # f2_one.py, f2_user.py

# ── Bundled tool binaries (must be present next to this spec file) ─────────────
binaries = []
for _name in ('gallery-dl.exe', 'yt-dlp.exe'):
    _p = Path(_name)
    if _p.exists():
        binaries.append((str(_p), '.'))
    else:
        print(f"WARNING: {_name} not found — it will NOT be bundled.")

# ── Hidden imports PyInstaller may miss ────────────────────────────────────────
hiddenimports = [
    'sv_ttk',
    'tkinter',
    'tkinter.ttk',
    'tkinter.scrolledtext',
    'tkinter.messagebox',
    'tkinter.filedialog',
    'asyncio',
    'f2',
    'f2.apps.douyin.handler',
    'f2.apps.douyin.utils',
    'f2.utils.utils',
    'aiohttp',
    'aiofiles',
]

# ── Analysis ───────────────────────────────────────────────────────────────────
a = Analysis(
    ['app.py'],
    pathex=[str(Path.cwd())],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas', 'PIL', 'pytest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE ────────────────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MediaDownloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                          # no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico' if Path('icon.ico').exists() else None,
)

# ── Collect (--onedir output) ──────────────────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MediaDownloader',
)
