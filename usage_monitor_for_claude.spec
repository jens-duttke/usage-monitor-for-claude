# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Claude&CodexUsage.

Build:
  pyinstaller usage_monitor_for_claude.spec
"""

a = Analysis(
    ['usage_monitor_for_claude/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('locale/*.json', 'locale'),
        ('usage_monitor_for_claude/notification_logo.ico', 'usage_monitor_for_claude'),
        ('usage_monitor_for_claude/popup/popup.html', 'usage_monitor_for_claude/popup'),
        ('usage_monitor_for_claude/popup/popup.css', 'usage_monitor_for_claude/popup'),
        ('usage_monitor_for_claude/popup/popup.js', 'usage_monitor_for_claude/popup'),
    ],
    hiddenimports=[
        'usage_monitor_for_claude.platforms.win32',
        'usage_monitor_for_claude.platforms.instance_win32',
        'usage_monitor_for_claude.platforms.popup_win32',
        'pystray._win32',
        'pystray._util',
        'pystray._util.win32',
        'webview',
        'webview.platforms.edgechromium',
        'clr_loader',
        'pythonnet',
        'bottle',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'unittest', 'test',
        'xmlrpc', 'pydoc',
        'tkinter', '_tkinter',
        'PIL._avif', 'PIL._webp',
        'PIL._imagingcms', 'PIL._imagingmath', 'PIL._imagingtk', 'PIL._imagingmorph',
        'setuptools', '_distutils_hack',
        'asyncio', 'concurrent',
        'multiprocessing',
        'xml', 'tomllib',
        'sqlite3',
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
    name='Claude&CodexUsage',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon='usage_monitor_for_claude.ico',
    version='version_info.py',
)
