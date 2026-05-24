# Design: TTS + MIDI-triggered Sampler

Status: **design accepted 2026-05-24, implementation pending**.
Tracking issues: see `tts-sampler` milestone (or label `area:tts` /
`area:audio`).

## Goal

Add two new audio channels to slackbeatz:

* **A MIDI-triggered sampler** that plays WAV files on note_on events.
  Use cases: drum kits, multi-sampled instruments, sound effects,
  spoken-word phrases.
* **A text-to-speech pipeline** that synthesises calm whispering /
  meditation-instructor style narration. Output is a WAV that gets
  loaded into the sampler — so "playing a phrase" is just "send a
  MIDI note to the sampler channel".

This unifies sound effects + voice into one mechanism (the sampler);
TTS becomes a way to *generate* sample files, not a runtime concern.

## Architecture

```
slackbeatz channels:
  ch 1  = lead    → surge-xt-cli (existing)
  ch 2  = bass    → surge-xt-cli (existing)
  ch 3  = pad     → surge-xt-cli (existing)
  ch 4  = candy   → surge-xt-cli (existing)
  ch 5  = voice   → python sampler          ← NEW
  ch 10 = drums   → FluidSynth (existing)
  ch 11 = fx      → python sampler          ← NEW
```

The voice + fx channels go through the same `MultiPortSink` as the
Surge-backed channels. Each gets a dedicated virtual MIDI port
(`slackbeatz-voice`, `slackbeatz-fx`); the Python sampler subscribes
to those ports in-process (no subprocess, no IPC).

## Design choices (locked)

* **Sampler**: roll our own in Python (`mido` + `sounddevice` +
  `soundfile` + `numpy`). ~200 LOC. Rejected: sfizz (heavy on macOS),
  FluidSynth dynamic SF2 (high complexity to build SF2 files on the
  fly).
* **TTS backend**: [Piper](https://github.com/rhasspy/piper) — small
  ONNX models (~50-100MB), fast (~real-time on CPU), good English
  female voices including soft-spoken / whispery ones. No cloud calls.
* **Voice cloning**: **deferred**. Coqui XTTSv2 (which can clone a
  voice from a 6-10s reference clip) is a future option, not part of
  v1.
* **Pitch handling**: native pitch per note, **no re-pitching**. Each
  MIDI note maps to a distinct sample (drum-kit OR multi-sampled
  instrument layout — the sampler doesn't care which).
* **Sample format**: WAV only for v1 (FLAC / MP3 later if needed).

## Module layout

```
slackbeatz/
├── sampler.py                    # NEW — Python sampler engine
├── tts.py                        # NEW — Piper-backed TTS pipeline
├── generators/
│   ├── speech/                   # NEW — gen type `speech`
│   │   ├── __init__.py
│   │   └── ambient.py
│   └── sample/                   # NEW — gen type `sample`
│       ├── __init__.py
│       └── one_shot.py
└── synthhost.py                  # UPDATED — OSC_CHANNELS gains voice + fx
```

## `slackbeatz/sampler.py` — the engine

### Public API

```python
class Sampler:
    """Listens on a MIDI input port, plays WAV samples on note_on.

    One Sampler instance can subscribe to multiple MIDI ports — the
    typical setup has one Sampler covering both 'slackbeatz-voice'
    and 'slackbeatz-fx', with per-port sample banks.
    """

    def __init__(self,
                 port_banks: dict[str, dict[int, Path]],
                 *, sample_rate: int = 44100,
                 max_polyphony: int = 16) -> None: ...

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def set_sample(self, port_name: str, midi_note: int, wav_path: Path) -> None: ...
    def remove_sample(self, port_name: str, midi_note: int) -> None: ...
    def get_bank(self, port_name: str) -> dict[int, Path]: ...
```

### Behaviour

* Opens each port via `mido.open_input(name, virtual=False)` —
  the ports already exist (created by `MultiPortSink`).
* Background thread per port reads MIDI messages.
* On `note_on` with velocity > 0:
    * Look up `bank[note]`. If absent, ignore (silent — same as
      hitting an unmapped pad on a hardware sampler).
    * Load + cache the WAV via `soundfile.read()` (cached, so reload
      is free).
    * Allocate a voice slot (LRU eviction if `max_polyphony` reached).
    * Scale by velocity / 127 (linear; could become curve-able later).
    * Mix into the sounddevice output stream.
* On `note_off`: trigger a short release envelope (~50ms) on the
  matching voice. This avoids clicks when notes are short.
* Audio output via `sounddevice.OutputStream` with a callback that
  mixes all currently-active voices.
* WAVs are loaded mono OR stereo; mono samples play centered, stereo
  preserve their panning.

### Sample bank

A bank is just `dict[int, Path]` per port. The DSL layer + GUI build
these dicts. The sampler itself has no concept of "drum kit" vs
"multi-sample instrument" — it's all just note → WAV lookups.

Bank persistence: bundle samples lookup inside the generator's
declaration (e.g. `bank=samples/junglekit/` resolves at gen-construct
time + populates the sampler's bank for that port).

## `slackbeatz/tts.py` — the TTS pipeline

### Public API

```python
def synthesize(text: str,
               voice: str = "en_US-amy-low",
               *, output_path: Path | None = None,
               post_fx: bool = True) -> Path:
    """Synthesise *text* via Piper to a WAV file. Cached.

    Returns the path to the WAV. Identical (text, voice, post_fx)
    triples return the cached file immediately — no re-synthesis.
    """

def available_voices() -> list[str]:
    """List downloaded Piper voice models."""

def download_voice(voice: str) -> None:
    """Fetch a Piper model from the rhasspy/piper-voices Hugging Face
    repo into ~/Library/Application Support/slackbeatz/piper-voices/."""
```

### Backend: Piper

* Pip dep: `piper-tts` (or shell out to `piper` binary if the pip
  package is fragile on Python 3.14).
* Voice models live at
  `~/Library/Application Support/slackbeatz/piper-voices/<voice>.onnx`
  + `.onnx.json` config.
* Default voice: `en_US-amy-low` (a soft female voice). Future
  alternatives to pre-document: `en_US-ljspeech-medium`,
  `en_US-hfc_female-medium`.

### Cache layout

```
~/Library/Caches/slackbeatz/tts/
├── <sha256-of-text+voice+post_fx>.wav
└── ...
```

Cache key: `hashlib.sha256(f"{text}|{voice}|{post_fx}".encode()).hexdigest()[:16]`.

### Post-processing (optional, on by default)

Make the raw Piper output sound more "meditation studio":

* Lowpass filter at ~6kHz (cuts harsh sibilance)
* Reverb tail (~1.5s, low predelay)
* Slight compression (-3dB threshold, 2:1 ratio)

Implementation: [`pedalboard`](https://github.com/spotify/pedalboard)
(Spotify's audio FX library — pip-installable, JUCE-backed, runs in
~10ms on Mac). Falls back to a no-op chain if `pedalboard` import
fails (graceful degradation).

## DSL extensions

### New value kind: list of strings

```
phrases=["breathe in", "and out", "let go of tension"]
```

Parser change in `slackbeatz/dsl/parser.py`: extend `kv_pair` to
accept `[STRING (, STRING)*]` as a value. Lexer needs no changes (it
already tokenises `[`, `]`, `,`, `STRING`).

### New gen types

```
gen voice  speech ambient \
    phrases=["breathe in", "and out"] \
    voice=en_US-amy-low \
    phrase_interval=8

gen percfx sample one_shot \
    bank=samples/junglekit/ \
    pattern=euclid pulses=3 steps=16
```

Both register with `@register_generator` like existing types. The
resolution flow auto-routes them based on their type → channel
convention:

* `speech` → channel 5  (the `voice` role)
* `sample` → channel 11 (the `fx` role)

If users want to override, raw `ch=` is still supported (DSL contract).

## `OSC_CHANNELS` change

Extend `slackbeatz/synthhost.py`:

```python
OSC_CHANNELS: dict[str, tuple[int, str, str | None]] = {
    "lead":  (1,  "slackbeatz-lead",  "Leads/Classic Lead 1.fxp"),
    "bass":  (2,  "slackbeatz-bass",  "Basses/Bass 1.fxp"),
    "pad":   (3,  "slackbeatz-pad",   "Pads/MKS-70 Warm Pad.fxp"),
    "candy": (4,  "slackbeatz-candy", "Sequences/Bell Seq.fxp"),
    "voice": (5,  "slackbeatz-voice", None),   # ← non-Surge
    "fx":    (11, "slackbeatz-fx",    None),
}
```

Patch field becomes `Optional[str]`. `None` means "this role isn't
Surge-backed". `surge_host.py:spawn_surge_instances()` filters those
out before spawning surge-xt-cli; `MultiPortSink` still creates the
virtual port (so the sampler can subscribe).

## CLI wiring

`cmd_repl` and `cmd_live` need to:

1. Create the Sampler instance after `MultiPortSink.open()` (so the
   virtual ports exist).
2. Hand it the per-port banks (built from the resolved song's
   `speech` / `sample` generators).
3. Call `sampler.start()` to begin listening.
4. On shutdown: `sampler.stop()`.

Player.surge_routing → rename to something synth-agnostic? Defer —
the boolean still semantically means "use the per-channel
MultiPortSink routing model". Could rename to `osc_routing` later;
not in scope here.

## GUI panels

Two new sub-tabs in the Sound notebook:

### 🎙 Voice (ch 5)

```
┌─ Phrase library ───────────────────────────────┐
│  60 (C4)  "breathe in"           [▶] [✏️] [✕]  │
│  62 (D4)  "and out"              [▶] [✏️] [✕]  │
│  64 (E4)  "let go"               [▶] [✏️] [✕]  │
└────────────────────────────────────────────────┘

Synthesize new phrase:
  Text:  [_________________________________]
  Voice: [en_US-amy-low ▾]
  Note:  [65 ▾]
        [ Generate ]
```

### 🔊 FX (ch 11)

```
┌─ Sample bank ──────────────────────────────────┐
│  36 (C2)  /Users/jonno/kits/909/kick.wav  [▶] [✕]│
│  38 (D2)  /Users/jonno/kits/909/snare.wav [▶] [✕]│
│  42 (F#2) /Users/jonno/kits/909/hat.wav   [▶] [✕]│
└────────────────────────────────────────────────┘

[ + Add WAV… ]  or drag-and-drop WAVs here
```

Drag-and-drop integration via `tkdnd` (optional pip dep). Without it,
the "+ Add WAV" file picker covers the use case.

## Implementation phases + estimated effort

| # | Phase                                    | Effort | Depends on   |
|---|------------------------------------------|--------|--------------|
| 1 | Sampler engine (sampler.py)              | M      | —            |
| 2 | OSC_CHANNELS + sampler wiring            | S      | 1            |
| 3 | TTS pipeline (tts.py, Piper)             | M      | —            |
| 4 | DSL: list-of-strings value               | S      | —            |
| 5 | `speech` gen type (ambient style)        | M      | 1, 2, 3, 4   |
| 6 | `sample` gen type (one_shot style)       | M      | 1, 2, 4      |
| 7 | GUI: Voice + FX sub-tabs in Sound        | M      | 1, 2, 3      |
| 8 | TTS post-FX (lowpass + reverb)           | S      | 3            |

S = small (≈1-2h), M = medium (≈3-5h).

## Future work (out of scope for v1)

* Coqui XTTSv2 backend for voice cloning (record a reference, clone)
* FLAC / MP3 sample support
* Sample-time pitch shifting (one sample covers an octave)
* Round-robin sample variation (multiple WAVs per note, picked at random)
* Sample slicing / chop mode (single long WAV → multiple notes)
* External plugin host wrapper (sfizz, EXS24, Kontakt) as alt backend
