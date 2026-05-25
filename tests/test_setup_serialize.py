"""Tests for :mod:`slackbeatz.setup.serialize` — round-trip Setup → .sb."""

from __future__ import annotations

from slackbeatz.dsl.parser import parse
from slackbeatz.setup.loader import setup_from_ast
from slackbeatz.setup.model import Instrument, Kit, Setup
from slackbeatz.setup.serialize import emit_setup


def test_emit_minimal_setup() -> None:
    setup = Setup(
        name="Test",
        instruments={
            "bass": Instrument(name="bass", channel=2, note=None),
        },
        kits={},
    )
    text = emit_setup(setup)
    assert text == 'setup "Test"\ninst bass ch=2\n'


def test_emit_drum_instrument_includes_note() -> None:
    setup = Setup(
        name="Test",
        instruments={
            "kick": Instrument(name="kick", channel=10, note=36),
        },
        kits={},
    )
    text = emit_setup(setup)
    assert 'inst kick ch=10 note=36' in text


def test_emit_kit_with_drum_notes_indented() -> None:
    setup = Setup(
        name="T",
        instruments={},
        kits={
            "kit": Kit(name="kit", channel=10, drum_notes={"kick": 36, "snare": 38}),
        },
    )
    text = emit_setup(setup)
    assert "kit kit ch=10" in text
    assert "  kick 36" in text
    assert "  snare 38" in text


def test_emit_preserves_insertion_order() -> None:
    # Python dicts maintain insertion order; the serialiser must
    # honour that so the GUI's "instruments in this order" UI choice
    # round-trips.
    setup = Setup(
        name="T",
        instruments={
            "lead": Instrument(name="lead", channel=1, note=None),
            "bass": Instrument(name="bass", channel=2, note=None),
            "pad":  Instrument(name="pad",  channel=3, note=None),
        },
        kits={},
    )
    text = emit_setup(setup)
    lines = [l for l in text.splitlines() if l.startswith("inst")]
    assert lines == [
        "inst lead ch=1",
        "inst bass ch=2",
        "inst pad ch=3",
    ]


# --------------------------------------------------------------------------
# Round-trip — parse → setup_from_ast → emit → parse → setup_from_ast
# --------------------------------------------------------------------------

def _roundtrip(src: str) -> Setup:
    """Helper: parse the source, build a Setup, emit, re-parse, return."""
    original = setup_from_ast(parse(src).setup)
    emitted = emit_setup(original)
    return setup_from_ast(parse(emitted).setup)


def test_roundtrip_preserves_simple_setup() -> None:
    original = setup_from_ast(parse(
        'setup "S"\n'
        'inst bass ch=2\n'
        'inst lead ch=4\n'
    ).setup)
    again = _roundtrip(
        'setup "S"\n'
        'inst bass ch=2\n'
        'inst lead ch=4\n'
    )
    assert again == original


def test_roundtrip_preserves_drum_instruments() -> None:
    original = setup_from_ast(parse(
        'setup "S"\n'
        'inst kick  ch=10 note=36\n'
        'inst snare ch=10 note=38\n'
        'inst bass  ch=2\n'
    ).setup)
    again = _roundtrip(
        'setup "S"\n'
        'inst kick  ch=10 note=36\n'
        'inst snare ch=10 note=38\n'
        'inst bass  ch=2\n'
    )
    assert again == original


def test_roundtrip_preserves_kit_drum_map() -> None:
    # Kit referenced via preset=gm — drum_notes get materialised on
    # load, so the round-trip emits them explicitly. Equality on the
    # Kit's drum_notes dict guarantees a clean round-trip.
    original = setup_from_ast(parse(
        'setup "S"\n'
        'inst bass ch=2\n'
        'kit kit ch=10 preset=gm\n'
    ).setup)
    again = _roundtrip(
        'setup "S"\n'
        'inst bass ch=2\n'
        'kit kit ch=10 preset=gm\n'
    )
    assert again == original
    assert again.kits["kit"].drum_notes == original.kits["kit"].drum_notes


def test_roundtrip_preserves_kit_with_per_drum_overrides() -> None:
    # The user has overridden a drum note away from the gm preset —
    # the override should survive the round-trip.
    src = (
        'setup "S"\n'
        'inst bass ch=2\n'
        'kit kit ch=10 preset=gm\n'
        '  kick 35\n'  # override kick from 36 → 35
    )
    original = setup_from_ast(parse(src).setup)
    again = _roundtrip(src)
    assert again.kits["kit"].drum_notes["kick"] == 35
    assert again == original


def test_emitted_setup_is_well_formed_for_re_parse() -> None:
    # Whatever the serialiser produces should be valid .sb syntax —
    # the parser shouldn't raise on it.
    setup = Setup(
        name="T",
        instruments={
            "bass": Instrument(name="bass", channel=2, note=None),
            "kick": Instrument(name="kick", channel=10, note=36),
        },
        kits={
            "kit": Kit(name="kit", channel=10, drum_notes={"kick": 36}),
        },
    )
    text = emit_setup(setup)
    # No exception means the parser accepts the output.
    parse(text)
