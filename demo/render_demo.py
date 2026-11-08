"""Rebuild demo/demo.mp4 and demo/demo.gif from a real run of the offline replay.

Run: python3 demo/render_demo.py   (needs ffmpeg, Pillow, the DejaVu fonts and pytest)

The terminal text is the captured output of REPLAY and TESTS, run in the repo at build time.
Only the captions and the two cards are written here; each caption's decision is checked
against the real decision line, and every wrapped line against the real line, before drawing.
"""
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "demo"
W, H, FPS, GIF_FPS, GIF_W = 1280, 720, 30, 10, 800
REPLAY = "python3 invoice_gate.py --replay --today 2026-11-07"
TESTS = "python3 -m pytest -q"
# caption shown as each sample starts: (label, verdict, the decision the verdict claims)
SAMPLES = {
    "01_clean": ("01 clean: ", "approve", "APPROVE"),
    "02_amount_mismatch": ("02 amount mismatch: ", "hold", "HOLD"),
    "03_injected": ("03 injected text: ", "stopped at Guard", "HOLD"),
    "04_personal_data": ("04 personal data: ", "approve, nothing personal in the log", "APPROVE"),
    "05_overdue": ("05 overdue: ", "hold for a person", "HOLD"),
    "06_payee_no_mail": ("06 payee domain takes no mail: ", "hold", "HOLD"),
}
INTRO = ("6 invoices, each with its purchase order", "", None)
OUTRO = ("the tests, no network", "", None)

BG, WIN, BAR = (13, 17, 23), (6, 8, 12), (33, 38, 45)
FG, DIM, WHITE = (201, 209, 217), (125, 133, 144), (240, 246, 252)
GREEN, AMBER = (86, 211, 100), (227, 179, 65)
COLOR = {"APPROVE": GREEN, "HOLD": AMBER}

# Pillow finds these by file name in the system font folders
MONO, MONO_B = ImageFont.truetype("DejaVuSansMono.ttf", 28), ImageFont.truetype("DejaVuSansMono-Bold.ttf", 28)
SANS, SANS_B = ImageFont.truetype("DejaVuSans.ttf", 40), ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
BIG, SMALL = ImageFont.truetype("DejaVuSans-Bold.ttf", 100), ImageFont.truetype("DejaVuSans.ttf", 32)
BAR_FONT = ImageFont.truetype("DejaVuSans.ttf", 18)

CAP_H, BAR_H, PAD = 88, 34, 20
WX0, WY0, WX1, WY1 = 24, CAP_H, W - 24, H - 20  # terminal window
CX0, CY0 = WX0 + PAD, WY0 + BAR_H + 12  # text area
CW, CH = WX1 - PAD - CX0, WY1 - 12 - CY0
CHAR_W, LH = MONO.getlength("M"), 36
COLS, ROWS = int(CW // CHAR_W), CH // LH


def run(cmd):
    """Real stdout and stderr of one command in the repo; COLUMNS gives pytest the terminal width."""
    done = subprocess.run(cmd.split(), cwd=REPO, env=dict(os.environ, COLUMNS=str(COLS)),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
    return done.stdout.splitlines()


def kind(line):
    if line.startswith("== "):
        return "head"
    if line.startswith("decision: "):
        return line.split()[1].rstrip(";")  # APPROVE or HOLD
    return "out"


def check_captions(lines):
    names = [line[3:] for line in lines if kind(line) == "head"]
    assert names == list(SAMPLES), f"samples changed: {names}"
    sample = None
    for line in lines:
        if kind(line) == "head":
            sample = line[3:]
        elif line.startswith("decision: "):
            assert kind(line) == SAMPLES[sample][2], f"caption for {sample} no longer matches: {line}"


def rows_of(lines):
    """Wrap at spaces like fold -s; a prompt keeps its trailing space for the cursor."""
    rows = []
    for text, k in lines:
        wrapped = [text] if k == "prompt" else textwrap.wrap(text, COLS, break_on_hyphens=False) or [""]
        assert k == "prompt" or " ".join(wrapped) == text, f"wrapping changed a line: {text}"
        rows += [(r, k) for r in wrapped]
    return rows


def card(lines):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    for text, f, color, y in lines:
        d.text((W / 2, y), text, font=f, fill=color, anchor="mm")
    y = lines[0][3] + 66
    d.rectangle((W / 2 - 60, y, W / 2 + 60, y + 4), fill=GREEN)
    return im


def chrome():
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((WX0, WY0, WX1, WY1), 12, fill=WIN, outline=BAR, width=2)
    d.rounded_rectangle((WX0, WY0, WX1, WY0 + BAR_H), 12, fill=BAR, corners=(True, True, False, False))
    d.text((W / 2, WY0 + BAR_H / 2), "invoice-gate", font=BAR_FONT, fill=DIM, anchor="mm")
    return im


CHROME = chrome()


def terminal(rows, top, caption, cursor):
    im = CHROME.copy()
    d = ImageDraw.Draw(im)
    label, verdict, decision = caption
    x = (W - SANS_B.getlength(label + verdict)) / 2
    d.text((x, CAP_H / 2), label, font=SANS_B, fill=WHITE if verdict else DIM, anchor="lm")
    if verdict:
        d.text((x + SANS_B.getlength(label), CAP_H / 2), verdict, font=SANS_B, fill=COLOR[decision], anchor="lm")
    view = Image.new("RGB", (CW, CH), WIN)
    v = ImageDraw.Draw(view)
    for i in range(top, min(len(rows), top + ROWS)):
        text, k = rows[i]
        y = (i - top) * LH + 2
        if k == "prompt":
            v.text((0, y), "$", font=MONO_B, fill=GREEN)
            v.text((2 * CHAR_W, y), text[2:], font=MONO, fill=WHITE)
        else:
            v.text((0, y), text, font=MONO if k == "out" else MONO_B,
                   fill={"out": FG, "head": WHITE}.get(k) or COLOR[k])
    if cursor:
        y = (len(rows) - 1 - top) * LH
        x = len(rows[-1][0]) * CHAR_W
        v.rectangle((x, y + 4, x + CHAR_W - 2, y + LH - 2), fill=FG)
    im.paste(view, (CX0, CY0))
    return im


def terminal_segments(replay, tests):
    """(duration, render(t_in_segment, t_in_video), mark) for the terminal part."""
    segs, lines, state = [], [], {"caption": INTRO}

    def show(dur, cursor=False, typing=False, mark=None):
        rows, caption = rows_of(lines), state["caption"]
        top = max(0, len(rows) - ROWS)  # scrolls in whole rows, like a terminal

        def render(t, tv):
            return terminal(rows, top, caption, cursor and (typing or tv % 1.0 < 0.5))
        segs.append((dur, render, mark))

    def type_command(cmd):
        lines.append(["$ ", "prompt"])
        show(0.6, cursor=True)
        for i, ch in enumerate(cmd):
            lines[-1][0] += ch
            show(0.045 + (i * 37 % 7) * 0.006, cursor=True, typing=True)  # fixed, uneven keystrokes
        show(0.35, cursor=True, typing=True)

    type_command(REPLAY)
    sample = None
    for line in replay:
        k = kind(line)
        lines.append([line, k])
        if k == "head":
            sample = line[3:]
            state["caption"] = SAMPLES[sample]
            show(0.9, mark="caption " + sample)
        elif k == "out":
            show(0.3 + min(len(line), 140) / 140)  # skim pace, long lines capped
        else:
            show(2.4, mark="decision " + sample)  # held long enough to read
    state["caption"] = OUTRO
    type_command(TESTS)
    for line in tests:
        lines.append([line, "out"])
        show(0.5, mark="tests" if " passed" in line else None)
    lines.append(["$ ", "prompt"])
    show(2.2, cursor=True)
    return segs


def still(im):
    return lambda t, tv: im


def blend(a, b, dur):
    return lambda t, tv: Image.blend(a, b, min(1.0, t / dur))


def main():
    replay, tests = run(REPLAY), run(TESTS)
    check_captions(replay)
    title = card([("invoice-gate", BIG, WHITE, 300), ("pay, hold or stop an invoice with Clocked", SANS, FG, 420)])
    end = card([("invoice-gate", BIG, WHITE, 260), ("MIT license", SANS, FG, 380),
                ("offline replay of real Clocked answers, 8 Nov 2026", SMALL, DIM, 450)])
    term = terminal_segments(replay, tests)
    t0 = 3.5  # title and crossfade
    t1 = t0 + sum(s[0] for s in term)
    first, last = term[0][1](0, t0), term[-1][1](term[-1][0], t1)
    segs = [(3.0, still(title), "title"), (0.5, blend(title, first, 0.5), None), *term,
            (0.5, blend(last, end, 0.5), None), (3.0, still(end), "end card")]

    tmp = Path(tempfile.mkdtemp())
    src, pal, mp4, gif = tmp / "gif_src.mkv", tmp / "palette.png", OUT / "demo.mp4", OUT / "demo.gif"
    ff = ["ffmpeg", "-v", "error", "-y"]
    enc = subprocess.Popen(ff + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-framerate", str(FPS),
                                 "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                                 "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                                 "-force_key_frames", f"{t0},{t1 + 0.5}", str(mp4),  # clean frames after each fade
                                 "-an", "-vf", f"fps={GIF_FPS},scale={GIF_W}:-2:flags=lanczos", "-c:v", "ffv1",
                                 "-pix_fmt", "bgr0",
                                 str(src)], stdin=subprocess.PIPE)
    clock, n, marks = 0.0, 0, []
    for dur, render, mark in segs:
        if mark:
            marks.append(f"{clock:6.2f} s  {mark}")
        start, clock = clock, clock + dur
        while n < round(clock * FPS):
            enc.stdin.write(render(n / FPS - start, n / FPS).tobytes())
            n += 1
    enc.stdin.close()
    assert enc.wait() == 0, "ffmpeg failed"
    subprocess.run(ff + ["-i", str(src), "-vf", "palettegen=max_colors=64", str(pal)], check=True)
    subprocess.run(ff + ["-i", str(src), "-i", str(pal), "-lavfi", "paletteuse=dither=none:diff_mode=rectangle",
                         str(gif)], check=True)
    for f in (src, pal):
        f.unlink()
    tmp.rmdir()
    print(f"{n} frames, {n / FPS:.2f} s at {FPS} fps; terminal {COLS} columns x {ROWS} rows")
    print("\n".join(marks))
    print(f"demo.mp4 {mp4.stat().st_size} bytes, demo.gif {gif.stat().st_size} bytes")
    assert 45 <= n / FPS <= 90, "duration outside 45 to 90 s"
    assert mp4.stat().st_size < 10e6 and gif.stat().st_size < 6e6, "file too big"


if __name__ == "__main__":
    main()
