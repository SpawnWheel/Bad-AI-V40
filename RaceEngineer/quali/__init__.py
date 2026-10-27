"""
Qualifying module for AMS2 AI Race Engineer.
Tracks qualifying runs, excludes pit outlaps, evaluates leaderboard position,
and pinpoints sector-by-sector speed (where the driver is fast vs slow).
"""

from .tracker import QualiTracker, QualiRunRecord

__all__ = ["QualiTracker", "QualiRunRecord"]
