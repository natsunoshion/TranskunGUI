# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main_gradio.py'],
    pathex=[],
    binaries=[('ffmpeg_bin\\ffmpeg.exe', 'ffmpeg_bin'), ('ffmpeg_bin\\ffprobe.exe', 'ffmpeg_bin')],
    datas=[('models', 'models'), ('C:\\Users\\myname\\micromamba\\envs\\yipeng\\lib\\site-packages\\gradio', 'gradio'), ('C:\\Users\\myname\\micromamba\\envs\\yipeng\\lib\\site-packages\\transkun', 'transkun'),
        ('C:\\Users\\myname\\micromamba\\envs\\yipeng\\lib\\site-packages\\gradio_client', 'gradio_client'),
        ('C:\\Users\\myname\\micromamba\\envs\\yipeng\\lib\\site-packages\\safehttpx', 'safehttpx'),
        ('C:\\Users\\myname\\micromamba\\envs\\yipeng\\lib\\site-packages\\groovy', 'groovy')],
    hiddenimports=['mir_eval'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TranskunGUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TranskunGUI',
)
