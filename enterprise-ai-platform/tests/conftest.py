"""
conftest.py — Adds project root to sys.path so all modules resolve correctly.
This file must live at the project root (alongside agents/, security/, rag/, etc.)
"""
import sys
import os

# Ensure project root is first on the path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Also add backend/ so app.* imports work in integration tests
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(1, BACKEND)
