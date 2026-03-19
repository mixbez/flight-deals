"""
Pytest configuration for flight-deals tests.

main.py writes to /app/startup.txt and /app/bot.log at import time (Docker paths).
We patch builtins.open and logging.FileHandler before importing so tests work outside Docker.
"""

import builtins
import logging
import unittest.mock as mock

# Intercept /app/* file writes that happen at module import time
_real_open = builtins.open

def _patched_open(file, *args, **kwargs):
    if str(file).startswith("/app/"):
        return mock.mock_open()()
    return _real_open(file, *args, **kwargs)

builtins.open = _patched_open

# Prevent logging.FileHandler from trying to open /app/bot.log
logging.FileHandler = lambda *args, **kwargs: logging.NullHandler()
