"""The opening intro and the page transitions.

Intro (about 3.5 s): the neon shield draws itself, "ReRoute" rises letter by letter, the tagline fades in, four
safety checks tick on (PREFLIGHT, REGISTRY, DETECTOR, RULES), then the screen slides up to reveal the site.
It plays when the page is opened or refreshed, and when someone logs in or out. Click or press Esc to skip.

Page transition: clicking to another page slides the new page in (from the right when moving down the menu,
from the left when moving up) with a thin neon line sweeping across the top.

How it works: a tiny invisible component adds this to the page (like the login cookie writer in common.py). It
keeps short notes on the open page (wiped by a refresh) to know whether this run is a refresh, a log in or out,
or a page change. People who turn off animations in their device settings get no motion.
Interface only: it reads nothing and changes nothing.
"""

import streamlit.components.v1 as components

LETTERS = [("R", "#00e0ea"), ("e", "#00d4ee"), ("R", "#05c4f3"), ("o", "#0bb2f8"), ("u", "#4b8ffb"),
           ("t", "#9a6ff2"), ("e", "#ee50e6")]
CHECKS = ["PREFLIGHT", "REGISTRY", "DETECTOR", "RULES"]

CSS = """
#rr-intro { position: fixed; inset: 0; z-index: 2147483000; background: #050b16; color: #e4f1f5;
  display: flex; align-items: center; justify-content: center; overflow: hidden; cursor: pointer;
  font-family: 'Manrope', system-ui, sans-serif; transition: transform .75s cubic-bezier(.76,0,.24,1), opacity .75s; }
#rr-intro.out { transform: translateY(-100%); opacity: .2; pointer-events: none; }
#rr-intro { animation: rrSafetyOut .75s 5s forwards; }   /* backup: always leaves the screen, even if a timer fails */
@keyframes rrSafetyOut { to { transform: translateY(-100%); opacity: 0; visibility: hidden; pointer-events: none; } }
#rr-intro .grid { position: absolute; inset: -40px; opacity: 0; animation: rrFade 1.2s .1s forwards;
  background-image: linear-gradient(rgba(0,224,234,.06) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(0,224,234,.06) 1px, transparent 1px);
  background-size: 48px 48px; }
#rr-intro .glow { position: absolute; width: 70vmax; height: 70vmax; border-radius: 50%; opacity: 0;
  background: radial-gradient(circle, rgba(17,168,255,.22), rgba(238,80,230,.08) 45%, transparent 70%);
  animation: rrFade 1.6s .2s forwards; }
#rr-intro .scan { position: absolute; left: 0; right: 0; height: 2px; top: -5%; opacity: .55;
  background: linear-gradient(90deg, transparent, #00e0ea, transparent);
  box-shadow: 0 0 18px #00e0ea; animation: rrScan 2.4s .3s ease-in-out forwards; }
#rr-intro .center { position: relative; display: flex; flex-direction: column; align-items: center;
  gap: clamp(10px, 1.6vh, 18px); padding: 0 16px; text-align: center; }
#rr-intro svg { width: clamp(84px, 16vmin, 140px); height: auto; overflow: visible;
  filter: drop-shadow(0 0 18px rgba(0,224,234,.55)); animation: rrPulse 1.4s 1.6s ease-in-out; }
#rr-intro .shield { fill: rgba(6,20,42,.0); stroke: url(#rrg); stroke-width: 2.6; stroke-linejoin: round;
  stroke-dasharray: 140; stroke-dashoffset: 140; animation: rrDraw 1s .2s cubic-bezier(.65,0,.35,1) forwards,
  rrFill .6s 1.05s forwards; }
#rr-intro .path { fill: none; stroke: #00e0ea; stroke-width: 3.4; stroke-linecap: round; stroke-linejoin: round;
  stroke-dasharray: 40; stroke-dashoffset: 40; animation: rrDraw .6s .9s ease-out forwards; }
#rr-intro .head { fill: none; stroke: #00e0ea; stroke-width: 3.4; stroke-linecap: round; stroke-linejoin: round;
  stroke-dasharray: 16; stroke-dashoffset: 16; animation: rrDraw .3s 1.35s ease-out forwards; }
#rr-intro .dot { fill: #ee50e6; transform-origin: 35.5px 12.5px; transform: scale(0);
  animation: rrPop .35s 1.55s cubic-bezier(.34,1.56,.64,1) forwards; }
#rr-intro .word { display: flex; font-family: 'Chakra Petch', 'Segoe UI', sans-serif; font-weight: 700;
  font-size: clamp(40px, 9vmin, 84px); line-height: 1; letter-spacing: .01em; padding: 6px 12px; }
#rr-intro .word span { display: inline-block; transform: translateY(60%); opacity: 0;
  animation: rrRise .55s calc(1.0s + var(--i) * .07s) cubic-bezier(.22,1,.36,1) forwards;
  text-shadow: 0 0 16px color-mix(in srgb, currentColor 55%, transparent); }
#rr-intro .tag { font-size: clamp(14px, 2.4vmin, 20px); color: #b7c7d0; opacity: 0; transform: translateY(8px);
  animation: rrUp .6s 1.6s ease-out forwards; }
#rr-intro .tag b { color: #e4f1f5; font-weight: 700; }
#rr-intro .checks { display: flex; flex-wrap: wrap; justify-content: center; gap: 6px 18px; margin-top: 4px;
  font-family: 'JetBrains Mono', Consolas, monospace; font-size: clamp(10px, 1.6vmin, 13px); letter-spacing: .16em; }
#rr-intro .checks span { color: #4d6270; opacity: 0; animation: rrFade .2s calc(1.9s + var(--i) * .22s) forwards,
  rrOn .25s calc(2.05s + var(--i) * .22s) forwards; }
#rr-intro .checks span::after { content: " \\2713"; color: transparent; }
#rr-intro .checks span.on::after { color: inherit; }
#rr-intro .bar { width: clamp(160px, 34vmin, 300px); height: 2px; background: rgba(0,224,234,.15); border-radius: 2px;
  overflow: hidden; margin-top: 2px; }
#rr-intro .bar i { display: block; height: 100%; width: 0; background: linear-gradient(90deg,#00e0ea,#11a8ff,#ee50e6);
  box-shadow: 0 0 10px #00e0ea; animation: rrBar 1.3s 1.9s cubic-bezier(.65,0,.35,1) forwards; }
#rr-intro .skip { position: absolute; bottom: 22px; left: 0; right: 0; text-align: center; color: #4d6270;
  font-family: 'JetBrains Mono', Consolas, monospace; font-size: 11px; letter-spacing: .2em; opacity: 0;
  animation: rrFade .5s .6s forwards; }
@keyframes rrFade { to { opacity: 1; } }
@keyframes rrDraw { to { stroke-dashoffset: 0; } }
@keyframes rrFill { to { fill: rgba(6,20,42,.9); } }
@keyframes rrPop { to { transform: scale(1); } }
@keyframes rrRise { to { transform: translateY(0); opacity: 1; } }
@keyframes rrUp { to { transform: translateY(0); opacity: 1; } }
@keyframes rrOn { to { color: #51e57e; text-shadow: 0 0 10px rgba(81,229,126,.6); } }
@keyframes rrBar { to { width: 100%; } }
@keyframes rrScan { 0% { top: -5%; opacity: 0; } 15% { opacity: .55; } 100% { top: 105%; opacity: 0; } }
@keyframes rrPulse { 50% { filter: drop-shadow(0 0 34px rgba(0,224,234,.9)); } }
@media (prefers-reduced-motion: reduce) {
  #rr-intro *, #rr-intro *::after { animation-duration: .01s !important; animation-delay: 0s !important; }
  #rr-intro { transition-duration: .3s; }
}
"""


def _html() -> str:
    letters = "".join(f'<span style="--i:{i};color:{c}">{ch}</span>' for i, (ch, c) in enumerate(LETTERS))
    checks = "".join(f'<span style="--i:{i}">{c}</span>' for i, c in enumerate(CHECKS))
    return (
        '<div class="grid"></div><div class="glow"></div><div class="scan"></div>'
        '<div class="center">'
        '<svg viewBox="0 0 48 48" aria-hidden="true"><defs><linearGradient id="rrg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#00e0ea"/><stop offset="1" stop-color="#11a8ff"/></linearGradient></defs>'
        '<path class="shield" d="M24 3.5 L41.5 9.5 V22 C41.5 33.2 34.3 41 24 44.5 C13.7 41 6.5 33.2 6.5 22 V9.5 Z"/>'
        '<path class="path" d="M16 32 V25 C16 20.5 18.8 18.5 22.5 18.5 H30.5"/>'
        '<path class="head" d="M26.5 14 L31.5 18.5 L26.5 23"/>'
        '<circle class="dot" cx="35.5" cy="12.5" r="3"/></svg>'
        f'<div class="word" aria-label="ReRoute">{letters}</div>'
        '<div class="tag"><b>Stop scams.</b> Never freeze a wallet.</div>'
        f'<div class="checks">{checks}</div><div class="bar"><i></i></div>'
        '</div><div class="skip">CLICK OR PRESS ESC TO SKIP</div>')


# Runs inside the page itself (not the hidden component frame), so it keeps working even when Streamlit
# replaces the frame on the next run.
PARENT_CODE = """
(function () {
  var P = window, D = document;
  var TOKEN = %TOKEN%, PAGE = %PAGE%, IDX = %IDX%;
  var reduce = P.matchMedia && P.matchMedia('(prefers-reduced-motion: reduce)').matches;
  // These notes live on the open page, so a refresh wipes them: refresh = intro again.
  var prevToken = P.__rrIntroToken, prevPage = P.__rrPage, prevIdx = P.__rrPageIdx;
  P.__rrPage = PAGE; P.__rrPageIdx = IDX;
  if (prevToken === undefined || prevToken !== TOKEN) {   // first load / refresh / logged in or out
    P.__rrIntroToken = TOKEN;
    if (!D.getElementById('rr-intro')) playIntro();       // already playing (e.g. remembered login): keep it
    return;
  }
  if (prevPage !== undefined && prevPage !== PAGE && !D.getElementById('rr-intro')) {
    transition(IDX >= prevIdx ? 1 : -1);                    // clicked to another page
  }

  function transition(dir) {
    if (reduce) return;
    var main = D.querySelector('[data-testid="stMainBlockContainer"]') || D.querySelector('.block-container');
    if (main && main.animate) {
      main.animate([{ opacity: 0, transform: 'translateX(' + (dir * 46) + 'px)', filter: 'blur(5px)' },
                    { opacity: 1, transform: 'none', filter: 'none' }],
                   { duration: 460, easing: 'cubic-bezier(.22,1,.36,1)' });
    }
    var bar = D.createElement('div');
    bar.style.cssText = 'position:fixed;top:0;height:2px;z-index:2147482000;pointer-events:none;' +
      (dir > 0 ? 'left:0;' : 'right:0;') + 'width:0;background:linear-gradient(90deg,#00e0ea,#11a8ff,#ee50e6);' +
      'box-shadow:0 0 12px #00e0ea;';
    D.body.appendChild(bar);
    if (bar.animate) {
      var a = bar.animate([{ width: '0%', opacity: 1 }, { width: '100%', opacity: 1, offset: .7 },
                           { width: '100%', opacity: 0 }], { duration: 650, easing: 'ease-out' });
      a.onfinish = function () { bar.remove(); };
    } else { bar.remove(); }
  }

  function playIntro() {
  var style = D.createElement('style'); style.id = 'rr-intro-style'; style.textContent = %CSS%;
  D.head.appendChild(style);
  var el = D.createElement('div'); el.id = 'rr-intro'; el.setAttribute('role', 'presentation');
  el.innerHTML = %HTML%; D.body.appendChild(el);
  var checks = el.querySelectorAll('.checks span');
  checks.forEach(function (c, i) { P.setTimeout(function () { c.classList.add('on'); }, 2050 + i * 220); });
  var finished = false;
  function done() {
    if (finished) return; finished = true;
    el.classList.add('out');
    P.setTimeout(function () { el.remove(); style.remove(); }, 800);
  }
  el.addEventListener('click', done);
  D.addEventListener('keydown', function (e) { if (e.key === 'Escape') done(); });
  P.setTimeout(done, reduce ? 500 : 3500);
  }
})();
"""

# The hidden component frame only hands PARENT_CODE to the page and runs it there.
SCRIPT = """
<script>
(function () {
  var D = window.parent.document, s = D.createElement('script');
  s.textContent = %CODE%;
  D.head.appendChild(s);
  s.remove();
})();
</script>
"""


def play(token: int, page: str, index: int) -> None:
    """Add the intro / page transition helper to the page. Safe to call on every run.

    token: changes when someone logs in or out (the intro plays again).
    page, index: the current page and its position in the menu (a page change slides the new page in, from
    the right when moving down the menu and from the left when moving up). A refresh always plays the intro.
    """
    import json
    code = (PARENT_CODE.replace("%CSS%", json.dumps(CSS)).replace("%HTML%", json.dumps(_html()))
            .replace("%TOKEN%", json.dumps(int(token))).replace("%PAGE%", json.dumps(str(page)))
            .replace("%IDX%", json.dumps(int(index))))
    components.html(SCRIPT.replace("%CODE%", json.dumps(code).replace("</", "<\\/")), height=0)
