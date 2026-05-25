"""Universal Feel knobs — the post-emit / per-bar humanisation layer.

"Feel" is the set of knobs that apply uniformly to **every** algorithm:
small variations in timing, velocity, pitch, gate, and per-bar muting
that turn a perfect-grid sequence into something that breathes. They are
distinct from **Pattern** knobs, which are algorithm-specific (swing,
gate, voicing, progression, density, octave, …) and shape the actual
note content the algorithm emits.

This module is the **single source of truth** for the Feel knob set. It
declares each knob's spec (name, low/high range, default, kind) so other
parts of the codebase — the per-part overrides parser (Phase C), the
mixer/scene state serialiser (Phase D), and the GUI's three-tier
drilldown picker (Phase E) — can iterate the set without re-listing it.

# Current consumption model (transitional)

Today every algorithm reads its own Feel-knob values from the gen's
``knobs`` dict, with defaults from
:mod:`slackbeatz.generators.defaults`, and applies them via the helpers
in :mod:`slackbeatz.generators._shared` (``humanize_hit``,
``apply_gate_jitter``, ``maybe_octave_jump``, ``maybe_passing_tone``,
``should_mute_bar``, ``evolution_multiplier``, ``apply_mistake``).
Generators that *don't* call these helpers silently lack the
corresponding knob behaviour even though the knob is still accepted on
the gen line.

# Future direction

A follow-up phase will:

1. Replace the per-algorithm helper calls with a single scheduler-level
   :func:`slackbeatz.engine.feel_apply.apply_feel` post-emit pass that
   mutates each generator's emitted events uniformly.
2. Move per-bar Feel logic (``mute_prob``, ``evolution``) into a
   pre-generator hook in the scheduler so all algorithms participate.
3. Re-render the example corpus + update byte-identical CI hashes.

That refactor will change rendered output (event-mutation ordering
shifts) and needs an A/B-listening pass on the example corpus before
acceptance. It is deliberately *not* done here — this module + the
:mod:`slackbeatz.engine.feel_apply` no-op stub establish the call site
and the registry without breaking byte-identical output today.

# What's not in Feel

``density_drift`` lives in :mod:`slackbeatz.generators.defaults` and is
read by every rhythm generator, but it perturbs euclidean pulse counts —
a Pattern-tier concept specific to rhythm gens. It is **not** a Feel
knob in this redesign. Pitched algorithms have no equivalent semantics
(no "pulse count" to drift).

``drop_prob`` is similar — it's a per-hit drop probability used by
rhythm/drums gens. It's intentionally not in the universal set; rhythm
algorithms keep it as a Pattern knob.

``accent`` is a rhythm Pattern knob (every Nth step gets a velocity
boost) — not Feel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FeelKind = Literal["per_event", "per_note", "per_bar"]


@dataclass(frozen=True)
class FeelSpec:
    """Declarative spec for one universal Feel knob.

    * **per_event** knobs mutate a single emitted MIDI event (tick,
      velocity, or pitch on a note_on). Example: ``humanize`` shifts
      tick; ``vel_jitter`` shifts velocity.
    * **per_note** knobs mutate a paired (note_on, note_off) such that
      both endpoints must move together. Example: ``gate_jitter`` adjusts
      the note duration by moving note_off.
    * **per_bar** knobs operate on a per-bar basis rather than per-event.
      Example: ``mute_prob`` rolls once per bar to skip the whole bar.
    """

    name: str
    low: float
    high: float
    default: float
    kind: FeelKind
    summary: str


FEEL_KNOBS: tuple[FeelSpec, ...] = (
    FeelSpec(
        name="humanize",
        low=0, high=20, default=0, kind="per_event",
        summary="±N tick offset per emitted event; 0 = on the grid.",
    ),
    FeelSpec(
        name="vel_jitter",
        low=0, high=30, default=8, kind="per_event",
        summary="±N velocity-point random shift per note_on.",
    ),
    FeelSpec(
        name="gate_jitter",
        low=0.0, high=1.0, default=0.0, kind="per_note",
        summary="±fraction of note duration; 0.3 = each note rolls "
                "duration in roughly [base*0.7, base*1.3].",
    ),
    FeelSpec(
        name="mute_prob",
        low=0.0, high=1.0, default=0.0, kind="per_bar",
        summary="Per-bar chance the whole bar is dropped (no events).",
    ),
    FeelSpec(
        name="octave_jump",
        low=0.0, high=1.0, default=0.0, kind="per_event",
        summary="Per-event chance the pitch shifts ±12 semitones.",
    ),
    FeelSpec(
        name="passing_tones",
        low=0.0, high=1.0, default=0.0, kind="per_event",
        summary="Per-event chance the pitch is replaced with a "
                "chromatic neighbour (±1 semitone).",
    ),
    FeelSpec(
        name="evolution",
        low=0.0, high=1.0, default=0.0, kind="per_bar",
        summary="Linear velocity ramp across the part — multiplier "
                "moves in [1-evolution, 1+evolution] from first to "
                "last bar (direction picked per part instance).",
    ),
    FeelSpec(
        name="mistakes",
        low=0.0, high=0.1, default=0.0, kind="per_event",
        summary="Per-note chance of a small random perturbation in "
                "pitch / tick / velocity (one dimension at a time).",
    ),
)


FEEL_KNOB_NAMES: frozenset[str] = frozenset(s.name for s in FEEL_KNOBS)
"""Set of knob names that belong to the universal Feel tier.

Phase C uses this to validate per-part Feel-knob overrides; Phase E uses
it to render the Feel section of the per-(voice, part) detail pane
uniformly across algorithms.
"""


def is_feel_knob(name: str) -> bool:
    """True if *name* is one of the universal Feel knobs."""
    return name in FEEL_KNOB_NAMES


def feel_spec(name: str) -> FeelSpec | None:
    """Return the :class:`FeelSpec` for *name*, or ``None`` if not a Feel knob."""
    for spec in FEEL_KNOBS:
        if spec.name == name:
            return spec
    return None
