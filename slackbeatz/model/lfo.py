"""LFO data model — issue #65.

An LFO is a named time-varying source. Songs declare LFOs at the top
level; parts attach them to targets (MIDI CC, Surge parameter, and
in future Pattern / Feel knobs) via ``apply`` lines.

Phase 1 implementation (this file) covers:
  * Five shapes — sine / sawtooth / square / pulse / noise
  * Period in bars (Hz support is a parser extension, not engine work)
  * Width / depth controls
  * MIDI CC and Surge OSC parameter targets

Feel-knob and Pattern-knob targets are accepted by the parser but
deferred at the engine level — they need re-running the algorithm with
swept knob values per LFO tick, which is heavier than the per-CC-tick
emission this commit ships. The target schema is forward-compatible.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Literal


Shape = Literal["sine", "sawtooth", "square", "pulse", "noise"]


@dataclass(frozen=True)
class LfoSpec:
    """Static definition of one named LFO.

    * ``period_bars`` — cycle length in bars. Floating-point so half-
      bar and dotted cycles work; resolved into ticks at scheduler
      time using the active meter's ``ticks_per_bar``.
    * ``width`` — for ``pulse`` and ``square`` shapes, the duty cycle
      (0..1). Ignored by sine / sawtooth / noise.
    * ``height`` — amplitude scale applied to the unit-range source
      before mapping to the target. A height of 1.0 means full range
      (0..127 for MIDI CC, full parameter range for Surge); smaller
      values centre around the midpoint with a smaller swing.
    * ``offset`` — DC offset added after height scaling (0..1
      conventional). Defaults to 0.5 for sine/square/pulse (so they
      swing around the centre); 0.0 for sawtooth and noise.
    """

    name: str
    shape: Shape
    period_bars: float
    width: float = 0.5
    height: float = 1.0
    offset: float | None = None  # None → shape-appropriate default

    def effective_offset(self) -> float:
        if self.offset is not None:
            return self.offset
        # All shapes default to 0.5 centre so a height=1.0 LFO maps
        # to the full [0,1] target range without clamping. (Pre-1.8
        # sawtooth + noise defaulted to 0.0 which combined with the
        # `centre + (raw-0.5)*height` formula clamped the first half
        # of the ramp to 0 — i.e. a "0→1 sawtooth" actually emitted
        # 0,0,0,0,0,0.1,0.2,0.3,0.4,0.5 across phase 0→1.)
        return 0.5


@dataclass(frozen=True)
class LfoTarget:
    """What an LFO drives.

    * ``midi_cc`` — ``ref`` is ``"ch:NN/cc:NN"`` (e.g. ``"ch:2/cc:74"``).
      Mapped 0..1 → 0..127 by the scheduler at emission time.
    * ``surge_param`` — ``ref`` is a Surge OSC address (e.g.
      ``"/param/a/filter_unison_a/cutoff/value"``). Mapped 0..1 → the
      param's 0..1 normalised range. Parser-only today — re-enabled
      via AbletonOSC in a future commit.
    * ``pattern_knob`` — ``ref`` is ``"<voice_handle>:<knob_name>"``.
      Re-emitted per bar by the scheduler with the swept knob value.
    * ``feel_knob`` — ``ref`` is ``"<voice_type>:<knob_name>"``.
      Re-emitted per bar by the scheduler with the swept knob value.
    * ``root_note`` — ``ref`` is ``"<scope>[:<lo>:<hi>[:<mode>]]"``
      where scope is ``"global"`` (applies to every pitched gen in
      the part) or a voice handle. Optional positional params:
      lo / hi = MIDI note range (default 36..72); mode = ``degree``
      (LFO 0..1 indexes scale degrees across the range — predictable
      stepwise melody) or ``snap`` (LFO 0..1 maps chromatically into
      the range, then snaps to nearest scale tone — random-feeling).
      Default mode is ``degree``. The scheduler applies the resulting
      semitone offset via ``PartContext.transpose_semitones`` per bar.
    """

    kind: Literal[
        "midi_cc", "surge_param", "pattern_knob", "feel_knob", "root_note",
    ]
    ref: str


@dataclass(frozen=True)
class LfoApplication:
    """A per-part ``apply <lfo_name> target=...`` line."""

    lfo_name: str
    target: LfoTarget


def lfo_value_at(spec: LfoSpec, phase: float, rng: random.Random | None = None) -> float:
    """Sample the LFO at *phase* ∈ [0, 1).

    Returns a value in roughly [0, 1] after applying height + offset
    (clamped). The scheduler maps this to the target's value range.

    ``rng`` is used only by the ``noise`` shape so the output is
    deterministic per-tick when the scheduler feeds a seeded PRNG.
    """
    # Wrap phase defensively.
    phase = phase - math.floor(phase)
    if spec.shape == "sine":
        # Centre around 0.5 so multiply-by-height stays in [0, 1].
        raw = (math.sin(2 * math.pi * phase) + 1) / 2
    elif spec.shape == "sawtooth":
        raw = phase  # 0 → 1 linear ramp
    elif spec.shape == "square":
        raw = 1.0 if phase < spec.width else 0.0
    elif spec.shape == "pulse":
        # Pulse is square with explicit duty cycle, kept for clarity.
        raw = 1.0 if phase < spec.width else 0.0
    elif spec.shape == "noise":
        raw = (rng or random.Random()).random()
    else:  # pragma: no cover — keeps mypy quiet
        raw = 0.0

    # Apply height around the shape's natural centre. All shapes use
    # the same swing-around-centre formula now that every shape
    # defaults to centre=0.5 — for `raw` in [0,1], output is
    # `centre + (raw - 0.5) * height` which maps to [centre-h/2,
    # centre+h/2]. A height=1.0 LFO with offset=0.5 spans the full
    # [0,1] target range; height=0.5 spans [0.25, 0.75]; offset=0.3
    # height=0.4 spans [0.1, 0.5].
    centre = spec.effective_offset()
    value = centre + (raw - 0.5) * spec.height
    return max(0.0, min(1.0, value))


def parse_target(raw: str) -> LfoTarget:
    """Parse a ``target="..."`` reference into an :class:`LfoTarget`.

    Accepted forms:

    * ``"midi:ch:N/cc:M"`` → MIDI CC target
    * ``"surge:/param/..."`` → Surge OSC parameter (parser-only)
    * ``"pattern:<handle>:<knob>"`` → pattern-knob (per-bar re-emit)
    * ``"feel:<type>:<knob>"`` → feel-knob (per-bar re-emit)
    * ``"root:<scope>[:<lo>:<hi>[:<mode>]]"`` → scale-quantized root
      note. Scope is ``global`` or a voice handle; lo/hi are MIDI note
      bounds (default 36/72); mode is ``degree`` (default) or ``snap``.
    """
    if raw.startswith("midi:"):
        return LfoTarget(kind="midi_cc", ref=raw[len("midi:"):])
    if raw.startswith("surge:"):
        return LfoTarget(kind="surge_param", ref=raw[len("surge:"):])
    if raw.startswith("pattern:"):
        return LfoTarget(kind="pattern_knob", ref=raw[len("pattern:"):])
    if raw.startswith("feel:"):
        return LfoTarget(kind="feel_knob", ref=raw[len("feel:"):])
    if raw.startswith("root:"):
        return LfoTarget(kind="root_note", ref=raw[len("root:"):])
    raise ValueError(
        f"unknown LFO target {raw!r} — expected midi:.. / surge:.. / "
        "pattern:.. / feel:.. / root:.."
    )
