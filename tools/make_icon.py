"""meet-board's icon: assets/meet-board.svg is the source (a scoreboard with fireworks).
assets/meet-board-256.png is rendered from it (any browser: open the SVG at 256x256 and save);
this script makes assets/meet-board.ico and static/favicon.ico from that PNG.
Run: uv run --with pillow python tools/make_icon.py"""
from PIL import Image

img = Image.open('assets/meet-board-256.png').convert('RGBA')
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
img.save('assets/meet-board.ico', sizes=sizes)
img.save('static/favicon.ico', sizes=sizes[:4])
