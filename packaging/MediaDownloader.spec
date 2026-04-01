# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Media Downloader
# Run:  pyinstaller build\MediaDownloader.spec   (from project root)
#
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_all

block_cipher = None
_root = Path(SPECPATH).parent   # project root  (spec lives in build/, so parent = root)

# ── Package data files ─────────────────────────────────────────────────────────
datas    = []
binaries = []
hiddenimports = []

sv_ttk_datas, sv_ttk_bins, sv_ttk_hidden = collect_all('sv_ttk')
datas         += sv_ttk_datas
binaries      += sv_ttk_bins
hiddenimports += sv_ttk_hidden

datas += collect_data_files('f2')         # f2 language / config files
datas += [(str(_root / 'helpers'), 'helpers')]   # f2_one.py, f2_user.py

# ── Bundled tool binaries (live next to this spec in packaging/) ───────────────
for _name in ('gallery-dl.exe', 'yt-dlp.exe'):
    _p = Path(SPECPATH) / _name
    if _p.exists():
        binaries.append((str(_p), '.'))
    else:
        print(f"WARNING: {_name} not found in packaging/ — it will NOT be bundled.")

# ── Hidden imports PyInstaller may miss ────────────────────────────────────────
hiddenimports += [
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
    [str(_root / 'app.py')],
    pathex=[str(_root)],
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
_icon = str(_root / 'icon.ico') if (_root / 'icon.ico').exists() else None

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
    icon=_icon,
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
