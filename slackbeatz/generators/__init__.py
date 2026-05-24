"""Generator package — importing this triggers all algorithm registrations.

Each ``type`` subpackage is imported in turn, which in turn imports its
``style`` modules. Their ``@register_generator`` decorators run as a
side-effect, populating :data:`slackbeatz.generators.registry.REGISTRY`.
"""

from . import rhythm, drums, bass, melody, chords, candy  # noqa: F401
from . import speech, sample  # noqa: F401 — TTS + sampler gen types
