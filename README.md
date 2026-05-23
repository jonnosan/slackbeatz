# slackbeatz

A small CLI that turns a tiny text DSL into MIDI for techno-leaning music. Built for driving a collection of hardware synths over a virtual MIDI port — write the song structure once, twist sound on the devices.

> **Note**: the previous incarnation of this repo was an Arduino-based hardware sequencer (ATmega + 1602 LCD + 5-button shield). That code is preserved at the `arduino-v1` tag. The current `main` is a Python rewrite.

## Install

```bash
pip install slackbeatz                  # or: pipx install slackbeatz / uv pip install slackbeatz
```

Requires Python 3.11+. On macOS, enable the IAC Driver in **Audio MIDI Setup → MIDI Studio** to get a virtual MIDI port to route into a DAW or external synth.

## Quick start

```bash
# List available MIDI output ports
slackbeatz list-ports

# Render a song to the first available port
slackbeatz play examples/dark_sunday.sb

# Same song on a different rig (every drum on its own channel)
slackbeatz play examples/dark_sunday.sb --setup multitimbral
```

## The DSL

Two file types, both `.sb`:

- **Setup file** — declares the rig: which instrument names live on which MIDI channels, plus drum kits with per-drum note maps.
- **Song file** — declares generators (in *logical* instrument names), parts (named sections), and an arrangement.

See `examples/dark_sunday.sb` and `examples/studio.sb` for a full example, or run `slackbeatz check examples/dark_sunday.sb` to validate without producing MIDI.

## How it works

Each generator is a chance-driven algorithm picked by `(type, style)` — e.g. `rhythm euclid`, `bass psytrance`. Algorithms use a seeded PRNG, so the same `seed` always produces the same output. Seed can be set globally (CLI), per song, per part, or per generator.

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

New styles are added by writing one small class per type (six in total) and registering them via `@register_generator("type", "newstyle")`.

## License

MIT — see [LICENSE](LICENSE).
