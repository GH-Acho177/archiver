# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Media Downloader
# Run:  pyinstaller build\MediaDownloader.spec   (from project root)
#
import importlib.util as _ilu
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
_root = Path(SPECPATH).parent   # project root  (spec lives in packaging/, so parent = root)

# ── Locate sv_ttk and copy its entire package folder into the bundle ───────────
# collect_all / collect_data_files only grab theme assets, not the .py files.
# Copying the folder to datas works because PyInstaller adds sys._MEIPASS to
# sys.path at runtime, making anything placed there directly importable.
_sv = _ilu.find_spec('sv_ttk')
if not _sv or not _sv.submodule_search_locations:
    raise SystemExit(
        "\n[BUILD ERROR] sv_ttk not found.\n"
        "Activate the virtual environment that has sv_ttk installed, then re-run build.bat.\n"
    )
_sv_dir = list(_sv.submodule_search_locations)[0]

# ── Package data files ─────────────────────────────────────────────────────────
datas    = []
binaries = []
hiddenimports = []

datas += [(_sv_dir, 'sv_ttk')]            # sv_ttk package (Python + theme assets)
datas += collect_data_files('f2')         # f2 language / config files
datas += [(str(_root / 'helpers'), 'helpers')]   # f2_one.py, f2_user.py
if (_root / 'fonts').exists():
    datas += [(str(_root / 'fonts'), 'fonts')]   # bundled fonts (e.g. JetBrains Mono)
if (_root / 'assets' / 'icon.ico').exists():
    datas += [(str(_root / 'assets' / 'icon.ico'), 'assets')]  # tray / window icon

# ── Bundled tool binaries (live next to this spec in packaging/) ───────────────
for _name in ('gallery-dl.exe', 'yt-dlp.exe'):
    _p = Path(SPECPATH) / _name
    if _p.exists():
        binaries.append((str(_p), '.'))
    else:
        print(f"WARNING: {_name} not found in packaging/ — it will NOT be bundled.")

# ── Hidden imports PyInstaller may miss ────────────────────────────────────────
hiddenimports += [
    'src.config',
    'src.utils',
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
    'pystray',
    'pystray._win32',
    'PIL',
    'PIL.Image',
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
    excludes=['matplotlib', 'numpy', 'pandas', 'pytest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE ────────────────────────────────────────────────────────────────────────
_icon = str(_root / 'assets' / 'icon.ico') if (_root / 'assets' / 'icon.ico').exists() else None

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
