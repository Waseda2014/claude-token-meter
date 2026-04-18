"""
py2app build script for Claude Token Meter.
Run:  python3 setup.py py2app
"""
from setuptools import setup

APP     = ['Claude Token Meter.py']
OPTIONS = {
    'argv_emulation': False,   # must be False for PyObjC menu-bar apps
    'emulate_shell_environment': False,
    'semi_standalone': False,
    'packages': [
        'objc', 'AppKit', 'Foundation', 'WebKit',
        'Cocoa', 'CoreFoundation',
    ],
    'frameworks': [],
    'plist': {
        # ── Identity ──────────────────────────────────────────────
        'CFBundleName':              'Claude Token Meter',
        'CFBundleDisplayName':       'Claude Token Meter',
        'CFBundleIdentifier':        'com.johnnyhuang.claudetokenmeter',
        'CFBundleVersion':           '1.0.0',
        'CFBundleShortVersionString': '1.0',

        # ── Menu-bar app (no Dock icon) ───────────────────────────
        'LSUIElement': True,

        # ── Display ───────────────────────────────────────────────
        'NSHighResolutionCapable':   True,
        'NSSupportsAutomaticGraphicsSwitching': True,

        # ── Permissions (needed for reading ~/.claude JSONL files) ─
        'NSDesktopFolderUsageDescription':
            'Claude Token Meter reads Claude usage logs from your home folder.',
        'NSDocumentsFolderUsageDescription':
            'Claude Token Meter reads Claude usage logs from your home folder.',
        'NSDownloadsFolderUsageDescription':
            'Claude Token Meter reads Claude usage logs from your home folder.',

        # ── Network (claude.ai API calls) ─────────────────────────
        'NSAppTransportSecurity': {
            'NSAllowsArbitraryLoads': True,
        },
    },
    'excludes': ['tkinter', 'matplotlib', 'numpy', 'scipy', 'PIL'],
}

setup(
    name='Claude Token Meter',
    app=APP,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
