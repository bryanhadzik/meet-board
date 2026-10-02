# PyInstaller spec: one-file console exe with templates and static bundled.
# Build: uv run pyinstaller meet-board.spec   ->  dist/meet-board(.exe)
import re
import sys
from PyInstaller.utils.hooks import collect_submodules

version = re.search(r'"([^"]+)"', open('_version.py').read()).group(1)
nums = (list(map(int, re.findall(r'\d+', version))) + [0, 0, 0, 0])[:4]

version_info = None
if sys.platform == 'win32':
  from PyInstaller.utils.win32.versioninfo import (
      VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct)
  # Windows file version resource: lets the launcher read the version instantly
  # from the exe's Properties instead of running it.
  version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=tuple(nums), prodvers=tuple(nums)),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('CompanyName', 'bryanhadzik/meet-board'),
            StringStruct('FileDescription', 'meet-board - CTS scoreboard overlay and meet board'),
            StringStruct('FileVersion', version),
            StringStruct('ProductName', 'meet-board'),
            StringStruct('ProductVersion', version),
            StringStruct('OriginalFilename', 'meet-board.exe'),
        ])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ],
)

hidden = (
    collect_submodules('hytek_parser')
    + collect_submodules('engineio.async_drivers')
    + collect_submodules('mutagen')          # song tags + length; formats load on demand
    + ['engineio.async_drivers.threading', 'simple_websocket', 'serial.tools.list_ports']
)

a = Analysis(
    ['CTS_Scoreboard.py'],
    pathex=['.'],
    datas=[('templates', 'templates'), ('static', 'static')],
    hiddenimports=hidden,
    excludes=['tkinter', 'pytest', 'playwright'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='meet-board',
    console=True,
    upx=False,
    icon='assets/meet-board.ico',
    version=version_info,
)
