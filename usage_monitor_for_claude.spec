# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Usage Monitor for Claude.

Build:
  pyinstaller usage_monitor_for_claude.spec
"""

a = Analysis(
    ['usage_monitor_for_claude.py'],
    pathex=[],
    binaries=[],
    datas=[('locale/*.json', 'locale')],
    hiddenimports=[
        'pystray._win32',
        'pystray._util',
        'pystray._util.win32',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'unittest', 'test',
        'html', 'http.server',
        'xmlrpc', 'pydoc',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='UsageMonitorForClaude',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon='usage_monitor.ico',
    version_info='version_info.py',
)
