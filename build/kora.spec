# PyInstaller one-folder specification for KORA.
# Run from the repository root with: python -m PyInstaller build/kora.spec
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
ICON = os.path.join(SRC, "web", "kora_logo.ico")
CONSOLE = os.environ.get("KORA_BUILD_CONSOLE") == "1"

block_cipher = None
datas, binaries, hiddenimports = [], [], []

# Collect package data and native dependencies used by KORA's runtime modules.
for package in (
    "webview", "flask", "werkzeug", "requests", "pymupdf", "docx", "pptx", "openpyxl", "reportlab",
    "pytesseract", "xlrd", "pypdf", "PIL",
):
    try:
        package_data, package_binaries, package_hidden = collect_all(package)
        datas += package_data
        binaries += package_binaries
        hiddenimports += package_hidden
    except Exception as exc:
        print(f"[kora.spec] package collection skipped for {package}: {exc}")

# Flat project modules are explicitly listed so dynamic imports are retained.
hiddenimports += [
    "app_logging", "config", "engine", "exporter", "extractor", "knowledge",
    "llm", "model_runtime", "server", "tools", "tuning",
    "clr", "pythonnet", "bottle", "proxy_tools", "winreg",
]

a = Analysis(
    [os.path.join(SRC, "main.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy.tests", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="KORA",
    debug=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    icon=ICON if os.path.isfile(ICON) else None,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="KORA")
