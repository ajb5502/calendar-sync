"""Shared pytest configuration and fixtures."""

import os
import sys

# Ensure scripts/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
