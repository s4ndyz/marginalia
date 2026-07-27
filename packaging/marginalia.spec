# -*- mode: python ; coding: utf-8 -*-
#
# Marginalia 的 PyInstaller 打包配置。
#
# 用法（必须在 macOS 上执行，无法从别的系统交叉打包出 .app）：
#
#     cd 项目根目录
#     uv run pyinstaller packaging/marginalia.spec --clean
#
# 打包产物在 dist/Marginalia.app
#
# 关于 --clean：每次改了 .spec 之后都建议加，否则 PyInstaller 会复用
# build/ 里的缓存分析结果，有时候资源文件更新了也不会重新收集。

import sys
from pathlib import Path

block_cipher = None

# .spec 文件运行时，PyInstaller 会把当前工作目录加进 sys.path，
# 但为了保险起见（比如以后有人从别的目录调用），这里显式定位项目根目录。
PROJECT_ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        # assets/web 下的 JS 文件要原样打进包里，
        # core/paths.py 里的 resource_path() 打包后就是去这个目录下找
        (str(PROJECT_ROOT / "assets" / "web"), "assets/web"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Marginalia",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # QtWebEngine 的二进制体积本来就大，UPX 压缩收益不大，
                         # 反而可能触发 macOS Gatekeeper 的误报，索性关掉
    console=False,       # 不要黑色终端窗口，这是一个 GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,    # 默认按当前机器架构打（Apple Silicon 就是 arm64）
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "packaging" / "icon.icns"), 
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Marginalia",
)

app = BUNDLE(
    coll,
    name="Marginalia.app",
    icon=str(PROJECT_ROOT / "packaging" / "icon.icns"), 
    bundle_identifier="com.marginalia.reader",
    info_plist={
        "CFBundleName": "Marginalia",
        "CFBundleDisplayName": "Marginalia",
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        # epub 文件双击后可以直接用 Marginalia 打开（可选，锦上添花）
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "EPUB Document",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": ["org.idpf.epub-container"],
            }
        ],
    },
)
