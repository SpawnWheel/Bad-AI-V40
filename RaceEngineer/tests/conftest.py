"""Keep tests from writing learned data (track maps, pit model, stats, setup journal) into ./data."""
import tempfile
from pathlib import Path

try:
    import pytest
except ImportError:  # plain unittest run: tests/__init__.py calls isolate_learned_data() itself
    pytest = None

_TMP = Path(tempfile.mkdtemp(prefix="ams2_eng_tests_"))


def isolate_learned_data():
    try:
        from race.track_model import track_model
        track_model.storage_path = _TMP / "track_info.json"
        track_model._db = {}
        track_model.info = None
    except Exception:
        pass
    try:
        from race.pit_stop_tracker import pit_stop_tracker
        pit_stop_tracker.storage_path = _TMP / "pit_stops_v2.json"
        pit_stop_tracker._db = {"version": 2, "tracks": {}, "cars": {}}
    except Exception:
        pass
    try:
        from race.car_track_stats import car_track_stats
        car_track_stats.path = _TMP / "car_track_stats.json"
        car_track_stats._db = {}
    except Exception:
        pass
    try:
        from practice.setup_journal import setup_journal
        setup_journal.path = _TMP / "setup_journal.json"
        setup_journal._db = {"cars": {}, "sessions": {}}
    except Exception:
        pass


if pytest is not None:
    @pytest.fixture(autouse=True, scope="session")
    def _isolate_learned_data():
        isolate_learned_data()
        yield
