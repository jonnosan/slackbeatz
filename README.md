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

Each generator is a chance-driven algorithm picked by `(type, style)` — e.g. `rhythm techno`, `bass techno`. Algorithms use a seeded PRNG, so the same `seed` always produces the same output. Seed can be set globally (CLI), per song, per part, or per generator. v1 algorithms lean on the Euclidean rhythm primitive carried forward from the Arduino prototype.

## Generator types

| Type | What it makes |
|---|---|
| `rhythm` | A single drum voice (one note pattern at one MIDI note) |
| `drums` | A full coordinated drum kit (kick + snare + hat + clap) on one channel |
| `bass` | Pitched bass-register patterns in the part's key |
| `melody` | Pitched lead phrases; tracks chord progression if a `chords` gen is present |
| `chords` | Polyphonic pad voicings on a 4-chord progression |
| `candy` | Risers / sweeps / FX (CC + notes) at part transitions |

## License

MIT — see [LICENSE](LICENSE).
