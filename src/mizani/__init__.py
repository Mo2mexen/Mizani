"""Mizani: offline triage co-pilot. Rules decide, the model assists, a named nurse approves."""

from importlib.metadata import version

# One source for the version: pyproject.toml. The page and /api/health show it.
__version__ = version("mizani")
