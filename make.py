"""YouTube thumbnails (1280x720) for the @PrattAquaticCenter scheduled streams. Renders HTML with Playwright."""
import base64, json, sys
from pathlib import Path
from PIL import Image

L = Path("/logos")
LOGO = {
    "Tooele": "png/4A R11 Tooele.png", "Fremont": "by-code/FREM.png", "Ben Lomond": "png/3A R12 Ben Lomond.png",
    "Deseret Peak": "png/4A R11 Deseret Peak.png", "Grantsville": "png/3A R12 Grantsville.png",
    "Stansbury": "png/4A R11 Stansbury.png", "Judge": "png/4A R10 Judge Memorial.png", "Sky View": "png/4A R11 Sky View.png",
}
MEETS = [  # file, video id, visitors, host, big title (None = "A @ B"), subtitle, date
    ("2026-11-12 Fremont @ Tooele", "OG0akD78C1g", ["Fremont"], "Tooele", None, None, "THU · NOV 12, 2026"),
    ("2026-11-13 Ben Lomond & Deseret Peak @ Grantsville", "ELtdd71h-eU", ["Ben Lomond", "Deseret Peak"], "Grantsville", None, None, "FRI · NOV 13, 2026"),
    ("2026-11-20 South Region Invite", "Sa4mgIrzvEQ", [], "Tooele", "SOUTH REGION INVITE", "HOSTED BY TOOELE", "FRI · NOV 20, 2026"),
    ("2026-11-21 Stansbury Family Swim Meet", "aaj3tdpB7n4", [], "Stansbury", "FAMILY SWIM MEET", "STANSBURY", "SAT · NOV 21, 2026"),
    ("2026-12-01 Westfield & Deseret Peak @ Stansbury", "vfB3jjlJD_c", ["Westfield", "Deseret Peak"], "Stansbury", None, None, "TUE · DEC 1, 2026"),
    ("2026-12-03 Judge & Stansbury @ Deseret Peak", "5jipNTUuBKE", ["Judge", "Stansbury"], "Deseret Peak", None, None, "THU · DEC 3, 2026"),
    ("2026-12-15 West Jordan & Stansbury @ Deseret Peak", "B0KNr8Gebik", ["West Jordan", "Stansbury"], "Deseret Peak", None, None, "TUE · DEC 15, 2026"),
    ("2026-12-18 Mel Roberts Invitational Day 1", "fMFEMHMIP1w", [], "Tooele", "MEL ROBERTS INVITATIONAL", "DAY 1", "FRI · DEC 18, 2026"),
    ("2026-12-19 Mel Roberts Invitational Day 2", "stoJ8TEeTEk", [], "Tooele", "MEL ROBERTS INVITATIONAL", "DAY 2", "SAT · DEC 19, 2026"),
    ("2027-01-14 Sky View @ Tooele", "ecoBguhyVdw", ["Sky View"], "Tooele", None, None, "THU · JAN 14, 2027"),
    ("2027-01-16 Tooele County Quad Meet", "cGKK0WE5WWU", ["Stansbury", "Grantsville", "Deseret Peak"], "Tooele", "TOOELE COUNTY QUAD", None, "SAT · JAN 16, 2027"),
]


def tile(name, size):
    f = LOGO.get(name)
    if not f:   # no logo on file: lettered badge
        ini = "".join(w[0] for w in name.split())[:2]
        return f'<div class="tile txt" style="width:{size}px;height:{size}px"><b style="font-size:{int(size*.42)}px">{ini}</b><span>{name.upper()}</span></div>'
    im = Image.open(L / f).convert("RGBA")
    bg = "#%02x%02x%02x" % im.getpixel((2, 2))[:3]
    b64 = base64.b64encode((L / f).read_bytes()).decode()
    return f'<div class="tile" style="width:{size}px;height:{size}px;background:{bg}"><img src="data:image/png;base64,{b64}"></div>'


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Anton&family=Barlow+Condensed:wght@600;800&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{width:1280px;height:720px;overflow:hidden;font-family:'Barlow Condensed',sans-serif;color:#fff;
 background:radial-gradient(ellipse at 30% -10%,#38c5f0 0%,#0b6fb0 38%,#063a6b 70%,#03213f 100%)}
.water{position:absolute;inset:0;opacity:.18;background:
 repeating-linear-gradient(115deg,transparent 0 60px,rgba(255,255,255,.55) 64px,transparent 72px 150px),
 repeating-linear-gradient(65deg,transparent 0 90px,rgba(255,255,255,.4) 94px,transparent 100px 210px)}
.lane{position:absolute;left:-20px;right:-20px;height:16px;border-radius:8px;opacity:.9;
 background:repeating-linear-gradient(90deg,#e11d2e 0 22px,#fff 22px 44px,#1d4ed8 44px 66px,#fff 66px 88px)}
.top{position:absolute;top:34px;left:46px;display:flex;gap:16px;align-items:center}
.live{background:#e11d2e;font-family:Anton;font-size:40px;padding:2px 18px 4px;border-radius:8px;letter-spacing:2px;
 display:flex;align-items:center;gap:10px}.live i{width:16px;height:16px;border-radius:50%;background:#fff;display:block}
.kick{font-weight:800;font-size:40px;letter-spacing:3px;text-shadow:0 3px 8px rgba(0,0,0,.4)}
.logos{position:absolute;top:118px;left:0;right:0;display:flex;justify-content:center;align-items:center;gap:26px}
.tile{border-radius:28px;display:flex;align-items:center;justify-content:center;padding:14px;
 box-shadow:0 10px 30px rgba(0,0,0,.45);border:5px solid #fff}
.tile img{max-width:100%;max-height:100%;object-fit:contain}
.tile.txt{background:#0f172a;flex-direction:column;gap:2px}.tile.txt b{font-family:Anton;line-height:1}
.tile.txt span{font-size:22px;font-weight:800;letter-spacing:1px;text-align:center}
.sep{font-family:Anton;font-size:90px;text-shadow:0 4px 12px rgba(0,0,0,.5)}
.title{position:absolute;left:40px;right:40px;bottom:118px;text-align:center;font-family:Anton;line-height:1.02;
 text-transform:uppercase;text-shadow:0 5px 0 #03213f,0 8px 24px rgba(0,0,0,.6)}
.sub{font-family:'Barlow Condensed';font-weight:800;font-size:46px;letter-spacing:4px;color:#ffd23f;display:block;margin-top:2px}
.date{position:absolute;bottom:34px;left:50%;transform:translateX(-50%);background:#ffd23f;color:#03213f;font-family:Anton;
 font-size:46px;padding:4px 30px 6px;border-radius:10px;white-space:nowrap;box-shadow:0 6px 18px rgba(0,0,0,.4)}
.venue{position:absolute;top:40px;right:46px;font-weight:800;font-size:30px;letter-spacing:2px;text-align:right;opacity:.92;line-height:1.05}
"""


def page(m):
    _, vid, vis, host, big, sub, date = m
    teams = vis + [host] if vis else ([host] if not big or "QUAD" not in big else vis)
    if big and "QUAD" in big:
        teams = ["Tooele", "Stansbury", "Grantsville", "Deseret Peak"]
    n = len(teams)
    size = {1: 300, 2: 260, 3: 220, 4: 200}[n]
    parts = []
    for i, t in enumerate(teams):
        if i:
            quad = bool(big and "QUAD" in big)
            parts.append('<div class="sep">%s</div>' % ("&" if quad else "@" if (vis and i == n - 1) else "&"))
        parts.append(tile(t, size))
    if not big:
        big = (" & ".join(vis) + " @ " + host)
    fs = 112 if len(big) <= 18 else 96 if len(big) <= 24 else 80 if len(big) <= 32 else 66
    subhtml = f'<span class="sub">{sub}</span>' if sub else ""
    lane_y = max(118 + size + 30, 410)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div class="water"></div><div class="lane" style="top:{lane_y}px;opacity:.55"></div>
<div class="top"><div class="live"><i></i>LIVE</div><div class="kick">HIGH SCHOOL SWIMMING</div></div>
<div class="venue">PRATT AQUATIC<br>CENTER</div>
<div class="logos">{''.join(parts)}</div>
<div class="title" style="font-size:{fs}px">{big}{subhtml}</div>
<div class="date">{date}</div></body></html>"""


if __name__ == "__main__":
    from playwright.sync_api import sync_playwright
    out = Path("/out"); out.mkdir(exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1280, "height": 720})
        for m in MEETS:
            pg.set_content(page(m)); pg.wait_for_timeout(700)
            pg.screenshot(path=str(out / (m[0] + ".png")))
            Image.open(out / (m[0] + ".png")).convert("RGB").save(out / (m[0] + ".jpg"), quality=90)
            (out / (m[0] + ".png")).unlink()
            print("ok", m[0])
        b.close()
    json.dump([{"file": m[0] + ".jpg", "video": m[1]} for m in MEETS], open(out / "index.json", "w"), indent=1)
