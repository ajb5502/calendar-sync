#!/usr/bin/env python3
"""Calendar Sync — OpenClaw skill for cross-provider calendar synchronization."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cli import main

if __name__ == "__main__":
    main()
