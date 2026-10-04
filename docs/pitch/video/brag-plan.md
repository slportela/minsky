# Brag plan: Minsky

The creative contract is the approved spec: `docs/superpowers/specs/2026-10-04-pitch-video-design.md`. This page records the brag-specific decisions on top of it.

- **What it is:** a chat assistant that takes a bank customer from "I don't recognize this charge" to an open dispute case, and knows when to hand over to a person.
- **Angle:** an Apple-style launch pitched to a bank investor. One customer, Lucía, carries the Why and the What; the dots carry the value; one scene carries the How.
- **Hook (0-3 s):** a phone buzzes in the dark: "Purchase approved · 48.50 USD". She paid 32.50.
- **User flow shown:** the customer describes the charge → Minsky finds it and asks → the case is opened and verified. Plus the two other endings (nothing to dispute; fraud: card blocked, a person takes over) and the agent console.
- **Tone:** preset `cinematic`, direction "Apple keynote product reveal for a bank". Restraint: one idea per frame, slow holds, big type.
- **Format / duration:** landscape 1920×1080, ~2:30 with the draft voice (scene lengths follow the voice). This overrides brag's 15-25 s default: the hackathon requires a pitch video that explains the product.
- **Visual identity:** deck palette. Background `#05070a`, text `#e8eef2`, accent cyan `#00e5ef`; outcome colors green `#3ddc97` (case opened), cyan (nothing to dispute), amber `#ffb547` (a person takes over), red `#ff6b6b` (fraud / pain). System sans and mono fonts.
- **Voice:** enabled (user request). Draft with a local voice; final take with ElevenLabs. Captions burned in.
- **Audio:** "Autumn Day" by Kevin MacLeod (CC BY 3.0, credited on the end card). The track is offset so its 37.69 s lift lands on the logo; a beat of silence before it; music ducks under the voice. Sparse SFX: a synthesized phone buzz, soft drops for chat messages, clicks for taps, soft impacts for stats, a bell on each logo.
- **Honesty:** footnotes for the fictional customer and synthetic data, the recreated and translated conversations, the synthetic policy, and the estimates (62 %, 96 %, ~1,600 h). Numbers come from `facts.md`.
- **Share copy:** see `share-copy.txt`.

## Storyboard (draft-voice timings)

| Scene | Start | Length | Content |
|---|---|---|---|
| s0 cold open | 0:00 | 12 s | Lock screen, buzz, "11:47 p.m. Mexico City", dinner bill 32.50 vs charged 48.50 |
| s1 why | 0:12 | 22 s | Desaturated call: hold, 7:12 call, "We'll get back to you"; 56 % · 40 % · 15+ days; 27,133 |
| s2 reveal | 0:34 | 10 s | "What if it took three messages?" · silence · Minsky logo on the music lift · tagline |
| s3 hero | 0:44 | 21 s | Lucía's chat; callouts: finds the charge, asks before acting, opens the case; "Three messages. Any hour. No queue." |
| s4 limits | 1:06 | 22 s | Three phones: opens, explains, hands over; language morph EN → ES → PT |
| s5 console | 1:28 | 20 s | "One more thing." Agent console: queue reorders fraud first; the case card; claim |
| s6 worth | 1:48 | 17 s | 425,209 charges as dots sort into 54 / 8 / 38 %; 62 %; 96 %; ~1,600 h |
| s7 how | 2:05 | 15 s | The AI reads → code decides → code acts and checks; three guarantees |
| s8 close | 2:20 | 11 s | "Your claim is open"; logo; "Disputes, handled."; end card and credits |
