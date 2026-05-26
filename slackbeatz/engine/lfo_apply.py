"""Per-bar LFO modulation of Feel + Pattern knobs.

This module owns the "convert an LFO 0..1 sample into a mutated knob
value, and re-emit the next bar's events with that mutation" logic
used by the scheduler when an LFO application targets a
``feel_knob`` / ``pattern_knob`` (and, via the same path, the new
``root_note`` target).

Design — per-bar re-emit:

The scheduler's normal path calls each generator once per
part-instance with the resolved knobs and gets the whole part's
events. That model can't honour an LFO that's supposed to sweep a
knob across the part because the knob value is read once at
generation time.

For LFO-targeted Feel/Pattern knobs we instead split the part into
N single-bar slices, sample each affected LFO at the bar boundary,
mutate the knob, and re-run the generator with `bars=1` per bar.
Events from each bar are concatenated with appropriate tick offsets.
Per-bar PRNG seeding stays deterministic — each bar's seed is
derived from the song seed + part + handle + bar index.

Targets handled:

* ``feel_knob`` — ref = ``"<voice_type>:<knob_name>"``. Modulates
  any gen whose ``type_`` matches. Knob range comes from
  :data:`slackbeatz.generators.feel.FEEL_KNOBS`.
* ``pattern_knob`` — ref = ``"<voice_handle>:<knob_name>"``.
  Modulates only the gen with matching handle. Knob range comes
  from :data:`slackbeatz.ui.knob_specs.KNOB_SPECS`.
* ``root_note`` — ref = ``"global"`` or ``"part:<name>"`` or
  ``"<handle>"``. Handled separately by :func:`apply_root_note`
  (see [[backend_is_setup]] notes on root_note design).

The data model already accepts these kinds (parsed by
:func:`slackbeatz.model.lfo.parse_target`); the scheduler used to
drop them at emit time. This module wires them up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from slackbeatz.generators.feel import FEEL_KNOBS
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import SCALES
from slackbeatz.ui.knob_specs import KNOB_SPECS, KnobSpec


# Lookup the feel knob spec by name. Built once for cheap O(1).
_FEEL_BY_NAME = {spec.name: spec for spec in FEEL_KNOBS}


def parse_feel_pattern_ref(ref: str) -> tuple[str, str] | None:
    """Split ``"<scope>:<knob>"`` → ``(scope, knob)`` or None on malformed."""
    if ":" not in ref:
        return None
    scope, knob = ref.split(":", 1)
    if not scope or not knob:
        return None
    return scope, knob


def lfo_value_to_knob(
    knob_name: str,
    value_unit: float,
    *,
    is_feel: bool,
) -> Any | None:
    """Map an LFO 0..1 sample to a value for *knob_name*.

    *is_feel* selects which registry to consult — Feel knobs have
    their own simpler spec (low/high/default) in
    :data:`FEEL_KNOBS`; Pattern knobs come from
    :data:`KNOB_SPECS` which also handles bool / enum / int / float.

    Returns ``None`` for unknown knob names so the scheduler can skip
    silently rather than break the render.
    """
    if is_feel:
        spec = _FEEL_BY_NAME.get(knob_name)
        if spec is None:
            return None
        lo, hi = float(spec.low), float(spec.high)
        # All Feel knobs are numeric; humanize / vel_jitter are int
        # ranges so round to int when both endpoints are integer.
        mapped = lo + (hi - lo) * max(0.0, min(1.0, value_unit))
        if isinstance(spec.low, int) and isinstance(spec.high, int):
            return int(round(mapped))
        return mapped
    # Pattern knob — full KnobSpec dispatch (bool / enum / int / float).
    spec_p: KnobSpec | None = KNOB_SPECS.get(knob_name)
    if spec_p is None:
        return None
    v = max(0.0, min(1.0, value_unit))
    if spec_p.kind == "bool":
        return v >= 0.5
    if spec_p.kind == "enum":
        choices = list(spec_p.choices or ())
        if not choices:
            return None
        idx = min(len(choices) - 1, int(v * len(choices)))
        return choices[idx]
    if spec_p.kind == "int":
        lo_p = int(spec_p.low if spec_p.low is not None else 0)
        hi_p = int(spec_p.high if spec_p.high is not None else 1)
        return int(round(lo_p + (hi_p - lo_p) * v))
    if spec_p.kind == "float":
        lo_p = float(spec_p.low if spec_p.low is not None else 0.0)
        hi_p = float(spec_p.high if spec_p.high is not None else 1.0)
        return lo_p + (hi_p - lo_p) * v
    return None


@dataclass(frozen=True)
class RootNoteConfig:
    """Resolved positional params of a ``root:<...>`` LFO target."""

    scope: str               # "global" or a voice handle
    lo: int = 36             # MIDI note, inclusive
    hi: int = 72             # MIDI note, inclusive
    mode: str = "degree"     # "degree" or "snap"


def parse_root_ref(ref: str) -> RootNoteConfig | None:
    """Parse ``"<scope>[:<lo>:<hi>[:<mode>]]"`` into a RootNoteConfig.

    Returns ``None`` if the format is malformed. The scheduler skips
    silently rather than failing — bad refs shouldn't kill playback.
    """
    if not ref:
        return None
    parts = ref.split(":")
    scope = parts[0]
    if not scope:
        return None
    lo, hi, mode = 36, 72, "degree"
    if len(parts) >= 3:
        try:
            lo = int(parts[1])
            hi = int(parts[2])
        except ValueError:
            return None
    if len(parts) >= 4 and parts[3] in ("degree", "snap"):
        mode = parts[3]
    if not (0 <= lo <= 127 and 0 <= hi <= 127 and lo < hi):
        return None
    return RootNoteConfig(scope=scope, lo=lo, hi=hi, mode=mode)


def gen_matches_root_scope(gen_handle: str, scope: str) -> bool:
    """True if a gen is affected by a root_note LFO of the given scope.

    ``"global"`` matches every pitched gen; otherwise we match handle.
    """
    return scope == "global" or gen_handle == scope


def root_note_semitones(
    value_unit: float, *,
    cfg: RootNoteConfig,
    key_str: str,
    scale_override: str | None,
) -> int:
    """LFO 0..1 → semitone offset from the part's natural root.

    Uses *key_str* (e.g. ``"Cm"``) and optional *scale_override* to
    pick scale intervals, then maps the LFO value to either a scale
    degree (``mode=degree``) or to a chromatic point in the range
    snapped to the nearest scale tone (``mode=snap``). Result is a
    semitone offset relative to the part's tonic — applied via
    :attr:`PartContext.transpose_semitones`.

    Algorithms can keep doing whatever they normally do (generate
    notes from the part's key) and they'll automatically be
    transposed by the per-bar offset.
    """
    try:
        tonic_pc, default_scale = parse_key(key_str)
    except Exception:
        return 0
    scale_name = scale_override or default_scale
    intervals = SCALES.get(scale_name) or SCALES.get(default_scale)
    if not intervals:
        return 0
    # Tonic MIDI in a low reference octave so all candidates ≥ cfg.lo.
    # We enumerate scale notes between lo..hi and pick one of them.
    # Octave numbering: midi_note(pc, oct) = pc + 12*(oct+1).
    candidates: list[int] = []
    for octave in range(-1, 10):
        base = tonic_pc + 12 * (octave + 1)
        for iv in intervals:
            n = base + iv
            if cfg.lo <= n <= cfg.hi:
                candidates.append(n)
    if not candidates:
        return 0
    candidates.sort()
    v = max(0.0, min(1.0, value_unit))
    if cfg.mode == "degree":
        # LFO indexes scale-tone candidates directly — stepwise melody.
        idx = min(len(candidates) - 1, int(v * len(candidates)))
        target = candidates[idx]
    else:  # snap
        # LFO maps to chromatic in [lo, hi], then snap to nearest tone.
        chromatic = cfg.lo + (cfg.hi - cfg.lo) * v
        target = min(candidates, key=lambda n: abs(n - chromatic))
    # Default part-natural root sits one octave below middle C if not
    # otherwise stated. We compute the offset relative to the tonic
    # at octave 3 (most algorithm bass / mid registers) so a degree-0
    # LFO value lands at a sensible base rather than jumping to the
    # arbitrary lowest in-range note. This makes height < 1 LFOs
    # behave musically.
    natural_root = tonic_pc + 12 * (3 + 1)  # tonic at C3 = MIDI 48
    return target - natural_root


def gen_matches_lfo_scope(
    gen_type: str, gen_handle: str, target_kind: str, scope: str,
) -> bool:
    """True if a gen is affected by an LFO application of *target_kind*.

    Feel targets apply to all gens of the matching ``type_`` (a single
    feel LFO can shape multiple instances of the same voice type).
    Pattern targets apply only to the specific handle.
    """
    if target_kind == "feel_knob":
        return gen_type == scope
    if target_kind == "pattern_knob":
        return gen_handle == scope
    return False
