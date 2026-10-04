# Pitch video

The submission video: a ~2:30 product launch for Minsky, built as code with [HyperFrames](https://hyperframes.heygen.com) (HTML + GSAP rendered to MP4). The rendered file (`minsky.mp4`) is not in git; build it with the commands below.

- **Creative direction:** [`docs/superpowers/specs/2026-10-04-pitch-video-design.md`](../../superpowers/specs/2026-10-04-pitch-video-design.md). It covers the organizers' guidance (investor pitch, 90 % product, launch-event feel), the story, the script and the look.
- **Storyboard and timings:** [`brag-plan.md`](brag-plan.md).
- **Every number on screen and its source:** [`facts.md`](facts.md). Estimates and illustrative content are labeled in the video's footnotes.

## How it is built

```
 script/*.txt ──TTS──▶ composition/assets/vo/*.wav ─┐
 (voiceover, one       (Kokoro draft voice;         │
  file per scene)       final voice: ElevenLabs)    ▼
                                              build.py ──▶ composition/index.html          (timeline, captions, audio)
 scenes/*.html ── includes scenes/shared/ ──▶   │     └──▶ composition/compositions/s0-s8  (one sub-composition per scene)
 (one scene each,  (phone mockup, chat                   │
  own GSAP timeline) playback, base styles)             ▼
                                              hyperframes check · snapshot · render ──▶ minsky.mp4
```

- **Scenes** (`scenes/`): nine HTML files, one per scene, each with its own paused GSAP timeline. The chat and console mockups follow the real `/chat` and `/console`; conversations are recreated and translated to English.
- **`build.py`** reads the voice clips and generates the composition:
  - each scene lasts its designed minimum, or longer if its voiceover needs it;
  - captions are timed inside each voice clip;
  - the music is offset so its lift (37.69 s) lands on the logo, ducks under the voice, goes silent just before the logo and fades out at the end.
  
  Do not edit `composition/index.html` or `composition/compositions/` by hand; edit `scenes/` or `index.template.html` and rebuild.
- **Audio:**
  - music: "Autumn Day" by Kevin MacLeod, CC BY 3.0 (credited on the end card);
  - SFX: CC0 from the brag skill's library, plus a synthesized phone buzz;
  - voice: generated per scene.

## Rebuild and render

Needs Node.js 22+, FFmpeg, the HyperFrames CLI (`npm i -g hyperframes`) and Python 3 for `build.py`. The music file is not in git: download "Autumn Day" (Orange Free Sounds, CC BY 3.0) to `composition/assets/music/autumn-day.mp3`.

```bash
cd docs/pitch/video
# voice: one clip per scene (Kokoro needs `pip install kokoro-onnx soundfile` in the Python HYPERFRAMES_PYTHON points to)
for f in script/*.txt; do hyperframes tts "$f" --voice af_heart --output "composition/assets/vo/$(basename "$f" .txt).wav"; done
python3 build.py                     # regenerate the timeline from the clips
cd composition
hyperframes check                    # lint, layout, contrast (opens a local browser)
hyperframes render --quality draft --output ../minsky.mp4
```

**Swapping in the final voice:**
1. Replace the WAVs in `composition/assets/vo/`, keeping the same names.
2. Run `python3 build.py`, then check and render.

Scene lengths, captions and ducking follow the new clips; nothing is edited by hand.
