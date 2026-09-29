"""Draws assets/meet-board.ico: a dark board with four lanes, each with a
team-color stripe, under a teal header - the meet board in miniature.
Run: uv run --with pillow python tools/make_icon.py"""
from PIL import Image, ImageDraw

S = 1024
img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
r = 180
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=r, fill='#0b1618')
# teal header band
d.rounded_rectangle([40, 40, S - 40, 330], radius=r, fill='#0a6b7c')
d.rectangle([40, 230, S - 40, 330], fill='#0a6b7c')
# clock digits hint: three white bars in the header
for i, w in enumerate([150, 90, 150]):
    x = 150 + i * 190
    d.rounded_rectangle([x, 150, x + w, 225], radius=30, fill='#ffffff')
# four lanes with team stripes and name bars
colors = ['#D18AE0', '#3D8BF5', '#F0546A', '#D9C46E']
top, lane_h, gap = 380, 125, 22
for i, c in enumerate(colors):
    y = top + i * (lane_h + gap)
    d.rounded_rectangle([110, y, S - 110, y + lane_h], radius=34, fill='#17282c')
    d.rounded_rectangle([110, y, 190, y + lane_h], radius=34, fill=c)
    d.rectangle([160, y, 190, y + lane_h], fill=c)
    d.rounded_rectangle([240, y + 40, 240 + [520, 430, 560, 380][i], y + lane_h - 40], radius=22, fill='#e8eeee')
img.save('assets/meet-board.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save('assets/meet-board-256.png')
print('ok')
