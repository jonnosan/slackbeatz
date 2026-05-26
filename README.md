# slackbeatz

A small DAW-style music generator. Compose techno-leaning songs from a title phrase, edit per-part / per-voice in a GUI built around an Algorithm → Pattern → Feel drilldown, and play through Surge XT (in-process), an external DAW, or any MIDI rig. The CLI is still there for headless playback / batch render.

> **Note**: the previous incarnation of this repo was an Arduino-based hardware sequencer (ATmega + 1602 LCD + 5-button shield). That code is preserved at the `arduino-v1` tag. The current `main` is a Python rewrite.

## Install

```bash
pip install slackbeatz                  # or: pipx install slackbeatz / uv pip install slackbeatz
```

Requires Python 3.11+. On macOS, enable the IAC Driver in **Audio MIDI Setup → MIDI Studio** to get a virtual MIDI port to route into a DAW or external synth.

## Listen first

Pre-rendered audio for each bundled example lives under [`examples/rendered/`](examples/rendered/), one MIDI and one MP3 per song:

| Song | Style | MIDI | MP3 |
|---|---|---|---|
| `dark_sunday` | `euclid` (Arduino-derived defaults) | [.mid](examples/rendered/dark_sunday.mid) | [.mp3](examples/rendered/dark_sunday.mp3) |
| `deep_set` | `deep_techno` | [.mid](examples/rendered/deep_set.mid) | [.mp3](examples/rendered/deep_set.mp3) |
| `goa` | `psytrance` | [.mid](examples/rendered/goa.mid) | [.mp3](examples/rendered/goa.mp3) |
| `mall` | `vaporwave` | [.mid](examples/rendered/mall.mid) | [.mp3](examples/rendered/mall.mp3) |
| `phuture` | `acid` (Phuture / Acid Trax inspired) | [.mid](examples/rendered/phuture.mid) | [.mp3](examples/rendered/phuture.mp3) |
| `basic` | `dub_techno` (Basic Channel inspired) | [.mid](examples/rendered/basic.mid) | [.mp3](examples/rendered/basic.mp3) |
| `jungle` | `drum_and_bass` (170 bpm, Amen-break) | [.mid](examples/rendered/jungle.mid) | [.mp3](examples/rendered/jungle.mp3) |
| `polymeter` | polymeter demo — drums in 4/4 over bass in 3/4 | [.mid](examples/rendered/polymeter.mid) | [.mp3](examples/rendered/polymeter.mp3) |

## Compose from text

Start the GUI (`slackbeatz` with no arguments) and pick **+ New from title** on the Welcome screen. The dialog takes:

1. A **title** — the first phrase becomes the song title.
2. A **style** — leave on "Auto" to derive from the title's keywords + sentiment, or pick one of the 9 explicit styles.
3. A **setup** — picks the render backend (Surge XT for in-process synth, or external for bare-MIDI to a DAW or hardware).

The **full input** is hashed (SHA-256) to seed every PRNG decision. A single character change — including capitalisation — produces a different song with the same overall shape.

Or use `--text` to compose-and-play in one step from the command line:

```bash
slackbeatz play --text "Lonely night in Berlin" --setup surge
slackbeatz play --text "Cosmic mushroom dance" --setup external
```

Style-picker keyword examples:

| Input phrase | Picked style | Why |
|---|---|---|
| "Lonely night in Berlin" | `deep_techno` | berlin + night + lonely |
| "Cosmic mushroom dance" | `psytrance` | cosmic + mushroom |
| "Sunset over Plaza" | `vaporwave` | sunset + plaza |
| "Acid trax forever" | `acid` | acid + trax |
| "Smoke and fog submerged" | `dub_techno` | submerge + fog + smoke |
| "Jungle rolling neurofunk" | `drum_and_bass` | jungle + neurofunk |
| "UK 2step london garage" | `garage` | 2step + london + garage |
| Anything with no recognised keywords | `euclid` | safe techno fallback |

Pre-composed demos live under [`examples/composed/`](examples/composed/) — one `.sb` + `.mp3` for each style.

Under the `external` backend, MP3s are rendered through **FluidR3_GM** (Frank Wen's MIT-licensed 148 MB stereo GM soundfont, auto-downloaded to `~/.cache/slackbeatz/` on first use) with per-channel GM program selection — bass = Synth Bass, lead = Saw Lead / Square Lead, pad = Warm Pad, candy = FX patches — picked per `(type, style)` so each style sits in a recognisably different timbral space. It's still a GM rendering, not production audio. Under the `surge` backend, pitched channels render through Surge XT (offline VST3 in `slackbeatz audio`, headless `surge-xt-cli` instances in `slackbeatz play`) — proper subtractive synthesis with per-(role, style) factory patches.

## Quick start

The render backend is a property of the **setup**, not a CLI flag. Bundled setups:

| Setup | Backend | What it does |
|---|---|---|
| `surge` | Surge XT | Pitched channels through Surge; ch10 drums through FluidSynth. Best sound. |
| `external` | bare MIDI | Sends all MIDI to a single output port (DAW / IAC bus / hardware). No in-process synth. |
| `gm` / `808` / `909` / `multitimbral` | bare MIDI | Same channel layout as `external`, kept for backwards compatibility. |

```bash
# Launch the GUI (Welcome screen → New from title / Open .sb / Recents)
slackbeatz

# Stream a song live — backend chosen by the song's embedded setup
slackbeatz play examples/dark_sunday.sb

# Play through Surge XT instead of whatever the song's setup specifies
slackbeatz play examples/dark_sunday.sb --setup surge

# Send raw MIDI to an external rig (every drum on its own channel)
slackbeatz play examples/dark_sunday.sb --setup multitimbral

# List available MIDI output ports
slackbeatz list-ports

# Render to an .mp3 (backend = setup's backend)
slackbeatz audio examples/dark_sunday.sb -o /tmp/dark.mp3

# Render through Surge XT VST3 (deterministic, faster than real-time)
slackbeatz audio examples/dark_sunday.sb --setup surge -o /tmp/dark.mp3
```

### Audio rendering setup

Under the **external** backend, `slackbeatz audio` shells out to **FluidSynth** + **ffmpeg** and uses a General MIDI soundfont. Discovery order:

1. `--soundfont <path>` flag if set
2. `$SLACKBEATZ_SOUNDFONT` env var if set
3. Common system paths (Homebrew, `/usr/share/sounds/sf2/`, …)
4. `~/.cache/slackbeatz/FluidR3_GM.sf2` (auto-download default — 148 MB)
5. `~/.cache/slackbeatz/FluidR3Mono_GM.sf3` (compressed mono variant)
6. `~/.cache/slackbeatz/GeneralUser-GS.sf2` (legacy slackbeatz default)
7. Auto-download FluidR3_GM.sf2 if nothing above hit

```bash
# macOS
brew install fluid-synth ffmpeg

# Linux
apt install fluidsynth ffmpeg            # or dnf, pacman, etc.

# Windows
choco install fluidsynth ffmpeg          # or scoop install fluidsynth ffmpeg
```

`-o foo.wav` stops after FluidSynth; `-o foo.mp3` (or any other ffmpeg-supported format) continues through the ffmpeg encode step. Override the soundfont via `--soundfont <path>` or `$SLACKBEATZ_SOUNDFONT`.

Each non-drum channel gets a default GM patch picked by the gen's `(type, style)` pair (e.g. `bass deep_techno` → Synth Bass 2, `melody psytrance` → Square Lead). Override with the `program=N` knob on any gen:

```text
gen lead melody psytrance program=87   # GM patch 87 (Bass + Lead)
```

## The GUI

`slackbeatz` with no arguments opens the Welcome screen. From there you either generate a new song from a title or open an existing `.sb`. The Arrangement view shows a voice × part grid with override markers; clicking a cell drops you into the **Algorithm / Pattern / Feel** drilldown:

- **Algorithm** — picks the generator class for this (voice, part) pair (e.g. `rolling` / `acid_303` / `gallop` for bass).
- **Pattern** — algorithm-specific knobs (swing, voicing, progression, gate, density, octave, …). Differs per algorithm.
- **Feel** — a fixed universal knob set applied to every algorithm (humanize, vel_jitter, gate_jitter, mute_prob, octave_jump, passing_tones, evolution, mistakes).

Knob edits land in one of three scopes you pick at the top of the detail pane:
- **Song** — affects every part and every voice
- **Voice** — affects this voice (e.g. bass) across every part
- **Part** — affects this voice in this single part only

A scope dot next to each knob shows which scope an override lives at; hovering reveals the full cascade chain (Part value / Voice default / Song default / engine default). The `↺` button reverts the override at the current scope.

The arrangement header has buttons for **Mixer** (per-channel mute/solo, wired through Player), **Setup** (instruments + kits + backend picker), and **LFOs** (named time-varying sources — see below).

Mixer state (mute / solo) round-trips through Save in a new `scene` block. Recents + last-used setup live in `~/.slackbeatz/state.json` so the next launch lands you back where you were.

## The DSL

Two file types, both `.sb`:

- **Setup file** — declares the rig: which instrument names live on which MIDI channels, plus drum kits with per-drum note maps.
- **Song file** — declares generators (in *logical* instrument names), parts (named sections), and an arrangement.

A minimal song that resolves against the bundled `gm` setup:

```text
# tiny.sb

song "Tiny"
  setup "gm"          # bundled name; or a path like "studio.sb"
  tempo 128
  key   Am
  seed  42

# gen <handle> <type> <style> [<k=v>...]
gen kick  rhythm euclid
gen bass  bass   euclid
gen pad   chords euclid

# part <name> <bars> [tempo=N] [key=K] [role=R] [seed=N]
# indented lines list the gen handles active in this part
part intro 8
  kick
  pad

part drop 16  role=drop
  kick
  bass
  pad

# arrangement; `*N` repeats, `()` groups
play intro drop drop
```

Validate without producing audio:

```bash
slackbeatz check examples/dark_sunday.sb
slackbeatz list-generators            # show every registered (type, style)
slackbeatz list-setups                # show bundled setup names
```

Full reference lives in [`examples/dark_sunday.sb`](examples/dark_sunday.sb) (every DSL feature) and [`examples/studio.sb`](examples/studio.sb) (standalone setup).

### Per-part knob overrides

Indented gen lines inside a part accept knob overrides — vary the swing in the verse without touching the song default:

```text
part verse 16
  bass humanize=4              # part-scoped knob only
  lead acid_stab gate=0.8      # per-part algorithm + knob
  pad triad_sustain            # per-part algorithm only
```

### Voice block — defaults for a voice type

`voice TYPE` declares knobs that apply to every gen of that type unless a specific part overrides:

```text
voice bass
  swing=0.6
  humanize=4

voice melody
  passing_tones=0.3
```

The cascade order is: engine default → style profile → song-level gen line → voice block → part override (last wins).

### Setup mode directive

Bundled setups carry a `mode` directive that selects one of three peer render paths:

```text
setup "Studio"
mode surge-standalone     # or external / ableton-blackhole
inst lead ch=1
inst bass ch=2
...
```

| mode | What it does | When to pick |
|---|---|---|
| `external` | Raw MIDI to a port — no synth spawned. Wire to a DAW or external hardware yourself. | You already have an audio chain set up and just want SB's notes. |
| `surge-standalone` | Headless `surge-xt-cli` per pitched channel + FluidSynth for ch10 drums; audio writes direct to CoreAudio. SB's Mixer tab owns per-channel volume + FX. Single-screen workflow, no DAW. | Quick iteration; you're OK without master-bus FX or cross-instance reverb sends. |
| `ableton-blackhole` | Same Surge + FluidSynth spawn, but audio routes through [BlackHole 16ch](https://github.com/ExistentialAudio/BlackHole) channels into an Ableton Live Set that owns mixing / FX / master chain. Dual MIDI emission is free via CoreMIDI pub/sub: any Ableton MIDI track can subscribe to the same `slackbeatz-<role>` virtual port surge is listening on — so you can layer SB-driven Surge bass + a hand-added 303 in Ableton on the same channel. | macOS only. You want bus FX, EQ on the master, or cross-instance reverb sends. You already use Ableton. |

The legacy `backend surge` / `backend external` directive still parses (maps to `surge-standalone` and `external` respectively).

#### Ableton+BlackHole setup

One-time setup for `mode ableton-blackhole`:

1. `brew install --cask blackhole-16ch` then `sudo killall coreaudiod` (or reboot). Driver lands at `/Library/Audio/Plug-Ins/HAL/`.
2. **Audio MIDI Setup → BlackHole 16ch → Format → 44100 Hz** for both Input and Output (the default on a fresh install is 8 kHz — drastically degrades audio).
3. In Ableton: **Live → Settings → Audio → Audio Input Device** = BlackHole 16ch. **Input Config** — enable channel pairs 3/4 through 11/12.
4. Add 5 audio tracks in the Live Set with `Audio From → Ext. In`:
   - Track 1 — `3/4`  = lead
   - Track 2 — `5/6`  = bass
   - Track 3 — `7/8`  = pad
   - Track 4 — `9/10` = candy
   - Track 5 — `11/12`= sub
   - Monitor = `In` on each track; drop FX (EQ Eight, Glue Compressor, etc) as desired.
5. **Drums on a MIDI track** — `ableton-blackhole` doesn't spawn FluidSynth; ch10 drum notes emit to a `slackbeatz-drums` virtual MIDI port. Add a MIDI track, set **MIDI From** = `slackbeatz-drums` (Channel = All), drop any Drum Rack / sampler on it, set Monitor = In, arm it. (BlackHole 1/2 is left free; repurpose if useful.)
6. For bidirectional transport: **Live → Settings → Link/MIDI**:
   - MIDI Input row for `slackbeatz-transport-out`: **Sync = On** (SB drives Ableton's clock + Start/Stop/SPP).
   - MIDI Output row for `slackbeatz-transport-in`: **Sync = On** (Ableton's transport buttons drive SB).
7. (Optional) For per-voice MIDI layering: on any Ableton MIDI track, set **MIDI From** to `slackbeatz-bass` (or `-lead` / etc.) — Ableton instruments on that track receive the same notes Surge does. Mute/unmute either side to layer.
8. (Optional) Add MIDI tracks subscribed to `slackbeatz-chord` / `slackbeatz-root` for arp / triad-builder tools.
9. Save as `~/Music/Ableton/User Library/Templates/Slackbeatz.als` — SB's Mixer tab "Open Ableton template" button will reopen this set each session.

### Scene block — mixer state round-trip

GUI Save emits a `scene` block reflecting current mute / solo state per channel:

```text
scene
  ch 2 mute=true
  ch 5 solo=true
  ch 10 vol=0.65         # vol/pan/program are parsed but not yet emitted
```

The block is recursive (`SceneEntry` has `children`), reserved for future per-part Surge patch + LFO automation persistence.

### LFOs — time-varying automation

Declare named LFOs at the song level; bind them to targets per part:

```text
lfo slow_filter shape=sine bars=8 height=0.6
lfo build_drop  shape=sawtooth bars=16 height=1.0

part build 16
  bass rolling
  apply slow_filter target=midi:ch:2/cc:74
  apply build_drop  target=midi:ch:2/cc:1
```

Shapes: `sine`, `sawtooth`, `square`, `pulse`, `noise`. Targets:

- `midi:ch:N/cc:M` — MIDI CC stream on channel N, controller M
- `surge:/param/...` — Surge XT OSC parameter (parser-only; future via AbletonOSC)
- `pattern:HANDLE:KNOB` — per-bar re-emit of an algorithm's pattern knob (e.g. `pattern:bass:density`)
- `feel:TYPE:KNOB` — per-bar re-emit of a Feel knob across all gens of a type (e.g. `feel:bass:humanize`)
- `root:SCOPE[:LO:HI[:MODE]]` — scale-quantized root-note transposition (e.g. `root:global:36:60:degree`). `MODE` is `degree` (LFO 0..1 indexes scale tones) or `snap` (chromatic mapping then snap-to-scale).

### Harmonic broadcast on ch15 + ch16

Every render also emits a steady stream of harmonic-context MIDI on two reserved channels, intended for external listener tools (Ableton arps / triad builders / chord-aware FX) to lock onto the song's current chord without parsing SB internals:

- **ch16 — root note** — single pitch on every quarter note. Follows the bass gen's `progression=` knob if set, then the chord gen's `progression=` knob, then falls back to holding the part's tonic. Routed to the dedicated `slackbeatz-root` virtual MIDI port.
- **ch15 — chord (Imaj7-style)** — four simultaneous pitches built from scale degrees 1/3/5/7 above the current chord root (mode-appropriate: minor-7 over minor scale, maj-7 over major). Triggers at the same quarter-note grid as ch16. Routed to the dedicated `slackbeatz-chord` virtual MIDI port.

In Ableton, add a MIDI track and set **MIDI From** to `slackbeatz-root` or `slackbeatz-chord` to feed an arp / triad builder / instrument. The streams don't reach Surge or FluidSynth — they live entirely on their own virtual ports so they can't pollute the audio mix.

Drum-only parts emit nothing on these channels.

### Per-gen knobs

Every `gen` line accepts a small whitelisted set of `key=value` knobs after the `(type, style)` pair:

| Knob | Type | What it does | Where it applies |
|---|---|---|---|
| `inst` / `kit` | name | Override the setup-name lookup | All |
| `ch` / `note` | int | Raw MIDI override (sketch mode without a setup) | All |
| `program` | int 0..127 | Pin the GM patch on this channel (overrides style default) | Pitched + candy |
| `intensity` | 0..1 | Velocity scaling and density multiplier | All |
| `swing` | 0..1 | Offbeat microtiming shift | Rhythm |
| `humanize` | int ticks | Random ±N tick offset per hit | Rhythm, drums |
| `drop_prob` | 0..1 | Per-hit probability of dropping the note | Rhythm, drums |
| `accent` | int | Every Nth step gets +12 velocity | Rhythm, drums |
| `duck` | 0..1 | Sidechain ducking depth on each beat (1.0 = off) | Bass |
| `density_drift` | 0..1 | Per-bar pulse-count perturbation around the base | Rhythm, drums |
| `mute_prob` | 0..1 | Per-bar (or per-emission) chance the gen drops out | All |
| `evolution` | 0..1 | Linear intensity ramp across the part (direction picked by rng) | All |
| `base_vel` / `base_octave` | int | Override the per-style velocity / register defaults | All |
| `octave` | int | Register offset (legacy alias for `base_octave`) | Pitched gens |
| `gate` | 0..1 | Note length as a fraction of step duration | Pitched gens |
| `density` | 0..1 | CC event count | Candy |
| `cc` | int 0..127 | Override which CC controller candy modulates | Candy |
| `resonance` | int 0..127 | CC 71 ceiling for filter-resonance sweeps | Candy, bass acid |
| `modwheel` | int 0..127 | CC 1 LFO peak amplitude | Melody vaporwave |
| `pan` | int 0..127 | Stereo placement centre (64 = middle) | Melody vaporwave |
| `reverb` | int 0..127 | CC 91 reverb send level | Chords vaporwave |
| `bend` | int | Per-note pitch-wheel wobble amount (8192 ≈ ±1 semitone) | Bass psytrance / acid |
| `cycle` | int bars | LFO period for slow modulators | Candy, bass acid |
| `gate_jitter` | 0..1 | Per-note random duration variance | Pitched gens |
| `arp_prob` | 0..1 | Probability a chord plays as an arpeggio instead of held | Chords (euclid / deep_techno / psytrance / vaporwave) |
| `arp_period` | int N | Deterministic arpeggio every Nth chord (default 2 for vaporwave; 0 = disabled) | Chords vaporwave |
| `burble_prob` | 0..1 | Probability a bass note hits the phrygian b2 instead of the root | Bass psytrance |
| `octave_jump` | 0..1 | Per-note probability of jumping ±1 octave | Bass + melody |
| `motif_memory` | int N | Markov-like — recent degrees reuse with probability ~N×0.1 | Melody (psytrance / vaporwave) |
| `kick_env` | 0..1 | Per-beat CC 74 envelope (filter dips on kick) | Bass deep_techno |
| `passing_tones` | 0..1 | Per-note chance to swap the pitch for a chromatic neighbour | Melody |
| `voice_lead` | bool | Snap each chord tone to the nearest pitch in the next chord | Chords euclid |
| `polyrhythm` | int N | Secondary euclid layer of N pulses at lower velocity | Rhythm |
| `pair` | gen handle | Call-and-response partner — this gen plays alternate 2-bar windows | Melody |
| `arp_period` | int N | Deterministic arpeggio every Nth chord (default 2 for vaporwave) | Chords vaporwave |
| `scale` | scale name | Override the gen's hardcoded scale (e.g. `scale=dorian`) | All pitched |
| `seed` | int | Override the resolved seed for this gen | All |

Part-level knobs (on the `part <name> <bars>` line):

| Knob | What it does |
|---|---|
| `tempo=N` / `key=NAME` / `role=R` / `seed=N` | Per-part overrides of the song defaults |
| `scale=NAME` | Override the scale for all pitched gens in this part |
| `transpose_prob=0..1` | Per-arrangement-instance roll for transposition (±N semitones); shared across all gens in the part so harmony stays coherent |
| `tension=0..1` | Part-level energy scalar; multiplies every gen's velocity. Auto-derived from role if unset (intro/break/outro = 0.5, drop = 1.0, etc.) |

`<bars>` itself can be a range — `part main 32..48` — and the scheduler picks an integer in the range per arrangement-instance (deterministic per seed). Real DJ-style arrangements vary section lengths constantly.

`role=transition` (or `role=fill`) marks a short transitional part — drums gens force-fill every bar; candy gens trigger their sweep behaviour.

### Time signatures + polymeter

Set the time signature at the song level (`meter 3/4`) or per-part (`part odd 8 meter=5/4`). The engine operates on a 16th-note grid so the step count per bar scales with the meter: 4/4 = 16 steps, 3/4 = 12, 5/4 = 20, 7/8 = 14, 6/8 = 12. Supported denominators: 1, 2, 4, 8, 16.

**Polymeter** — set `meter=N/M` on a single `gen` to make it loop at a different meter than the part. The gen drifts in and out of phase against other gens, realigning at the LCM:

```text
song "Polymeter"
  tempo 120
  key Am

gen kick rhythm euclid             # uses part meter
gen sub  bass   euclid meter=3/4   # this one loops at 3/4

part main 12                       # part is 4/4 (the default)
  kick
  sub
play main
```

Drums see 16-step bars; the bass loops a 12-step bar against them. They realign every LCM(16, 12) = 48 steps = 3 bars of 4/4 = 4 bars of 3/4.

Song-level (under the `song "..."` block):

| Attribute | What it does |
|---|---|
| `tempo N` / `key NAME` / `seed N` | Defaults inherited by parts |
| `scale NAME` | Default scale for all pitched gens (overridable per part / per gen) |
| `setup "..."` | Path or bundled name of the setup to bind against |

The chance-driven ones (`humanize`, `drop_prob`, `accent`, `duck`) are deliberately off by default — existing songs keep playing the same; users opt into the variation explicitly. See the example `.sb` files for style-appropriate values.

## How it works

Each generator is a chance-driven algorithm picked by `(type, style)` — e.g. `rhythm euclid`, `bass psytrance`. Algorithms use a seeded PRNG, so the same `seed` always produces the same output.

### Seeds and reproducibility

The seed used for a given *(part, generator)* pair is resolved most-specific-first:

```
gen.seed   →   part.seed   →   song.seed   →   CLI --seed   →   default 0
```

That integer is then mixed with the part name and generator handle (deterministically, via SHA-256, so it survives `PYTHONHASHSEED`) to derive an independent PRNG stream. Practical consequences:

* Repeated parts (`drop drop` in the arrangement) play **identically** — same `(seed, part_name, gen_name)` → same stream. Techno loops typically want this.
* Different gens in the same part roll independently from the same base seed.
* For deliberate variation across repeats, declare differently-named parts (`drop1` / `drop2`) with different `seed=` values.

## Generator types

| Type | What it makes |
|---|---|
| `rhythm` | A single drum voice (one note pattern at one MIDI note) |
| `drums` | A full coordinated drum kit (kick + snare + hat + clap) on one channel |
| `bass` | Pitched bass-register patterns in the part's key |
| `melody` | Pitched lead phrases; tracks chord progression if a `chords` gen is present |
| `chords` | Polyphonic pad voicings on a chord progression |
| `candy` | Risers / sweeps / FX (CC + notes) at part transitions |

## Generator styles

| Style | Character |
|---|---|
| `euclid` | Arduino-derived defaults — Euclidean 4-on-the-floor, 4-bar fills, chord-following lead |
| `deep_techno` | Slower, sparser, modal. Sustained pads, half-note bass, 1–2 melody notes per bar |
| `psytrance` | 138–148 bpm, gallop 16th-note bass, offbeat hats, phrygian arpeggios |
| `vaporwave` | 70–80 bpm, half-time kick, descending i-VII-VI-V on Rhodes electric piano, Tenor Sax leads, periodic tubular-bell glints |
| `acid` | Phuture / Acid Trax — TB-303-style 16th-note bass with octave jumps, continuous CC 74 filter sweep + CC 71 resonance climb, pitch-bend wobble, sparse 909 drums, occasional organ stab |
| `dub_techno` | Basic Channel / Maurizio — soft 4/4 kick + closed hat, off-beat chord stab on every 8th (the signature "chk-chk-chk-chk"), sustained warm-pad bass drone, slow CC 74 + CC 91 modulation. No fills |
| `drum_and_bass` | 170 bpm Amen-break flavoured kit, snare ghost notes, sub-bass drone, lush 9th-chord pads |
| `garage` | UK 2-step (130 bpm) — kick on 1, snare/clap on beat 3 (not 2 & 4), shuffled hats, syncopated sub-bass, Wurli stab chords |

New styles are added by writing one small class per type (six in total) and registering them via `@register_generator("type", "newstyle")`.

## Status

| Component | State |
|---|---|
| DSL parser, setup resolver, arrangement expansion | ✅ shipping |
| Realtime MIDI playback (`slackbeatz play`) | ✅ shipping (master clock only — see below) |
| WAV/MP3 rendering (`slackbeatz audio`) | ✅ shipping |
| Standard MIDI File output (`slackbeatz render`) | 🟡 stubbed; engine writes MIDI internally already (used by `audio`) — exposing via the subcommand is small |
| Slave to external MIDI Clock (`--clock external`) | 🟡 architecture in place, implementation deferred |
| Per-step pattern overrides in the DSL | ❌ deliberately not planned — algorithms own the notes |

The clock-mode plumbing is split cleanly: `engine/clock_source.py` carries a `ClockSource` ABC with `InternalClock` (v1, master) implemented and `ExternalClock` stubbed with a docstring that lays out the future MIDI-Clock-slave contract. The scheduler talks to the ABC only, so adding the external implementation won't touch any other module.

## Development

```bash
git clone https://github.com/jonnosan/slackbeatz && cd slackbeatz
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest        # 31 tests, ~100ms — covers DSL, resolve, engine, examples
```

Open ideas / future work live as [GitHub issues](https://github.com/jonnosan/slackbeatz/issues) — patches welcome.

The previous Arduino sketch is still at the [`arduino-v1`](https://github.com/jonnosan/slackbeatz/tree/arduino-v1) tag for posterity.

## License

MIT — see [LICENSE](LICENSE).
