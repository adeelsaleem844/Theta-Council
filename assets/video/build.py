"""Build the demo video, scene by scene.

No screen capture and no voiceover: every frame is rendered from HTML by headless
Chrome, and the narration is burned in as on-screen captions. The terminal
scenes show output captured from real runs (`run_mock.txt`, `mcp.txt`,
`tests.txt`) rather than mocked-up screenshots of a terminal.

    python assets/video/build.py frames      # render the PNGs
    python assets/video/build.py encode      # stitch them with ffmpeg

Scenes are held for a set number of seconds rather than animated, so the cost
is one Chrome render per scene instead of one per frame.
"""
from __future__ import annotations

import html
import os
import pathlib
import re
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FRAMES = HERE / "frames"
W, H = 1920, 1080

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
FFMPEG_CANDIDATES = [
    "ffmpeg",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
]


def find(cands):
    for c in cands:
        if os.path.sep in c or ":" in c:
            if os.path.exists(c):
                return c
        elif shutil.which(c):
            return shutil.which(c)
    return None


# ── the captured terminal text ───────────────────────────────────────────────

def load(name: str) -> str:
    return (HERE / name).read_text(encoding="utf-8", errors="replace")


def section(text: str, start: str, end: str | None = None, pad: int = 0) -> str:
    """Pull the block between two banner markers out of a captured run."""
    lines = text.splitlines()
    try:
        i = next(n for n, l in enumerate(lines) if start in l)
    except StopIteration:
        return ""
    i = max(0, i - pad)
    if end is None:
        return "\n".join(lines[i:])
    try:
        j = next(n for n, l in enumerate(lines[i + 1:], i + 1) if end in l)
    except StopIteration:
        j = len(lines)
    return "\n".join(lines[i:j])


# ── frame templates ──────────────────────────────────────────────────────────

SHELL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
 *{box-sizing:border-box;margin:0}
 html,body{width:%(W)spx;height:%(H)spx;overflow:hidden;background:#0b1220}
 body{font-family:"IBM Plex Sans",system-ui,sans-serif;color:#e5ebf4;
      background-image:linear-gradient(rgba(232,163,61,.035) 1px,transparent 1px),
      linear-gradient(90deg,rgba(232,163,61,.035) 1px,transparent 1px);
      background-size:60px 60px,60px 60px;position:relative}
 .stage{position:absolute;inset:0 0 148px 0;padding:56px 72px;display:flex;
        flex-direction:column;overflow:hidden}
 /* caption bar: the narration, burned in */
 .cap{position:absolute;left:0;right:0;bottom:0;height:148px;
      background:#070c15;border-top:1px solid #1d2836;
      padding:24px 72px;display:flex;align-items:center;gap:26px}
 .cap .bar{width:5px;align-self:stretch;background:#e8a33d;border-radius:3px;flex:none}
 .cap p{font:400 30px/1.36 Newsreader,Georgia,serif;color:#dbe4f0;max-width:52em}
 .brand{position:absolute;right:60px;top:44px;font:500 15px/1 "IBM Plex Mono",monospace;
        letter-spacing:.2em;text-transform:uppercase;color:#3d4a5c}
 .kicker{font:600 17px/1 "IBM Plex Mono",monospace;letter-spacing:.24em;
         text-transform:uppercase;color:#75839a;margin-bottom:30px;flex:none}
 h1{font:400 150px/.95 Newsreader,Georgia,serif;letter-spacing:-.03em;color:#fff}
 h2{font:400 68px/1.08 Newsreader,Georgia,serif;letter-spacing:-.02em;color:#fff;
    max-width:26em}
 h2 em{color:#e8a33d;font-style:italic}
 .rule{width:110px;height:5px;background:#e8a33d;margin:32px 0}
 .sub{font:400 34px/1.4 Newsreader,Georgia,serif;color:#aebbcd;max-width:26em}
 .quote{font:400 italic 66px/1.2 Newsreader,Georgia,serif;color:#fff;max-width:19em;
        border-left:6px solid #e8a33d;padding-left:40px}
 .chips{display:flex;gap:14px;flex-wrap:wrap;margin-top:46px}
 .chip{font:400 21px/1 "IBM Plex Mono",monospace;padding:14px 18px;
       border:1px solid #2a3646;border-radius:6px;color:#aebbcd;background:#101827}
 .chip b{color:#fff;font-weight:500}
 .chip.on{border-color:#3f9c8f;color:#4fb3a3;background:#0f2420}
 /* terminal */
 .term{flex:1;background:#080d16;border:1px solid #1d2836;border-radius:9px;
       display:flex;flex-direction:column;overflow:hidden}
 .tbar{display:flex;align-items:center;gap:9px;padding:14px 20px;background:#101827;
       border-bottom:1px solid #1d2836;flex:none}
 .dot{width:12px;height:12px;border-radius:50%%;background:#2a3646}
 .tbar span{margin-left:12px;font:400 17px/1 "IBM Plex Mono",monospace;color:#5f6d81}
 .tbody{flex:1;padding:22px 26px;overflow:hidden}
 pre{font:400 %(TS)spx/1.5 "IBM Plex Mono",monospace;color:#c3cddd;white-space:pre}
 .pr{color:#4fb3a3}.cm{color:#fff;font-weight:600}
 b.hl{color:#e8a33d;font-weight:500}
 mark{background:none;color:#4fb3a3;font-weight:500}
 .warn{color:#e08472}
</style></head><body>%(BODY)s
<div class="brand">Theta Council</div>
<div class="cap"><span class="bar"></span><p>%(CAP)s</p></div>
</body></html>"""


def frame(body: str, caption: str, term_size: int = 21) -> str:
    return SHELL % {"W": W, "H": H, "BODY": body, "CAP": caption, "TS": term_size}


def title_scene(kicker, heading, sub="", chips=(), quote=False):
    parts = [f'<div class="kicker">{kicker}</div>'] if kicker else []
    if quote:
        parts.append(f'<div class="quote">{heading}</div>')
    else:
        tag = "h1" if len(heading) < 22 else "h2"
        parts.append(f"<{tag}>{heading}</{tag}>")
    if sub:
        parts.append('<div class="rule"></div>')
        parts.append(f'<div class="sub">{sub}</div>')
    if chips:
        parts.append('<div class="chips">' +
                     "".join(f'<span class="chip{" on" if o else ""}">{c}</span>'
                             for c, o in chips) + "</div>")
    inner = "".join(parts)
    return (f'<div class="stage" style="justify-content:center">{inner}</div>')


def term_scene(command, output, term_size=21):
    """Highlight the numbers a viewer should actually look at."""
    body = html.escape(output)
    body = re.sub(r"\b(PASS)\b", r"<mark>\1</mark>", body)
    body = re.sub(r"\b(BLOCK|WARN|FAILED)\b", r'<span class="warn">\1</span>', body)
    body = re.sub(r"(\b1\.\d\d\b)", r'<b class="hl">\1</b>', body)
    body = re.sub(r"(EV \$\s*[+-][\d,]+)", r'<b class="hl">\1</b>', body)
    cmd = html.escape(command)
    return f"""<div class="stage">
      <div class="term">
        <div class="tbar"><span class="dot"></span><span class="dot"></span>
          <span class="dot"></span><span>theta-council</span></div>
        <div class="tbody"><pre><span class="pr">$</span> <span class="cm">{cmd}</span>

{body}</pre></div>
      </div></div>"""


# ── the storyboard ───────────────────────────────────────────────────────────

def storyboard():
    run = load("run_mock.txt")
    mcp = load("mcp.txt")
    tests = load("tests.txt")

    reads = section(run, "symbol reads", "risk officer")
    gates = section(run, "risk officer", "candidates (")
    cands = "\n".join(section(run, "candidates (", "refused (").splitlines()[:12])
    refused = section(run, "refused (", "orders sent")
    orders = section(run, "orders sent", "running totals")
    prov = section(run, "transport provenance", "running totals")
    bindings = section(mcp, "intent bindings", "all advertised tools")
    tests_tail = "\n".join([l for l in tests.splitlines() if l.strip()][-3:])

    S = []
    S.append((10, frame(title_scene(
        "Alpaca AI Trading Agents Hackathon &middot; lablab.ai &times; Alpaca",
        "Theta&nbsp;Council",
        "An autonomous options desk on Alpaca.<br>Five agents, one veto, a full audit trail.",
        (("paper <b>PA3YGPXXKC9E</b>", False), ("start <b>$100,000</b>", False),
         ("options level 3", True), ("<b>0</b> dependencies", False))),
        "Theta Council: an autonomous options desk on Alpaca. Five agents, one "
        "veto, and a full audit trail.")))

    S.append((11, frame(title_scene(
        "The problem",
        "Most AI trading agents hand a model an order endpoint and a prompt that "
        "says <em>be careful</em>."),
        "Most AI trading agents hand a language model an order endpoint and a "
        "prompt that says be careful. That is a nice demo and a bad trading desk.")))

    S.append((11, frame(title_scene(
        "The inversion",
        "The LLM has a veto and a dial. It never has the wheel.", quote=True),
        "So this is built the other way around. The model can reject a trade and "
        "shrink a position &mdash; it can never raise one.")))

    S.append((9, frame(term_scene("python -m council once --mock", ""),
        "One command runs the whole five-agent pipeline offline, with no API "
        "keys, in about two seconds.")))

    S.append((17, frame(term_scene("python -m council once --mock", reads, 23),
        "Every cycle it computes one number per symbol: implied volatility "
        "divided by twenty-day realised volatility.")))

    S.append((13, frame(title_scene(
        "The edge &middot; variance risk premium",
        "Selling an option is selling a <em>forecast of movement</em>.",
        "The market prices that forecast at implied vol. The underlying then "
        "delivers realised vol. On average the first is larger."),
        "That gap is the variance risk premium. It is not a prediction about "
        "direction &mdash; it is a fee, paid to whoever carries the gamma risk.")))

    S.append((13, frame(term_scene("python -m council once --mock", reads, 23),
        "Above 1.12 the desk sells premium. Below 0.98 it buys. In between it "
        "does nothing &mdash; which is most of the time, and is the point.")))

    S.append((16, frame(term_scene("python -m council once --mock", cands, 18),
        "It does not just say vol looks high. It prices the edge in dollars: "
        "expected value, and expected value over the money at risk.")))

    S.append((17, frame(term_scene("python -m council once --mock", gates, 22),
        "Then the Risk Officer runs. Twenty-four deterministic gates, each "
        "reporting its own numbers &mdash; kill switches, delta band, buying power.")))

    S.append((14, frame(term_scene("python -m council once --mock", refused, 20),
        "The trades it refused are on the record too, with the gate that stopped "
        "each one. A desk that only logs its fills is grading its own homework.")))

    S.append((12, frame(title_scene(
        "The leash", "The model may veto, and may shrink. Nothing else."),
        "All of that runs after the language model speaks, in an agent that does "
        "not read prompts. No prompt talks the Risk Officer into a bigger position.")))

    S.append((14, frame(term_scene("python -m council once --mock", orders, 20),
        "Approved structures go out as single multi-leg orders, so no leg is ever "
        "momentarily naked.")))

    S.append((16, frame(term_scene("python -m council mcp-doctor", bindings, 19),
        "It does not hard-code MCP tool names. It boots Alpaca's MCP server, reads "
        "all seventy-two tools and their JSON schemas, and binds to what is there.")))

    S.append((13, frame(term_scene("python -m council once --mock", prov, 22),
        "Every call records which pipe served it. So using the CLI and the MCP "
        "server is a table in the journal, not a claim in a README.")))

    S.append((12, frame(term_scene("python -m unittest discover -s tests", tests_tail, 26),
        "Fifty-two tests. Zero third-party dependencies &mdash; urllib, sqlite3 "
        "and math do all of it.")))

    S.append((12, frame(title_scene(
        "", "Theta&nbsp;Council",
        "Five agents. One veto. A full audit trail.<br>"
        "<span style='font-size:26px'>github.com/adeelsaleem844/Theta-Council "
        "&middot; paper trading only</span>"),
        "Theta Council. Paper trading only &mdash; hypothetical results, and not "
        "investment advice.")))
    return S


# ── render ───────────────────────────────────────────────────────────────────

def build_frames():
    chrome = find(CHROME_CANDIDATES)
    if not chrome:
        sys.exit("Chrome not found - cannot render frames")
    FRAMES.mkdir(parents=True, exist_ok=True)
    board = storyboard()
    plan = []
    for i, (secs, doc) in enumerate(board, 1):
        src = FRAMES / f"s{i:02d}.html"
        png = FRAMES / f"s{i:02d}.png"
        src.write_text(doc, encoding="utf-8")
        subprocess.run([chrome, "--headless", "--disable-gpu", "--no-sandbox",
                        "--hide-scrollbars", f"--window-size={W},{H}",
                        "--default-background-color=0b1220ff",
                        "--virtual-time-budget=5000",
                        f"--screenshot={png}", src.as_uri()],
                       capture_output=True, timeout=120)
        ok = png.exists() and png.stat().st_size > 20_000
        print(f"  s{i:02d}  {secs:>3}s  {'ok' if ok else 'FAILED'}  {png.name}")
        if ok:
            plan.append((png.name, secs))
    total = sum(s for _, s in plan)
    (FRAMES / "concat.txt").write_text(
        "".join(f"file '{n}'\nduration {s}\n" for n, s in plan)
        + f"file '{plan[-1][0]}'\n", encoding="utf-8")
    print(f"\n{len(plan)} scenes, {total}s ({total // 60}m{total % 60:02d}s)")
    return plan


def encode():
    ff = find(FFMPEG_CANDIDATES)
    if not ff:
        sys.exit("ffmpeg not found - install it, then rerun: "
                 "python assets/video/build.py encode")
    out = HERE.parent / "Theta-Council-demo.mp4"
    cmd = [ff, "-y", "-f", "concat", "-safe", "0",
           "-i", str(FRAMES / "concat.txt"),
           "-vf", "fps=25,format=yuv420p,scale=1920:1080:flags=lanczos",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-movflags", "+faststart", str(out)]
    print("  " + " ".join(cmd[:6]) + " ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2500:])
        sys.exit(f"ffmpeg failed rc={r.returncode}")
    mb = out.stat().st_size / 1e6
    print(f"\n  wrote {out}  ({mb:.1f} MB)")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    os.chdir(ROOT)
    if what in ("frames", "all"):
        print("rendering scenes")
        build_frames()
    if what in ("encode", "all"):
        print("\nencoding")
        encode()
