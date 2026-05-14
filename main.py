"""Compatibility entrypoint.

The deployable API backend now lives under ``web_api/script``.  This module is
kept so existing commands such as ``uvicorn web_api.main:app`` continue to work.
"""

from .script.roma_web_search_api.main import *  # noqa: F401,F403
