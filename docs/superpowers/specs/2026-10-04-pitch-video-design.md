# Pitch video: design

- Date: 2026-10-04 (submission due 2026-10-05)
- Status: approved in conversation; this spec is for review before the build plan
- Deliverable: the "short, mandatory video pitch demonstrating the working solution and explaining core architectural decisions" (kickoff deck)

## Brief

What the organizers asked for (kickoff deck + Slack guidance):
- Pitch it to a **bank investor**: sell a product that solves a real problem, not a technical project.
- **90 % product and creativity, 10 % technical.** Structure: **Why → What → How**.
- Editing and delivery carry the most weight. Avoid screen recordings with narration; use animation, transitions and mockups. It should feel like an Apple product launch.

Our decisions:
- About **3:05**, **all in English** (voiceover, on-screen text and the conversations), because some judges may not speak Spanish.
- The product is shown as **animated recreations** of the real `/chat` and `/console`, not screen recordings. The replies are adapted from the system's real behavior.
- **AI voiceover with burned-in captions.** Drafts use the free local Kokoro voice; the final take uses ElevenLabs.
- The storytelling follows the deck (`docs/pitch/index.html`) and the README: problem → what Minsky does → what it is worth → how it works → proof.
- Built with HyperFrames.

Honesty rules (AGENTS.md rule 8), shown as small Apple-style footnotes:
- Estimates are labeled as estimates (the 62 %, 96 % and ~1,600 h, with the slide 5 assumptions).
- Conversations are labeled as "recreated and translated to English; Minsky serves customers in Spanish and Portuguese".
- Lucía is labeled as a fictional customer; the dataset is labeled as synthetic.
- Test results are labeled with their split (development cases unless the held-out run is done before the render).

## Creative direction

A blend of three ideas:
- **Story spine, "11:47 p.m."**: one customer, Lucía, carries the Why and the What.
- **Keynote craft**: dark stage, one idea per frame, big type, match cuts, a "one more thing".
- **Data moment, "the dots"**: recent charges pour through Minsky and sort into the three outcomes; this is where the value lands.

## Script and scenes

About 270 words of voiceover; the rest is music and silence. Scenes 0-6 are product and story (~2:40, ~90 %); scene 7 is the technical 10 %.

| # | Time | Scene | Voiceover (draft) |
|---|---|---|---|
| 0 | 0:00-0:12 | **Cold open.** Black. A phone buzzes. Notification: *"Purchase approved · 48.50 USD · El Buen Sabor Restaurant"*. Lucía's thumb stops. | "11:47 p.m., Mexico City. Lucía paid 32.50 for dinner. Her bank just charged her 48.50." |
| 1 | 0:12-0:40 | **Why: the ordeal.** Desaturated, cramped montage: dialing, a hold-music waveform, a timer (2:00 wait, then a 7:12 call), *"we'll get back to you"*, a calendar flipping 15 days. Keynote stats lock in: **27,133** disputes · **40 %** of complaints · **56 %** of calls end without a solution · **15 days**. | "Today, fixing that means a phone call. A wait. A seven-minute call. And more than half the time, it ends without a solution. At this bank, disputes are 40 % of all complaints, and each one takes more than two weeks." |
| 2 | 0:40-0:50 | **Reveal.** One beat of silence; the light changes. Logo **Minsky**. Tagline: *From "I don't recognize this charge" to an open case.* | "What if it took three messages?" |
| 3 | 0:50-1:25 | **What: the hero conversation.** A floating phone plays Lucía's chat (English version of the deck's slide 3, conversation 1). Callouts: *Finds the charge* · *Asks before acting* · *Case DSP-4FEED87B*. | "Lucía tells Minsky what happened, in her own words. It finds the charge in her account, checks it against the bank's rules, asks before doing anything, and opens the case. Any hour. No queue." |
| 4 | 1:25-2:00 | **It knows when not to act.** Two more phones slide in. One explains a declined double charge (nothing to dispute). One handles "I didn't make this purchase": card blocked, a specialist takes over. Three words land: **Opens. Explains. Hands over.** Then a **language morph**: one bubble shifts English → Spanish → Portuguese. | "But a great assistant knows its limits. If there's nothing to dispute, it explains why. If it looks like fraud, it blocks the card in the first minute and hands the case to a person. In Spanish, and in Portuguese." |
| 5 | 2:00-2:20 | **"One more thing": the agent console.** The phone pulls back to a laptop. The queue reorders itself, fraud to the top. A case card unfolds: verified facts, what the customer said, actions taken, open questions. | "And when a person takes over, they don't start from zero. Today, an urgent case waits as long as any other. Minsky puts fraud first, with every fact already checked." |
| 6 | 2:20-2:40 | **Worth: the dots.** 425,209 recent charges as dots pour through Minsky into three streams: green 54 %, cyan 8 %, amber 38 %. **62 %** locks in, then **96 % of card purchases**, then **~1,600 agent hours a year¹**. | "We ran the bank's rules on every recent charge. 62 % would never need an agent to get started. For card purchases, 96 %." |
| 7 | 2:40-2:55 | **How (technical).** Three blocks: *AI reads → Code decides → Code acts and checks*. Three guarantees tick on: *never another customer's data · nothing without a "yes" · no action claimed until verified*. | "Under the hood, one rule: the AI reads the message; tested code decides and acts. Every action is verified before the customer hears about it, and simulated customers test it on every change." |
| 8 | 2:55-3:05 | **Close.** Lucía's phone: *"Your claim is open."* She puts it down; the light goes off. Logo, then *Minsky. Disputes, handled.*, the demo URL (TBD), "Team Minsky", and *Next: a 4-week pilot in one country.* | "Minsky. Disputes, handled." |

Notes:
- **Mexico City**, because Mexican accounts in the data are in USD, which matches the USD amounts used in the deck.
- **Scene 7 eval line**: it stays generic unless the held-out test run finishes before the render; then we use its real number with its denominator.
- The scene 3 and 4 conversations are the deck's three illustrative conversations, translated, with the system's real structure (transaction card, masked card •••• 4821, Yes/No buttons, case and handoff references).

## Visual language

- **Stage:** near-black `#05070a`, soft spotlight vignette; a faint grid only in scene 7.
- **Meaning colors**, the same everywhere (deck palette):
  - green `#3ddc97`: case opened
  - cyan `#00e5ef`: nothing to dispute, and the brand accent
  - amber `#ffb547`: a person takes over
  - red `#ff6b6b`: fraud only, used sparingly
- **Type:** a large, tight sans for headlines and stats (one idea per frame); monospace for ids and amounts. System fonts, embedded, so the render is deterministic.
- **Phone mockup:** floating, slightly rotated, with a soft reflection; bubbles type in. Layout follows the real `/chat`.
- **Console mockup:** a laptop/browser frame following the real `/console`: queue summary (open, overdue, by priority), the case list with priority badges, and the case detail with its four sections.
- **Motion (launch grammar):**
  - slow push-ins and depth-of-field blur;
  - stats count up and settle;
  - single words land one per beat;
  - match cuts instead of wipes (the notification becomes the logo, the screen fills the frame, the dots pour out of the phone).
- **Before/after contrast:** "today" is desaturated, cramped and jittery with a ticking clock. Minsky is bright, calm and smoothly eased.
- **Sound:**
  - one music bed that builds: sparse for the Why, a lift at the reveal, a steady build through the What, a resolve at the close;
  - small UI sounds: buzz, message pops, a "lock" on stats;
  - one silence before the logo.
- **Captions:** burned in, large and minimal, bottom center, word-synced (many judges watch muted).
- **Footnotes:** small grey text, bottom left, on the estimate, recreation and test-result frames.

## Production

**Location:** `docs/pitch/video/` on branch `docs/pitch-video` (worktree `../minsky-video`). Rendered video and audio are git-ignored.

```
docs/pitch/video/
  design.md      visual spec (this document's "Visual language")
  facts.md       every on-screen number → source doc, labeled measured / estimate
  script/        one voiceover .txt per scene (00-cold-open.txt … 08-close.txt)
  audio/         generated voice clips and the music track (git-ignored)
  scenes/        one sub-composition per scene
  index.html     master timeline: scenes in order, durations taken from the audio
```

**Voice:**
1. Drafts: `hyperframes tts` with Kokoro voice `af_heart` (its highest-quality voice, warm and clear), one clip per scene. Free, so the script can change freely.
2. Timing: `hyperframes transcribe` produces word timings. Captions and "word lands on this word" beats are tied to them, never to hard-coded seconds.
3. Final: the same scene texts through ElevenLabs. Pick the voice with one short test clip (a warm, confident, mid-paced narrator), then generate the nine clips once, re-transcribe and re-render.
   - The script is ~1,600 characters, so a full take is small.
   - The API key stays in the shell environment; it is never pasted in chat or committed.
   - The video is a marketing asset, not the banking system, so ADR 0001 does not apply. Mention it in the PR anyway.

**Review loop (cheapest first):**
1. **Storyboard:** one still per scene (9 PNGs via `hyperframes snapshot`), to approve the look.
2. **Animatic:** the full timeline with the draft voice and rough motion, to check pacing and story.
3. **Polish:** transitions, camera, dot-sorting, sound, captions.
4. **Final:** ElevenLabs voice and the music track; 1080p, 30 fps MP4; `hyperframes check`; a full watch-through.

**Accuracy guard:** scenes read their numbers from `facts.md`. Sources:

| Number | Source | Label |
|---|---|---|
| 27,133 disputes, 40 % of complaints | `docs/disputes_findings.md` | measured |
| 39,000 complaint contacts/year, 85 % by phone, 2 + 7.2 min, 56 % unresolved | deck slide 2 (complaints, call_center_interactions) | measured |
| 15-16 days median, ~20 % SLA breached, 15.5 vs 15.7 days by priority | `docs/disputes_findings.md`, deck slide 2 | measured |
| 425,209 charges → 54 / 8 / 38 %; 62 %; 96 % for card purchases | deck slide 5 | estimate |
| ~1,600 agent hours a year | deck slide 5 | estimate |
| 1.4-1.8 s system time per conversation | deck slide 5, live tests | measured |

**Inputs still needed:**
- a royalty-free music track (the user sources it; the prompt is below);
- the demo URL for the end card (TBD; the end card has a slot for it).

### Music search prompt (for ChatGPT)

> I'm making a 3-minute product launch video in the style of an Apple keynote reveal, for a banking AI assistant. I need **royalty-free music I can use in a public hackathon submission video** (free for commercial or online use, attribution acceptable). Please find 5-8 specific tracks with direct links, from sources like YouTube Audio Library, Pixabay Music, Free Music Archive, Uppbeat (free tier) or Mixkit, and say the license of each.
>
> The track should: be instrumental (no vocals; there is a voiceover on top); be modern, minimal and cinematic-tech (soft piano or plucks, warm synth pads, a light pulse, a gentle build; think Apple product films or Stripe/Linear launch videos); start sparse and quiet for about 40 seconds, lift into a more confident, optimistic section, and resolve calmly at the end; be at least 3 minutes long, or loop cleanly; sit around 90-110 BPM; and avoid corporate-stock clichés (no ukulele, no whistling, no "inspirational" stadium drums, no EDM drops).
>
> For each track give: title, artist, link, license, length, and one line on how it builds.

## Out of scope

- Screen recordings of the live system (the coordinators advise against them; recreations are labeled).
- A Spanish or Portuguese version of the video.
- New product features. The video shows only what exists on `feat/submission-gaps` (chat with es/pt replies, console with triage, Postgres-backed cases), framed as illustrative where it goes beyond a recording.
