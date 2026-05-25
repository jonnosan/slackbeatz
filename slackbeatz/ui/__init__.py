"""Slackbeatz UI package — the home for the redesigned GUI shell.

Phase E (per the redesign plan at
``~/.claude/plans/i-want-to-redesign-glistening-spark.md``) retires the
4092-line :mod:`slackbeatz.gui` notebook architecture in favour of a
collection of focused screen modules under this package: ``welcome``,
``arrangement``, ``mixer``, ``setup_editor``, plus shared helpers
(``transport``, ``scope_drilldown``, ``state``, ``voice_picker``).

Phase E proper is the largest single change in the redesign and lands
as a dedicated PR after Phases A–D merge. This package starts with the
non-UI plumbing the future screens will need (state persistence,
stale-file detection) so the eventual UI work can focus on the actual
widget design.
"""
