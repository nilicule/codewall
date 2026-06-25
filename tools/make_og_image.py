"""Render the social-share (Open Graph) card to static/og-image.png.

On-brand with the dashboard: dark grid-night ops wall, the green telemetry-bar
mark from the favicon, a live pip, and the org-activity headline. 1200x630 is
the canonical OG size Slack/Twitter/LinkedIn crop to.
"""
import pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parents[1] / "static" / "og-image.png"

HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@500&family=Inter:wght@400;500&display=swap');
:root{
  --void:#070b12;--void2:#0a1018;--panel:#0d141e;--hairline:#1b2735;
  --hairline2:#243446;--ember:#ff8a3d;--flux:#36d6e7;--fused:#a98bff;
  --grid-green:#3ddc84;--ink:#eaf2ff;--mute:#5d7088;
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1200px;height:630px}
body{
  background:var(--void);color:var(--ink);position:relative;overflow:hidden;
  font-family:"Inter",system-ui,sans-serif;
}
/* faint telemetry grid */
.grid{position:absolute;inset:0;
  background-image:
    linear-gradient(var(--hairline) 1px,transparent 1px),
    linear-gradient(90deg,var(--hairline) 1px,transparent 1px);
  background-size:64px 64px;opacity:.5;
  -webkit-mask-image:radial-gradient(ellipse 80% 75% at 38% 42%,#000 30%,transparent 92%);}
/* galaxy core glow on the right, echoing the hero constellation */
.glow{position:absolute;right:-180px;top:50%;transform:translateY(-50%);
  width:760px;height:760px;border-radius:50%;
  background:radial-gradient(circle,rgba(110,86,170,.32),rgba(54,214,231,.10) 42%,transparent 70%);
  filter:blur(8px);}
.wrap{position:absolute;inset:0;padding:74px 80px;display:flex;flex-direction:column;justify-content:space-between;z-index:2}
.top{display:flex;align-items:center;justify-content:space-between}
.brand{display:flex;align-items:center;gap:20px}
.mark{width:74px;height:74px;border-radius:16px;background:var(--void2);
  border:1px solid var(--hairline2);display:flex;align-items:flex-end;
  justify-content:center;gap:6px;padding:16px 0 16px}
.mark i{display:block;width:8px;border-radius:4px;background:var(--grid-green);
  box-shadow:0 0 14px rgba(61,220,132,.7)}
.mark i:nth-child(1){height:26px}.mark i:nth-child(2){height:46px}
.mark i:nth-child(3){height:34px}.mark i:nth-child(4){height:40px}
.brand .org{font-family:"JetBrains Mono",monospace;font-size:22px;letter-spacing:.32em;color:var(--mute);text-transform:uppercase}
.live{display:flex;align-items:center;gap:12px;font-family:"JetBrains Mono",monospace;
  font-size:19px;letter-spacing:.2em;color:var(--mute)}
.live .pip{width:13px;height:13px;border-radius:50%;background:var(--grid-green);box-shadow:0 0 16px var(--grid-green)}
h1{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:96px;
  line-height:.98;letter-spacing:-.01em;text-transform:uppercase}
h1 .b{color:var(--grid-green)}
.sub{margin-top:26px;font-size:27px;color:#aebbd0;max-width:760px;line-height:1.45;font-weight:400}
.legend{display:flex;gap:34px;font-family:"JetBrains Mono",monospace;font-size:20px;color:var(--mute);letter-spacing:.04em}
.legend span{display:flex;align-items:center;gap:11px}
.dot{width:13px;height:13px;border-radius:50%}
.host{font-family:"JetBrains Mono",monospace;font-size:20px;color:var(--mute);letter-spacing:.08em}
</style></head>
<body>
  <div class="grid"></div><div class="glow"></div>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <div class="mark"><i></i><i></i><i></i><i></i></div>
        <div class="org">NET2GRID</div>
      </div>
      <div class="live"><span class="pip"></span>LIVE</div>
    </div>
    <div>
      <h1>Org<br>Activity <span class="b">Wall</span></h1>
      <div class="sub">A live constellation of every commit and pull request across the org &mdash; streaming in real time over a 90-day window.</div>
    </div>
    <div class="top" style="align-items:flex-end">
      <div class="legend">
        <span><i class="dot" style="background:var(--ember);box-shadow:0 0 10px var(--ember)"></i>commits</span>
        <span><i class="dot" style="background:var(--flux);box-shadow:0 0 10px var(--flux)"></i>PRs opened</span>
        <span><i class="dot" style="background:var(--fused);box-shadow:0 0 10px var(--fused)"></i>PRs merged</span>
      </div>
      <div class="host">n2g.dev/codewall</div>
    </div>
  </div>
</body></html>
"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1200, "height": 630}, device_scale_factor=2)
    pg.set_content(HTML, wait_until="networkidle")
    pg.wait_for_timeout(400)  # let webfonts settle
    pg.screenshot(path=str(OUT), clip={"x": 0, "y": 0, "width": 1200, "height": 630})
    b.close()
print("wrote", OUT)
