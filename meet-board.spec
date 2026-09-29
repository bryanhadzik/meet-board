# PyInstaller spec: one-file console exe with templates and static bundled.
# Build: uv run pyinstaller meet-board.spec   ->  dist/meet-board(.exe)
from PyInstaller.utils.hooks import collect_submodules

hidden = (
    collect_submodules('hytek_parser')
    + collect_submodules('engineio.async_drivers')
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
    icon=None,
)
