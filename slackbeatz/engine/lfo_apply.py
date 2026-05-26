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

from typing import Any

from slackbeatz.generators.feel import FEEL_KNOBS
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
