#!/usr/bin/env python3
"""
Convenience entrypoint: ``python run_server.py``

Equivalent to ``python -m mcp_server``.
"""

import sys
import os

# Ensure project root is on sys.path so local imports resolve
sys.path.insert(0, os.path.dirname(__file__))

from mcp_server.server import main

if __name__ == "__main__":
    main()
