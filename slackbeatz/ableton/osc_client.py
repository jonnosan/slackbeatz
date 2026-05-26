"""AbletonOSC client — small wrapper around the bits SB actually uses.

[AbletonOSC](https://github.com/ideoforms/AbletonOSC) is a free
Max-for-Live device that exposes Live's Object Model over OSC. SB
uses it to push macro values into the Instrument Racks the user has
built on each role-track.

Today's surface (all that's needed for the macro-preset push):

* :meth:`AbletonOscClient.get_track_names` — async query → list of
  track names so SB can find "the bass track" by name.
* :meth:`AbletonOscClient.set_device_parameter` — fire-and-forget
  ``/live/device/set/parameter/value <track> <device> <param> <value>``.

AbletonOSC default port: 11000 send / 11001 receive (matches
the device's default config).

Connection model: fire-and-forget UDP. If AbletonOSC isn't running,
sends silently disappear — the caller surfaces "couldn't reach
AbletonOSC" by checking the track-name query reply.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


# AbletonOSC's default ports (configurable in the M4L device).
DEFAULT_SEND_PORT = 11000
DEFAULT_RECV_PORT = 11001


class AbletonOscClient:
    """Minimal AbletonOSC client.

    Construct with the default ports; call :meth:`connect` to open
    the listener thread (needed for query replies); then call the
    high-level methods.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        send_port: int = DEFAULT_SEND_PORT,
        recv_port: int = DEFAULT_RECV_PORT,
    ) -> None:
        self.host = host
        self.send_port = send_port
        self.recv_port = recv_port
        self._client = None
        self._server = None
        self._server_thread: Optional[threading.Thread] = None
        # Single-shot reply slot for the track-name query (sufficient
        # for the push use case — we don't pipeline queries).
        self._track_names_reply: Optional[list[str]] = None
        self._reply_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the UDP client + reply listener. Idempotent."""
        if self._client is not None:
            return
        from pythonosc import dispatcher as _disp
        from pythonosc import osc_server as _osc_server
        from pythonosc import udp_client as _udp

        self._client = _udp.SimpleUDPClient(self.host, self.send_port)
        disp = _disp.Dispatcher()
        disp.map("/live/song/get/track_names", self._on_track_names)
        self._server = _osc_server.ThreadingOSCUDPServer(
            ("127.0.0.1", self.recv_port), disp,
        )
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
        )
        self._server_thread.start()

    def close(self) -> None:
        """Shut down the listener. Idempotent."""
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None
        self._client = None
        self._server_thread = None

    # ------------------------------------------------------------------
    # Track lookup
    # ------------------------------------------------------------------

    def _on_track_names(self, _addr: str, *args) -> None:
        with self._reply_lock:
            self._track_names_reply = [str(a) for a in args]

    def get_track_names(self, *, timeout_s: float = 1.5) -> list[str]:
        """Query Ableton for the current Live Set's track names.

        Returns ``[]`` if AbletonOSC doesn't reply within *timeout_s*
        (most commonly because the M4L device isn't loaded, or
        Ableton isn't running). Caller surfaces the empty-list case
        as "couldn't reach AbletonOSC".
        """
        if self._client is None:
            return []
        with self._reply_lock:
            self._track_names_reply = None
        try:
            self._client.send_message("/live/song/get/track_names", [])
        except OSError:
            return []
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._reply_lock:
                if self._track_names_reply is not None:
                    return list(self._track_names_reply)
            time.sleep(0.02)
        return []

    # ------------------------------------------------------------------
    # Parameter writes
    # ------------------------------------------------------------------

    def set_device_parameter(
        self, track: int, device: int, parameter: int, value: float,
    ) -> None:
        """Fire-and-forget ``/live/device/set/parameter/value``.

        Track / device / parameter are 0-indexed. Macros on an
        Instrument Rack are at parameter indices 1..8 (index 0 is
        the rack's on/off toggle).

        *value* is the raw 0..1 macro value. AbletonOSC scales it
        to whatever range the underlying parameter expects.
        """
        if self._client is None:
            return
        try:
            self._client.send_message(
                "/live/device/set/parameter/value",
                [track, device, parameter, float(value)],
            )
        except OSError:
            pass
