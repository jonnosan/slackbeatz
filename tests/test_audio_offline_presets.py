"""Tests for the audio-offline preset layer.

Validates the per-(role, style) preset table itself (registered
combos, well-formed entries). The actual ``set_parameter`` /
``set_automation`` integration with the Surge XT VST3 isn't exercised
here — it needs the dawdreamer + Surge install which aren't part of
the standard test environment. The integration is verified by
hand-running ``audio --setup surge`` and spectral-analysing the
output (see ``examples/rendered/`` for the listening corpus).
"""

from __future__ import annotations

from slackbeatz.audio_offline_presets import (
    CC_TO_PARAM_NAME,
    ROLE_STYLE_PRESETS,
    apply_preset,
)


# --------------------------------------------------------------------------
# Preset table well-formedness
# --------------------------------------------------------------------------

def test_acid_bass_preset_registered() -> None:
    assert ("bass", "acid_303") in ROLE_STYLE_PRESETS


def test_acid_pad_preset_registered() -> None:
    assert ("pad", "acid_stab") in ROLE_STYLE_PRESETS


def test_acid_candy_preset_registered() -> None:
    assert ("candy", "acid_sweep") in ROLE_STYLE_PRESETS


def test_all_presets_have_filter_type_set() -> None:
    """Every preset should at least turn on a filter — the whole
    point of the layer is to escape Surge's init-state Filter: Off."""
    for key, preset in ROLE_STYLE_PRESETS.items():
        names = {name for name, _value in preset}
        assert "A Filter 1 Type" in names, f"{key} preset doesn't set Filter 1 Type"


def test_all_preset_values_are_in_unit_range() -> None:
    """Parameter values must be normalised to [0, 1] — dawdreamer's
    ``set_parameter`` takes the normalised form."""
    for key, preset in ROLE_STYLE_PRESETS.items():
        for name, value in preset:
            assert 0.0 <= value <= 1.0, (
                f"{key} preset has {name}={value} out of [0,1]"
            )


def test_all_preset_param_names_are_strings() -> None:
    for key, preset in ROLE_STYLE_PRESETS.items():
        for name, _value in preset:
            assert isinstance(name, str) and name
            # Surge uses "A " / "B " prefixes for scenes; we expect
            # scene A for everything today.
            assert name.startswith(("A ", "B "))


# --------------------------------------------------------------------------
# apply_preset behaviour with a stub synth
# --------------------------------------------------------------------------

class _StubSynth:
    """Minimal stand-in for dawdreamer.PluginProcessor for unit tests.

    Tracks every set_parameter call so the test can assert which
    params got written.
    """

    def __init__(self, param_names: list[str]) -> None:
        self._params = [
            {"name": n, "index": i} for i, n in enumerate(param_names)
        ]
        self.writes: list[tuple[int, float]] = []

    def get_plugin_parameters_description(self):
        return self._params

    def set_parameter(self, idx: int, value: float) -> None:
        self.writes.append((idx, value))


def test_apply_preset_returns_true_when_preset_exists() -> None:
    synth = _StubSynth([
        "A Filter 1 Type", "A Filter 1 Cutoff", "A Filter 1 Resonance",
        "A Filter 1 FEG Mod Amount", "A Filter 1 Keytrack",
        "A Filter EG Attack", "A Filter EG Decay",
        "A Filter EG Sustain", "A Filter EG Release",
        "A Filter 2 Type",
    ])
    assert apply_preset(synth, "bass", "acid_303") is True
    # Every preset param maps to one set_parameter call.
    preset = ROLE_STYLE_PRESETS[("bass", "acid_303")]
    assert len(synth.writes) == len(preset)


def test_apply_preset_returns_false_for_unknown_pair() -> None:
    synth = _StubSynth(["A Filter 1 Type"])
    assert apply_preset(synth, "bass", "unknown_algorithm") is False
    assert synth.writes == []


def test_apply_preset_silently_skips_missing_param_names() -> None:
    # Synth that's missing a few of the preset's params should still
    # accept the preset (apply what it can) rather than crash.
    synth = _StubSynth(["A Filter 1 Type", "A Filter 1 Cutoff"])
    assert apply_preset(synth, "bass", "acid_303") is True
    # Only the 2 known names got writes.
    assert len(synth.writes) == 2


def test_apply_preset_writes_filter_type_first_useful_value() -> None:
    """Filter Type 0.1 = LP Legacy Ladder — the 303 character."""
    synth = _StubSynth(["A Filter 1 Type"])
    apply_preset(synth, "bass", "acid_303")
    assert synth.writes[0] == (0, 0.1)


# --------------------------------------------------------------------------
# CC → parameter mapping
# --------------------------------------------------------------------------

def test_cc_to_param_includes_cc74_and_cc71() -> None:
    # The two CCs acid_303's built-in LFO emits.
    assert CC_TO_PARAM_NAME[74] == "A Filter 1 Cutoff"
    assert CC_TO_PARAM_NAME[71] == "A Filter 1 Resonance"


def test_cc_to_param_target_names_are_strings() -> None:
    for cc, name in CC_TO_PARAM_NAME.items():
        assert isinstance(cc, int) and 0 <= cc <= 127
        assert isinstance(name, str) and name.startswith(("A ", "B "))
