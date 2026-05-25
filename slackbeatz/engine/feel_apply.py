"""Post-emit Feel pass — placeholder for the future scheduler-side hoist.

Today the per-hit Feel work is woven into each generator (the generator
calls :func:`slackbeatz.generators._shared.humanize_hit` and friends,
emitting notes that are already velocity-jittered, humanize-shifted,
etc.). A future phase will hoist that work out of the generators and
into this single post-emit pass — every algorithm emits clean
on-grid / perfect-pitch events, then :func:`apply_feel` mutates the
event list uniformly.

The advantage: the universal Feel set (declared in
:mod:`slackbeatz.generators.feel`) is *guaranteed* to apply to every
algorithm without each generator opting in. Today, a generator that
forgets to call ``humanize_hit`` silently loses the humanise / vel_jitter
behaviour even though the gen line still accepts those knobs.

The cost: hoisting changes the order of event-mutation steps, which
shifts every random-number draw downstream. Identical-seed renders will
sound subtly different. The byte-identical CI corpus
(``tests/test_byte_identical_after_refactor.py``) will need its hashes
regenerated, and the bundled example MP3s should be A/B-listened against
the old renders before acceptance.

This module currently ships :func:`apply_feel` as a **no-op** so the
scheduler call site exists and downstream phases (Phase C per-part
overrides, Phase D scene state, Phase E GUI) can wire against a stable
function signature. The actual hoist + corpus regeneration is a
follow-up task.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import random
    from collections.abc import Iterable, Iterator

    from slackbeatz.engine.event import Event
    from slackbeatz.model.context import PartContext


def apply_feel(
    events: "Iterable[Event]",
    feel: dict[str, object],
    rng: "random.Random",
    ctx: "PartContext",
) -> "Iterator[Event]":
    """Post-emit Feel pass — currently a no-op pass-through.

    A future implementation will mutate the event stream per the
    universal Feel knobs declared in
    :mod:`slackbeatz.generators.feel`:

    * **per_event** knobs (``humanize``, ``vel_jitter``, ``octave_jump``,
      ``passing_tones``, ``mistakes``) mutate each emitted event.
    * **per_note** knobs (``gate_jitter``) mutate paired note_on /
      note_off so the duration changes coherently.
    * **per_bar** knobs (``mute_prob``, ``evolution``) drop or scale
      whole-bar event groups.

    Today this is a generator that yields events unchanged so the call
    site can land without changing rendered output. When the hoist
    happens, the generators stop calling ``humanize_hit`` etc.
    themselves and this function does the work.

    The *feel* dict is the cascaded effective value for each knob —
    Engine default → Style profile → Song-level → Voice → Part. The
    scheduler computes it before calling :func:`apply_feel`.
    """
    yield from events
