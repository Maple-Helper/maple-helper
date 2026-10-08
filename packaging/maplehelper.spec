# PyInstaller spec: builds dist/Maple Helper/ (onedir; the installer wraps it) on Windows,
# and dist/Maple Helper.app (a menu bar app; the DMG wraps it) on macOS.
# Run from the repo root:  .venv\Scripts\pyinstaller packaging\maplehelper.spec --noconfirm
# (or through packaging/build.ps1 / packaging/build-macos.sh)
import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
MAC = sys.platform == "darwin"
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "maplehelper" / "__init__.py").read_text()).group(1)

datas = [(str(ROOT / "assets"), "assets")]
kb = Path(os.environ.get("MAPLEHELPER_KB") or ROOT / "data" / "kb")   # CI points this at a KB of its choice
if (kb / "index.json").exists():
    datas.append((str(kb), "data/kb"))

binaries, hiddenimports = [], ["keyring.backends.macOS" if MAC else "keyring.backends.Windows",
                               "maplehelper.macapi" if MAC else "maplehelper.winapi"]
for pkg in ("faster_whisper", "ctranslate2", "sounddevice", "rapidocr"):     # rapidocr: its ONNX models and yaml configs
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    runtime_hooks=[str(ROOT / "packaging" / "rthook_no_av.py")],
    excludes=["av", "hf_xet", "tkinter", "torch", "tensorflow", "matplotlib", "pytest", "PySide6.QtWebEngineCore",
              "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml"],
    noarchive=False,
)
# drop heavy binaries the app never loads
DROP = ("opengl32sw", "Qt6Quick", "Qt6Qml", "Qt6Pdf", "Qt6VirtualKeyboard", "Qt6OpenGL", "av.libs", "avcodec",
        "avformat", "avutil", "swresample", "swscale", "avfilter", "avdevice")
a.binaries = [b for b in a.binaries if not any(d.lower() in b[0].lower() for d in DROP)]
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").startswith(("PySide6/translations/qtwebengine",))]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Maple Helper",
    # macOS: PyInstaller turns the PNG into an .icns (with Pillow, which the app depends on anyway)
    icon=str(ROOT / "assets" / "brand" / ("icon-source.png" if MAC else "app.ico")),
    version=None if MAC else str(ROOT / "packaging" / "version_info.txt"),
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Maple Helper", strip=False, upx=False)

if MAC:
    app = BUNDLE(
        coll,
        name="Maple Helper.app",
        icon=str(ROOT / "assets" / "brand" / "icon-source.png"),
        bundle_identifier="com.maplehelper.app",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "Maple Helper",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSUIElement": True,            # menu bar app: no Dock icon, may float over a fullscreen game
            "LSMinimumSystemVersion": "14.0",  # onnxruntime ships only macosx_14_0 wheels (Qt: 13.0)
            "NSHighResolutionCapable": True,
            # without this text macOS silently denies the microphone
            "NSMicrophoneUsageDescription": "Maple Helper records only after you press the talk key or the mic "
                                            "button, and transcribes on this Mac.",
            "NSHumanReadableCopyright": "Unofficial fan project. MapleStory is a trademark of Nexon.",
        },
    )
