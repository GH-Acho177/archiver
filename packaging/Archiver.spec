# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Archiver
# Run:  pyinstaller packaging/Archiver.spec   (from project root)
#
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
_root = Path(SPECPATH).parent   # project root (spec lives in packaging/)

# ── Package data files ─────────────────────────────────────────────────────────
datas    = []
binaries = []
hiddenimports = []

datas += collect_data_files('f2')                               # f2 language / config files
datas += [(str(_root / 'helpers'), 'helpers')]                  # f2_one.py, f2_user.py, tg_bot.py
datas += [(str(_root / 'src'), 'src')]                          # api.py, config.py, creator_store.py
datas += [(str(_root / 'viewer'), 'viewer')]                    # integrated archive viewer
if (_root / 'ui' / 'dist').exists():
    datas += [(str(_root / 'ui' / 'dist'), 'ui/dist')]          # React build

# Assets (icon + platform icons)
_assets = _root / 'assets'
for _asset in ('Archiver.png', 'Archiver.ico', 'Archiver_Viewer.png', 'Archiver_Viewer.ico', 'X.png', 'douyin.png', 'bilibili.png', 'xiaohongshu.png'):
    if (_assets / _asset).exists():
        datas += [(str(_assets / _asset), 'assets')]

# ── Bundled tool binaries (live next to this spec in packaging/) ───────────────
for _name in ('gallery-dl.exe', 'yt-dlp.exe', 'ffmpeg.exe', 'ffprobe.exe'):
    _p = Path(SPECPATH) / _name
    if _p.exists():
        binaries.append((str(_p), '.'))
    else:
        print(f"WARNING: {_name} not found in packaging/ — it will NOT be bundled.")

# ── Hidden imports PyInstaller may miss ────────────────────────────────────────
hiddenimports += [
    'src.config',
    'src.creator_store',
    'src.api',
    'fastapi',
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'webview',
    'pystray',
    'pystray._win32',
    'PIL',
    'PIL.Image',
    'asyncio',
    'f2',
    'f2.apps.douyin.handler',
    'f2.apps.douyin.utils',
    'f2.utils.utils',
    'aiohttp',
    'aiofiles',
    'truststore',
    'websockets',
    'xhs_cli',
    'xhs_cli.client',
    'xhs_cli.client_mixins',
    'xhshow',
    'httpx',
    'Crypto',
]

# ── Analysis ───────────────────────────────────────────────────────────────────
a = Analysis(
    [str(_root / 'run_api.py')],
    pathex=[str(_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas', 'pytest', 'tkinter', 'sv_ttk'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE ────────────────────────────────────────────────────────────────────────
_icon = str(_root / 'assets' / 'Archiver.ico') if (_root / 'assets' / 'Archiver.ico').exists() else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Archiver',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    name='Archiver',
)
