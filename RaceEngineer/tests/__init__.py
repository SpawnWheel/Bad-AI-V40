"""
Tests package for the AI Race Engineer (AMS2 / RaceRoom).

Learned data (track maps, pit model, stats, setup journal) is redirected to a temp folder on import,
so `python -m unittest discover` can't write into ./data either (conftest.py only runs under pytest).
"""
from .conftest import isolate_learned_data

isolate_learned_data()
