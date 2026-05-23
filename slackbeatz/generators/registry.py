"""``(type, style)`` → :class:`Generator` subclass registry.

Algorithm modules are imported by :mod:`slackbeatz.generators.__init__`;
the side-effect of importing one is that its :func:`register_generator`
decorator(s) run and populate :data:`REGISTRY`. The scheduler looks up
the class by ``(type, style)`` and instantiates it per resolved gen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import Generator


REGISTRY: dict[tuple[str, str], type["Generator"]] = {}


def register_generator(type_: str, style: str):
    """Decorator that adds a :class:`Generator` subclass to :data:`REGISTRY`.

    Raises :class:`ValueError` on duplicate ``(type, style)`` registrations
    so two classes can't silently shadow each other.
    """

    def deco(cls: type["Generator"]) -> type["Generator"]:
        cls.type_ = type_
        cls.style = style
        key = (type_, style)
        if key in REGISTRY:
            raise ValueError(
                f"duplicate generator registration for {key}: "
                f"already {REGISTRY[key].__qualname__}, now {cls.__qualname__}"
            )
        REGISTRY[key] = cls
        return cls

    return deco


def list_generators() -> list[tuple[str, str]]:
    """Sorted list of registered ``(type, style)`` pairs, for ``list-generators``."""
    return sorted(REGISTRY)
