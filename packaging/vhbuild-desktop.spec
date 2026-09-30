# PyInstaller spec for the V-Hbuild desktop app.
#   pyinstaller packaging/vhbuild-desktop.spec --noconfirm
# Produces dist/V-Hbuild/ (a folder app: fast start, and the installer wraps it).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "vhbuild" / "static"), "vhbuild/static")]
datas += collect_data_files("esptool")        # flasher stubs, one per chip
datas += collect_data_files("trimesh")        # exporter templates and resources
datas += collect_data_files("webview")
binaries = collect_dynamic_libs("manifold3d")
hiddenimports = (collect_submodules("uvicorn") + collect_submodules("esptool")
                 + collect_submodules("serial.tools") + collect_submodules("webview")
                 + ["vhbuild.web", "networkx", "lxml.etree"])

a = Analysis(
    [str(ROOT / "vhbuild" / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "scipy", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="V-Hbuild",
    console=False,                   # a window app; errors go to the window
    icon=str(ROOT / "packaging" / "vhbuild.ico") if (ROOT / "packaging" / "vhbuild.ico").exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="V-Hbuild")
