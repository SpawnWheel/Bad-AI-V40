"""
Car / class names for RaceRoom.

R3E shared memory only carries numeric ids (model_id, class_id). The game ships the id -> name table as
GameData/General/r3e-data.json in its install folder; it's found next to the running RRRE64.exe, in the
usual Steam folders, or at telemetry.r3e_data_path in config.yaml.
"""

import json
import time
from pathlib import Path
from typing import Dict, Optional

from utils.logging import logger
from .sim_detect import SIM_R3E, sim_exe_path

REL_PATH = Path("GameData") / "General" / "r3e-data.json"
STEAM_ROOTS = [
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
    r"D:\Steam", r"D:\SteamLibrary", r"E:\Steam", r"E:\SteamLibrary",
]


def _candidates(configured: Optional[str]):
    if configured:
        yield Path(configured)
    exe = sim_exe_path(SIM_R3E)
    if exe:
        p = Path(exe).parent
        for _ in range(4):  # ...\Game\x64\RRRE64.exe -> ...\Game\GameData\General
            yield p / REL_PATH
            yield p / "Game" / REL_PATH
            p = p.parent
    for root in STEAM_ROOTS:
        yield Path(root) / "steamapps" / "common" / "raceroom racing experience" / "Game" / REL_PATH


def _ci(d: dict, key: str):
    """Case-insensitive dict lookup (the file has used both 'Name' and 'name')."""
    for k, v in d.items():
        if str(k).lower() == key:
            return v
    return None


def _id_name_table(section) -> Dict[int, str]:
    out: Dict[int, str] = {}
    items = section.items() if isinstance(section, dict) else enumerate(section or [])
    for k, v in items:
        if not isinstance(v, dict):
            continue
        name = _ci(v, "name")
        ident = _ci(v, "id")
        try:
            ident = int(ident if ident is not None else k)
        except (TypeError, ValueError):
            continue
        if name:
            out[ident] = str(name).strip()
    return out


class R3EData:
    RETRY_SEC = 60.0

    def __init__(self, configured_path: Optional[str] = None):
        self.configured_path = configured_path
        self.cars: Dict[int, str] = {}
        self.classes: Dict[int, str] = {}
        self.path: Optional[Path] = None
        self._next_try = 0.0

    def load(self) -> bool:
        """Loads the table once; while it isn't found, retries at most every RETRY_SEC."""
        if self.path is not None:
            return True
        now = time.time()
        if now < self._next_try:
            return False
        self._next_try = now + self.RETRY_SEC
        for cand in _candidates(self.configured_path):
            try:
                if not cand.is_file():
                    continue
                with open(cand, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                self.cars = _id_name_table(_ci(data, "cars") or {})
                self.classes = _id_name_table(_ci(data, "classes") or {})
                self.path = cand
                logger.info(f"RaceRoom car data: {len(self.cars)} cars, {len(self.classes)} classes ({cand})")
                return True
            except Exception as e:
                logger.debug(f"r3e-data.json at {cand} unreadable: {e}")
        return False

    def car_name(self, model_id: int) -> str:
        if model_id is None or model_id < 0:
            return ""
        return self.cars.get(int(model_id)) or f"RaceRoom car {model_id}"

    def class_name(self, class_id: int) -> str:
        if class_id is None or class_id < 0:
            return ""
        return self.classes.get(int(class_id)) or f"Class {class_id}"

    @property
    def available(self) -> bool:
        return bool(self.cars)
