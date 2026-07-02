# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for E-Paper Dashboard
# Build with:
#   C:\Tools\epaper-build\Scripts\pyinstaller.exe epaper_dashboard.spec

import os
SRC = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    [os.path.join(SRC, 'main.py')],
    pathex=[SRC],
    binaries=[],
    datas=[
        (os.path.join(SRC, 'fonts'),               'fonts'),
        (os.path.join(SRC, 'pages'),               'pages'),
        (os.path.join(SRC, 'config.template.yml'), '.'),
        (os.path.join(SRC, 'icon.ico'),            '.'),
    ],
    hiddenimports=[
        'PIL._tkinter_finder',
        'pystray._win32',
        'pkg_resources.py2_compat',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EPaperDashboard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SRC, 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='EPaperDashboard',
)
