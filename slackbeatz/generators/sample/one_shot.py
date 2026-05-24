"""``sample one_shot`` — rhythmic WAV triggers from a sample bank.

Scan a directory (or accept a single WAV path) at construction time;
map each ``*.wav`` to ``note_base + index``. At play time, generate
trigger steps via a small set of patterns (currently ``euclid`` and
``every_bar``) and emit ``note_on`` events that round-robin through
the bank.

Knobs (whitelisted via :data:`_GEN_KNOBS` in the parser):

* ``bank``      directory of ``.wav`` files OR a single file path
* ``pattern``   ``euclid`` (default) / ``every_bar``
* ``pulses``    pulse count for Euclidean rhythm (default 4)
* ``steps``     step count for Euclidean rhythm (default 16)
* ``velocity``  note velocity 0..127 (default 90)
* ``note_base`` MIDI note for bank[0] (default 36 = C2); bank[N]
                 plays at ``note_base + N``.

Channel: auto-routed to **11** (the ``fx`` role in
:data:`OSC_CHANNELS`). See the resolver for details.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import euclid, step_to_ticks
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


@register_generator("sample", "one_shot")
class SampleOneShot(Generator):
    """Pattern-driven WAV sample triggerer. See module docstring."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._bank_paths: list[Path] = self._resolve_bank()
        self._pattern: str = self.knob_str("pattern", "euclid")
        self._pulses: int = max(0, self.knob_int("pulses", 4))
        self._steps: int = max(1, self.knob_int("steps", 16))
        self._velocity: int = max(1, min(127, self.knob_int("velocity", 90)))
        self._note_base: int = self.knob_int("note_base", 36)
        self._registered = False

    def _resolve_bank(self) -> list[Path]:
        """Read the ``bank`` knob, expand to a list of ``.wav`` paths.

        Accepts:

        * A directory — all ``*.wav`` children, sorted by filename.
        * A single ``.wav`` file — wrapped in a one-element list.
        * Anything else (or missing) — empty list (gen no-ops).
        """
        raw = self.knobs.get("bank")
        if not raw:
            return []
        p = Path(str(raw)).expanduser()
        if p.is_dir():
            return sorted(p.glob("*.wav"))
        if p.is_file():
            return [p]
        print(
            f"sample gen {self.handle!r}: bank path {p} doesn't exist",
            file=sys.stderr,
        )
        return []

    def _register_with_sampler(self) -> None:
        """Push the bank into the live sampler so its note_on handler
        finds the right WAV for each pitch. Idempotent + safe to call
        when no sampler is running."""
        if self._registered:
            return
        self._registered = True
        if not self._bank_paths:
            return
        from slackbeatz.sampler import get_active_sampler
        from slackbeatz.synthhost import OSC_CHANNELS
        sampler = get_active_sampler()
        fx_port = OSC_CHANNELS["fx"][1]
        if sampler is None:
            return
        for idx, wav in enumerate(self._bank_paths):
            sampler.set_sample(fx_port, self._note_base + idx, wav)

    # ------------------------------------------------------------------
    # Note emission
    # ------------------------------------------------------------------

    def generate(self, ctx: PartContext) -> Iterator[Event]:
        self._register_with_sampler()
        inst = self.instrument
        if inst is None or not self._bank_paths:
            return

        channel = inst.channel
        steps_per_bar = ctx.steps_per_bar

        # Build the step pattern once per gen — it doesn't change per
        # bar. Round-robin through bank notes as we walk active steps.
        pattern = self._build_pattern(steps_per_bar)
        bank_size = len(self._bank_paths)
        # Use ticks-per-step that matches the bar's step count rather
        # than the gen's `steps` knob — keeps timing aligned to the
        # part's meter even when `steps` is something exotic like 12.
        for bar in range(ctx.bars):
            bar_start = bar * ctx.ticks_per_bar
            hit_index = 0
            for s, hit in enumerate(pattern):
                if not hit:
                    continue
                note = self._note_base + (hit_index % bank_size)
                hit_index += 1
                if not 0 <= note <= 127:
                    continue
                # Short fixed duration — sampler plays the WAV to its
                # end regardless, this only governs scheduler bookkeeping.
                yield Note(
                    tick=bar_start + step_to_ticks(s, ctx.ppq, steps_per_bar),
                    duration=ctx.ppq // 4,  # 64th note
                    channel=channel,
                    pitch=note,
                    velocity=self._velocity,
                )

    def _build_pattern(self, steps_per_bar: int) -> list[bool]:
        """Resolve the gen's pattern knob to a per-step on/off list.

        Unknown pattern names fall through to ``every_bar`` (a single
        hit on step 0) with a one-time warning."""
        if self._pattern == "euclid":
            # Use the requested step count if it matches the bar's,
            # otherwise re-scale to the actual steps_per_bar to keep
            # timing consistent with the part's meter.
            steps = self._steps if self._steps == steps_per_bar else steps_per_bar
            return euclid(self._pulses, steps)
        if self._pattern == "every_bar":
            out = [False] * steps_per_bar
            out[0] = True
            return out
        print(
            f"sample gen {self.handle!r}: unknown pattern "
            f"{self._pattern!r} — falling back to every_bar",
            file=sys.stderr,
        )
        out = [False] * steps_per_bar
        out[0] = True
        return out
