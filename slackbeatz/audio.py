"""Audio rendering via FluidSynth + ffmpeg.

This is the back-end behind ``slackbeatz audio``. Pipeline::

    ResolvedSong → mido.MidiFile (engine/midifile.py)
                 → temp .mid file on disk
                 → fluidsynth -ni <sf> <mid> -F <wav>      [softsynth render]
                 → (if output is .mp3)
                   ffmpeg -i <wav> -b:a <bitrate> <mp3>    [encode]
                 → clean up temp files

Both ``fluidsynth`` and ``ffmpeg`` are looked up via ``shutil.which`` —
both ship as ``.exe`` on Windows, ``brew`` packages on macOS, and apt /
dnf packages on Linux. We surface install instructions for the user's
platform when either is missing.

Soundfont lookup order is documented on :func:`find_soundfont` — flag →
env var → common install paths → auto-download a small (~6 MB) GM
soundfont into ``~/.cache/slackbeatz/`` on first use.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


# --------------------------------------------------------------------------
# Soundfont discovery
# --------------------------------------------------------------------------

_DEFAULT_SOUNDFONT_NAME = "FluidR3_GM.sf2"
# Tried in order. musical-artifacts.com is the canonical community
# archive but rate-limits / cloudflare-gates Python's default UA, so
# we set a browser-ish header on the request *and* keep the
# github raw mirror as a fallback. Both serve byte-identical 148 MB
# FluidR3_GM.sf2 files (verified via Content-Length 148358590).
_DEFAULT_SOUNDFONT_URLS: tuple[str, ...] = (
    "https://musical-artifacts.com/artifacts/738/FluidR3_GM.sf2",
    "https://github.com/urish/cinto/raw/master/media/FluidR3%20GM.sf2",
)
# Browser-ish UA — bare "Python-urllib/X.Y" trips 403 on
# musical-artifacts. We don't try to look like a real browser, we just
# want past the obvious bot block.
_DOWNLOAD_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 slackbeatz/1.0"
)
_CACHE_DIR = Path.home() / ".cache" / "slackbeatz"

# Paths we'll silently pick up if a soundfont is already there. Ordered
# by likelihood + quality — the full FluidR3_GM stereo SF (Homebrew
# fluid-synth bundles it on some systems) trumps the cached compressed
# default. Then the legacy GeneralUser cache so existing users don't
# have to redownload if they liked it.
_COMMON_SOUNDFONT_PATHS: tuple[Path, ...] = (
    # macOS (Homebrew on Apple Silicon) — stereo FluidR3_GM if present.
    Path("/opt/homebrew/share/sounds/sf2/FluidR3_GM.sf2"),
    Path("/opt/homebrew/share/soundfonts/default.sf2"),
    # macOS (Homebrew on Intel).
    Path("/usr/local/share/sounds/sf2/FluidR3_GM.sf2"),
    Path("/usr/local/share/soundfonts/default.sf2"),
    # Linux (Debian/Ubuntu via fluid-soundfont-gm).
    Path("/usr/share/sounds/sf2/FluidR3_GM.sf2"),
    Path("/usr/share/sounds/sf2/TimGM6mb.sf2"),
    Path("/usr/share/soundfonts/default.sf2"),
    # Cached FluidR3_GM.sf2 stereo (auto-downloaded default for slackbeatz
    # ≥ this commit). Stereo wins over the compressed mono variant if
    # both happen to be present.
    Path.home() / ".cache" / "slackbeatz" / "FluidR3_GM.sf2",
    # Compressed mono variant of the same FluidR3 patches — acceptable
    # fallback that saves users from re-downloading 148 MB if they have
    # the smaller file already cached.
    Path.home() / ".cache" / "slackbeatz" / "FluidR3Mono_GM.sf3",
    # Legacy GeneralUser cache from earlier slackbeatz versions — still
    # usable, picked up only if no FluidR3 variant is reachable.
    Path.home() / ".cache" / "slackbeatz" / "GeneralUser-GS.sf2",
)


class SoundfontError(RuntimeError):
    """Raised when a soundfont can't be found or downloaded."""


def find_soundfont(override: str | os.PathLike | None = None) -> Path:
    """Resolve a soundfont path. Auto-downloads a default GM SF if none
    is configured and none can be discovered.

    Order:

    1. *override* if provided (typically from ``--soundfont``).
    2. ``$SLACKBEATZ_SOUNDFONT`` if set and pointing at an existing file.
    3. Any of :data:`_COMMON_SOUNDFONT_PATHS` that exists.
    4. The cached :data:`_DEFAULT_SOUNDFONT_NAME` if present.
    5. Downloads the default soundfont and caches it.

    Raises :class:`SoundfontError` if a download is required but fails.
    """
    if override is not None:
        p = Path(override)
        if not p.is_file():
            raise SoundfontError(f"soundfont not found: {p}")
        return p

    env = os.environ.get("SLACKBEATZ_SOUNDFONT")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        # If the env var is set but invalid, fail loudly — the user told
        # us where to look and got it wrong; auto-falling-back would be
        # surprising.
        raise SoundfontError(
            f"$SLACKBEATZ_SOUNDFONT points at non-existent file: {p}"
        )

    for c in _COMMON_SOUNDFONT_PATHS:
        if c.is_file():
            return c

    cached = _CACHE_DIR / _DEFAULT_SOUNDFONT_NAME
    if cached.is_file():
        return cached

    return _download_default_soundfont()


def _download_default_soundfont() -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = _CACHE_DIR / _DEFAULT_SOUNDFONT_NAME
    print(
        f"slackbeatz: downloading default soundfont (~148 MB) to {dest} ...",
        file=sys.stderr,
        flush=True,
    )
    errors: list[str] = []
    for url in _DEFAULT_SOUNDFONT_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _DOWNLOAD_UA})
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
            # Sanity-check: SF2 files start with "RIFF....sfbk". If a CDN
            # returns an HTML error page we'd otherwise cache it and
            # FluidSynth would explode on the next render.
            if not (data[:4] == b"RIFF" and data[8:12] == b"sfbk"):
                errors.append(
                    f"{url}: response is not a valid SF2 "
                    f"(first 16 bytes: {data[:16]!r})"
                )
                continue
            dest.write_bytes(data)
            print(
                f"slackbeatz: saved soundfont ({len(data) // 1024} KB) from {url}.",
                file=sys.stderr,
            )
            return dest
        except Exception as e:  # noqa: BLE001 — log + try next mirror
            errors.append(f"{url}: {e}")
            print(
                f"slackbeatz: mirror failed ({url}: {e}), trying next ...",
                file=sys.stderr,
            )
    raise SoundfontError(
        "failed to download default soundfont from any mirror:\n  "
        + "\n  ".join(errors)
        + "\nPass --soundfont <path> to a local .sf2/.sf3 file instead."
    )


# --------------------------------------------------------------------------
# External tool discovery
# --------------------------------------------------------------------------

class MissingToolError(RuntimeError):
    """Raised when an external CLI (fluidsynth, ffmpeg) isn't on PATH."""


def _platform_install_hint(tool: str) -> str:
    """One-line install hint, picked per platform."""
    if sys.platform == "darwin":
        pkg = "fluid-synth" if tool == "fluidsynth" else tool
        return f"brew install {pkg}"
    if sys.platform.startswith("linux"):
        return f"apt install {tool}  (or dnf install {tool}, etc.)"
    if sys.platform.startswith("win"):
        return f"choco install {tool}  (or scoop install {tool})"
    return f"install {tool} and put it on PATH"


def require_tool(name: str) -> str:
    """Return the full path of *name* on PATH, or raise with install hint."""
    path = shutil.which(name)
    if path is None:
        raise MissingToolError(
            f"{name} not found in PATH. Install via:\n"
            f"  {_platform_install_hint(name)}"
        )
    return path


# --------------------------------------------------------------------------
# Render pipeline
# --------------------------------------------------------------------------

def render_audio(
    midi_path: Path,
    output_path: Path,
    soundfont: Path,
    *,
    sample_rate: int = 44100,
    bitrate: str = "192k",
) -> None:
    """Synthesise *midi_path* via FluidSynth and write *output_path*.

    Output format is dispatched on the extension: ``.wav`` ends after
    FluidSynth, ``.mp3`` (or anything else ffmpeg knows) goes through
    an additional ffmpeg encode step using a temp WAV.

    Raises :class:`MissingToolError` if fluidsynth (or ffmpeg, for
    non-WAV output) isn't on PATH, and :class:`subprocess.CalledProcessError`
    if a subprocess fails.
    """
    fluidsynth = require_tool("fluidsynth")
    ext = output_path.suffix.lower()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _run_fluidsynth(wav_path: Path) -> None:
        # --fast-render is the dedicated off-line mode in FluidSynth 2.x.
        # `-F` (the older flag) still tries to open a real-time audio
        # driver alongside file output and can hang indefinitely if one
        # isn't available; --fast-render skips the audio driver entirely
        # and runs as fast as the CPU allows (typically 50-100x realtime).
        # -ni  : no shell, no MIDI input.
        # -r   : sample rate.
        # -q   : quiet — suppress the banner.
        subprocess.run(
            [
                fluidsynth, "-ni", "-q",
                "-r", str(sample_rate),
                f"--fast-render={wav_path}",
                str(soundfont), str(midi_path),
            ],
            check=True,
            stdin=subprocess.DEVNULL,
        )

    if ext == ".wav":
        _run_fluidsynth(output_path)
        return

    # Non-WAV: render to a temp WAV, then encode.
    ffmpeg = require_tool("ffmpeg")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = Path(tmp.name)
    try:
        _run_fluidsynth(tmp_wav)
        subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "warning",
                "-i", str(tmp_wav),
                "-b:a", bitrate, str(output_path),
            ],
            check=True,
        )
    finally:
        tmp_wav.unlink(missing_ok=True)
