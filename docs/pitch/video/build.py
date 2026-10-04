"""Build the pitch video's HyperFrames composition from scene sources and voice clips.

Scene lengths, captions and music ducking follow the measured voice clips, so swapping the
draft voice for the final one (ElevenLabs) is: replace the WAVs in composition/assets/vo/,
run `python3 build.py`, re-check and re-render. Nothing else is edited by hand.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
SCENES = HERE / "scenes"
COMP = HERE / "composition"
VO = COMP / "assets" / "vo"

# Autumn Day lifts at 37.69 s; the track is offset so that lift lands on the logo in scene 2.
MUSIC = "assets/music/autumn-day.mp3"
MUSIC_LIFT = 37.69
MUSIC_LEN = 184.87
BASE, DUCK = 0.30, 0.11

# id, source, minimum length (s), voice clips [(file stem, offset)], captions per clip, sfx [(file, t, volume)]
S = [
    dict(
        id="s0",
        src="00-cold-open.html",
        min=12.0,
        vo=[("00-cold-open", 2.8)],
        cap={
            "00-cold-open": [
                "11:47 p.m., Mexico City.",
                "Lucía paid 32.50 for dinner.",
                "Her bank just charged her 48.50.",
            ]
        },
        sfx=[("buzz.wav", 1.4, 0.75)],
    ),
    dict(
        id="s1",
        src="01-why.html",
        min=22.4,
        vo=[("01-why", 0.8)],
        cap={
            "01-why": [
                "Today, fixing that means a phone call.",
                "A wait. A seven-minute call.",
                "And more than half the time,",
                "it ends without a solution.",
                "At this bank, disputes are 40% of all complaints.",
                "And each one takes more than two weeks to resolve.",
            ]
        },
        sfx=[
            ("impactSoft_medium_001.ogg", 8.0, 0.45),
            ("impactSoft_medium_001.ogg", 10.2, 0.45),
            ("impactSoft_medium_001.ogg", 13.0, 0.45),
            ("impactSoft_medium_001.ogg", 16.6, 0.6),
        ],
    ),
    dict(
        id="s2",
        src="02-reveal.html",
        min=10.0,
        logo=4.0,
        vo=[("02a-question", 0.5), ("02b-meet", 5.1)],
        cap={},  # the words are already on screen
        sfx=[("impactBell_heavy_000.ogg", 4.0, 0.6)],
    ),
    dict(
        id="s3",
        src="03-hero.html",
        min=21.4,
        vo=[("03-hero", 1.5)],
        cap={
            "03-hero": [
                "Lucía tells Minsky what happened,",
                "in her own words.",
                "It finds the charge in her account,",
                "checks it against the bank's rules,",
                "and asks before doing anything.",
                "Then it opens the case.",
                "Any hour. No queue.",
            ]
        },
        sfx=[("drop_002.ogg", t, 0.32) for t in (1.0, 3.0, 6.0, 7.6, 10.0, 10.9)] + [("click1.ogg", 9.7, 0.55)],
    ),
    dict(
        id="s4",
        src="04-limits.html",
        min=22.4,
        vo=[("04-limits", 3.5)],
        cap={
            "04-limits": [
                "But a great assistant knows its limits.",
                "If there's nothing to dispute, it explains why.",
                "If it looks like fraud,",
                "it blocks the card in the first minute",
                "and hands the case to a person.",
                "In Spanish, and in Portuguese.",
            ]
        },
        sfx=[("drop_002.ogg", t, 0.28) for t in (2.1, 3.4, 5.0, 7.2, 8.4, 10.2, 11.0)]
        + [("click1.ogg", 9.9, 0.55), ("switch_002.ogg", 11.0, 0.45)],
    ),
    dict(
        id="s5",
        src="05-console.html",
        min=19.9,
        vo=[("05-console", 0.6)],
        cap={
            "05-console": [
                "And one more thing.",
                "When a person takes over,",
                "they don't start from zero.",
                "Today, an urgent case waits as long as any other.",
                "Minsky puts fraud first,",
                "with every fact already checked.",
            ]
        },
        sfx=[
            ("impactSoft_medium_001.ogg", 2.8, 0.5),
            ("switch_002.ogg", 8.9, 0.45),
            ("mouseclick1.ogg", 11.3, 0.55),
            ("mouseclick1.ogg", 15.4, 0.55),
        ],
    ),
    dict(
        id="s6",
        src="06-worth.html",
        min=17.1,
        vo=[("06-worth", 0.8)],
        cap={
            "06-worth": [
                "We ran the bank's rules on every recent charge.",
                "62% would never need an agent to get started.",
                "For card purchases, 96%.",
            ]
        },
        sfx=[("impactSoft_medium_001.ogg", 5.7, 0.55), ("impactSoft_medium_001.ogg", 8.4, 0.5)],
    ),
    dict(
        id="s7",
        src="07-how.html",
        min=15.1,
        vo=[("07-how", 0.8)],
        cap={
            "07-how": [
                "Under the hood, one rule.",
                "The AI reads the message.",
                "Tested code decides, and acts.",
                "Every action is verified",
                "before the customer hears about it,",
                "and simulated customers test every change.",
            ]
        },
        sfx=[("drop_002.ogg", t, 0.3) for t in (2.8, 4.6, 6.0)],
    ),
    dict(
        id="s8",
        src="08-close.html",
        min=11.0,
        vo=[("08-close", 5.0)],
        cap={},
        sfx=[("drop_002.ogg", 0.8, 0.4), ("impactBell_heavy_003.ogg", 4.7, 0.55)],
    ),
]


def wav_len(stem: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(VO / f"{stem}.wav")],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


def expand(path: Path) -> str:
    """Inline /*@include x*/, //@include x and <!--@include x--> from scenes/shared."""
    text = path.read_text()
    pattern = re.compile(r"/\*@include ([\w.-]+)\*/|//@include ([\w.-]+)|<!--@include ([\w.-]+)-->")
    return pattern.sub(lambda m: (SCENES / "shared" / next(g for g in m.groups() if g)).read_text(), text)


def music_lane(total: float, start: float, vo_windows: list[tuple[float, float]], logo: float) -> list[dict]:
    """Volume points (clip-local) for the music: duck under the voice, silence before the logo, fade out."""

    def target(t: float) -> float:
        if logo - 1.1 <= t < logo - 0.02:
            return 0.0
        if any(a - 0.15 <= t < b + 0.3 for a, b in vo_windows):
            return DUCK
        return BASE

    step, slew = 0.05, 0.06
    level, pts, t = 0.0, [], max(0.0, start)
    fade_in_end = max(0.0, start) + 2.0
    while t <= total + 1e-9:
        goal = target(t)
        if t < fade_in_end:
            goal = min(goal, BASE * (t - max(0.0, start)) / 2.0)
        if t > total - 3.0:
            goal = min(goal, BASE * max(0.0, total - t) / 3.0)
        jump = abs(t - logo) < step / 2  # the logo hit comes back at once, on the lift
        level = goal if jump else level + max(-slew, min(slew, goal - level))
        pts.append((round(t - start, 3), round(level, 3)))
        t += step
    keep = [pts[0]]  # drop points on straight segments
    for i in range(1, len(pts) - 1):
        (t0, v0), (t1, v1), (t2, v2) = keep[-1], pts[i], pts[i + 1]
        if abs((v1 - v0) * (t2 - t0) - (v2 - v0) * (t1 - t0)) > 1e-4:
            keep.append(pts[i])
    keep.append(pts[-1])
    return [{"t": t, "v": v} for t, v in keep]


def main() -> None:
    (COMP / "compositions").mkdir(exist_ok=True)
    clock, hosts, audio, caps, vo_windows, logo_abs = 0.0, [], [], [], [], None
    track = 30
    for sc in S:
        lens = {stem: wav_len(stem) for stem, _ in sc["vo"]}
        end = max(off + lens[stem] for stem, off in sc["vo"]) + 1.0
        dur = round(max(sc["min"], end), 2)
        (COMP / "compositions" / f"{sc['id']}.html").write_text(expand(SCENES / sc["src"]))
        hosts.append(
            f'      <div id="{sc["id"]}" data-composition-id="{sc["id"]}" '
            f'data-composition-src="compositions/{sc["id"]}.html" data-start="{clock:.2f}" '
            f'data-duration="{dur:.2f}" data-track-index="1" data-width="1920" data-height="1080"></div>'
        )
        for stem, off in sc["vo"]:
            a = clock + off
            vo_windows.append((a, a + lens[stem]))
            audio.append(
                f'      <audio id="vo-{stem}" src="assets/vo/{stem}.wav" data-start="{a:.2f}" '
                f'data-duration="{lens[stem]:.2f}" data-track-index="{track}" data-volume="1"></audio>'
            )
            track += 1
            chunks = sc["cap"].get(stem, [])
            weights = [len(c) + 8 for c in chunks]
            t = a + 0.1
            span = lens[stem] - 0.15
            for c, w in zip(chunks, weights, strict=True):
                d = span * w / sum(weights)
                caps.append(
                    f'      <div id="cap-{len(caps) + 1:02d}" class="cap clip" data-start="{t:.2f}" '
                    f'data-duration="{d - 0.04:.2f}" data-track-index="5"><span>{c}</span></div>'
                )
                t += d
        for i, (f, t, vol) in enumerate(sc["sfx"]):
            audio.append(
                f'      <audio id="sfx-{sc["id"]}-{i}" src="assets/sfx/{f}" data-start="{clock + t:.2f}" '
                f'data-track-index="{track}" data-volume="{vol}"></audio>'
            )
            track += 1
        if "logo" in sc:
            logo_abs = clock + sc["logo"]
        clock += dur
    total = round(clock, 2)

    mstart = logo_abs - MUSIC_LIFT
    media_start = max(0.0, -mstart)
    lane = music_lane(total, mstart, vo_windows, logo_abs)
    music_dur = min(total - max(0.0, mstart), MUSIC_LEN - media_start)
    automation = json.dumps({"version": 1, "lanes": [{"target": "volume", "points": lane}]}, separators=(",", ":"))
    audio.insert(
        0,
        f'      <audio id="music" src="{MUSIC}" data-start="{max(0.0, mstart):.2f}" '
        f'data-media-start="{media_start:.2f}" data-duration="{music_dur:.2f}" data-track-index="10" '
        f"data-volume=\"1\" data-automation='{automation}'></audio>",
    )

    html = (HERE / "index.template.html").read_text()
    html = html.replace("{{TOTAL}}", f"{total:.2f}").replace("{{HOSTS}}", "\n".join(hosts))
    html = html.replace("{{CAPTIONS}}", "\n".join(caps)).replace("{{AUDIO}}", "\n".join(audio))
    (COMP / "index.html").write_text(html)
    print(
        f"total {total:.2f}s · logo at {logo_abs:.2f}s · music starts {mstart:+.2f}s · "
        f"{len(caps)} captions · {track - 30} audio clips"
    )
    for h in hosts:
        m = re.search(r'id="(s\d)".*data-start="([\d.]+)" data-duration="([\d.]+)"', h)
        print(f"  {m.group(1)} {float(m.group(2)):7.2f} +{float(m.group(3)):.2f}")


if __name__ == "__main__":
    main()
