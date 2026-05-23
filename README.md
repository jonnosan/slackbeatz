# slackbeatz

A small CLI that turns a tiny text DSL into MIDI for techno-leaning music. Built for driving a collection of hardware synths over a virtual MIDI port — write the song structure once, twist sound on the devices.

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

The MP3s are rendered through **GeneralUser GS** (free GM soundfont, ~30 MB, auto-downloaded on first use) with per-channel GM program selection — bass = Synth Bass, lead = Saw Lead / Square Lead, pad = Warm Pad, candy = FX patches — picked per `(type, style)` so each style sits in a recognisably different timbral space. It's still a GM rendering, not production audio: plug a real synth in for that.

## Quick start

```bash
# List available MIDI output ports
slackbeatz list-ports

# Render a song to the first available port
slackbeatz play examples/dark_sunday.sb

# Same song on a different rig (every drum on its own channel)
slackbeatz play examples/dark_sunday.sb --setup multitimbral

# Render to an .mp3 you can play in any audio player
slackbeatz audio examples/dark_sunday.sb -o /tmp/dark.mp3
```

### Audio rendering setup

`slackbeatz audio` shells out to **FluidSynth** + **ffmpeg** and uses a General MIDI soundfont (auto-downloaded to `~/.cache/slackbeatz/` on first use — GeneralUser GS, ~30 MB).

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
| `burble_prob` | 0..1 | Probability a bass note hits the phrygian b2 instead of the root | Bass psytrance |
| `scale` | scale name | Override the gen's hardcoded scale (e.g. `scale=dorian`) | All pitched |
| `seed` | int | Override the resolved seed for this gen | All |

Part-level knobs (on the `part <name> <bars>` line):

| Knob | What it does |
|---|---|
| `tempo=N` / `key=NAME` / `role=R` / `seed=N` | Per-part overrides of the song defaults |
| `scale=NAME` | Override the scale for all pitched gens in this part |
| `transpose_prob=0..1` | Per-arrangement-instance roll for transposition (±N semitones); shared across all gens in the part so harmony stays coherent |

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
