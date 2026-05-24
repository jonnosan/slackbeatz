"""``speech ambient`` — calm, meditation-instructor style narration.

The generator owns a list of *phrases*. At construction time, each
phrase is synthesised via :mod:`slackbeatz.tts` to a cached WAV and
registered with the live :class:`Sampler` (if one is running) at
``note_base + phrase_index``. At play time the gen emits one
``note_on`` every ``phrase_interval`` bars, cycling through the
phrases — the corresponding WAV plays via the sampler.

Knobs (whitelisted via :data:`_GEN_KNOBS` in the parser):

* ``phrases``         tuple of strings, one per phrase (default empty
                       → gen no-ops, useful for sketch lines)
* ``voice``           Piper voice name (default ``en_US-amy-low``)
* ``phrase_interval`` bars between phrase triggers (default 8)
* ``velocity``        note velocity 0..127 (default 80)
* ``note_base``       MIDI note for phrase 0 (default 60 = C4); phrase
                       N plays at ``note_base + N``.

Channel: auto-routed to **5** (the ``voice`` role in
:data:`OSC_CHANNELS`). The resolver creates an Instrument with that
channel without needing a setup entry — see
:mod:`slackbeatz.setup.resolve`.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path
from typing import Any, Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


@register_generator("speech", "ambient")
class SpeechAmbient(Generator):
    """Phrase-scheduled TTS narration. See module docstring."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Resolve phrases / voice / note layout once at construction
        # so the (expensive on first call) TTS synthesis happens before
        # scheduler dispatch. Subsequent runs hit the on-disk cache and
        # are ~instant.
        self._phrases: tuple[str, ...] = self._resolve_phrases()
        self._voice: str = self.knob_str("voice", "en_US-amy-low")
        self._note_base: int = self.knob_int("note_base", 60)
        self._phrase_interval: int = max(1, self.knob_int("phrase_interval", 8))
        self._velocity: int = max(1, min(127, self.knob_int("velocity", 80)))
        # Per-phrase resolved WAV + duration-in-ticks-at-default-tempo
        # is computed lazily in :meth:`_prepare_phrases`, the first
        # time :meth:`generate` runs (so a sampler that starts after
        # gen construction still gets its bank populated).
        self._phrase_wavs: list[Path] = []
        self._phrase_durations_s: list[float] = []
        self._prepared = False

    def _resolve_phrases(self) -> tuple[str, ...]:
        """Read the ``phrases`` knob, normalising single-string and
        empty cases. The parser delivers list literals as ``tuple[str,
        ...]`` but a stray legacy form ``phrases=hello`` would arrive
        as the bare string — accept that gracefully as a one-phrase
        list."""
        raw = self.knobs.get("phrases")
        if raw is None:
            return ()
        if isinstance(raw, tuple):
            return tuple(str(p) for p in raw)
        return (str(raw),)

    def _prepare_phrases(self) -> None:
        """Synthesise each phrase + register it with the live sampler.
        Idempotent. If the sampler isn't running (e.g. ``--surge`` off),
        we still synthesise + cache the WAVs — the gen just won't make
        audible sound for this run."""
        if self._prepared:
            return
        self._prepared = True
        if not self._phrases:
            return

        try:
            from slackbeatz.tts import synthesize
        except ImportError as e:
            print(
                f"speech gen {self.handle!r}: tts module unavailable "
                f"({e}) — phrases silent",
                file=sys.stderr,
            )
            return

        from slackbeatz.sampler import get_active_sampler
        from slackbeatz.synthhost import OSC_CHANNELS
        sampler = get_active_sampler()
        # `voice` role's virtual port name — what the sampler subscribes
        # to. We register WAVs there even if the sampler isn't running
        # right now (it's a no-op in that case).
        voice_port = OSC_CHANNELS["voice"][1]

        for idx, phrase in enumerate(self._phrases):
            try:
                wav_path = synthesize(phrase, voice=self._voice)
            except Exception as e:  # noqa: BLE001 — never blow up playback
                print(
                    f"speech gen {self.handle!r}: failed to synthesise "
                    f"{phrase!r} ({e})", file=sys.stderr,
                )
                continue
            self._phrase_wavs.append(wav_path)
            self._phrase_durations_s.append(_wav_duration_s(wav_path))
            if sampler is not None:
                sampler.set_sample(voice_port, self._note_base + idx, wav_path)

    # ------------------------------------------------------------------
    # Note emission
    # ------------------------------------------------------------------

    def generate(self, ctx: PartContext) -> Iterator[Event]:
        self._prepare_phrases()
        inst = self.instrument
        if inst is None or not self._phrase_wavs:
            return  # no instrument bound, or no usable phrases

        channel = inst.channel
        # Bar-aligned trigger schedule: at bar 0, phrase 0; at bar
        # phrase_interval, phrase 1; etc. Phrases cycle when the list
        # is shorter than the part.
        for bar in range(ctx.bars):
            if bar % self._phrase_interval != 0:
                continue
            phrase_idx = (bar // self._phrase_interval) % len(self._phrase_wavs)
            note = self._note_base + phrase_idx
            if not 0 <= note <= 127:
                continue
            # Convert the phrase's wall-clock duration into ticks at
            # the part's tempo. Sampler ignores duration for the WAV
            # itself (always plays through), but a long note duration
            # makes the scheduler hold off scheduling the next phrase
            # in the same channel slot until this one's release.
            phrase_s = self._phrase_durations_s[phrase_idx]
            ticks = max(1, _seconds_to_ticks(phrase_s, ctx.tempo, ctx.ppq))
            yield Note(
                tick=bar * ctx.ticks_per_bar,
                duration=ticks,
                channel=channel,
                pitch=note,
                velocity=self._velocity,
            )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _wav_duration_s(wav_path: Path) -> float:
    """Return the duration of *wav_path* in seconds. Reads only the
    header (cheap). Falls back to a safe default if the file isn't a
    parseable WAV."""
    try:
        with wave.open(str(wav_path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / float(rate) if rate else 1.0
    except Exception:
        return 2.0  # plausible-ish fallback


def _seconds_to_ticks(seconds: float, tempo_bpm: int, ppq: int) -> int:
    """Convert wall-clock seconds to MIDI ticks at *tempo_bpm* + *ppq*.

    ``ticks_per_sec = ppq * bpm / 60``.
    """
    return int(seconds * ppq * tempo_bpm / 60.0)
