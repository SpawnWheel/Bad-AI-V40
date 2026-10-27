"""Dumps RaceRoom's raw shared memory, for working out the layout after a game update.

Writes r3e_dump_<time>.bin (the whole mapping) next to this script and prints each driver's
first bytes as int / float, so fields can be matched against what is on screen.
"""
import ctypes
import datetime
import os
import struct
import sys

from r3e_shared import R3ESharedMemory

def main():
    shm = R3ESharedMemory()
    if not shm.open():
        print("RaceRoom shared memory not found - is the game running?")
        return 1
    major, minor, offset, size = struct.unpack_from("<4i", ctypes.string_at(shm._view, 16))
    raw = ctypes.string_at(shm._view, offset + 128 * size)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f"r3e_dump_{datetime.datetime.now().strftime('%H%M%S')}.bin")
    with open(path, "wb") as f:
        f.write(raw)
    print(f"version {major}.{minor}  drivers at {offset}  driver size {size}  -> {path}")
    for i in range(int(sys.argv[1]) if len(sys.argv) > 1 else 3):
        base = offset + i * size
        name = raw[base:base + 64].split(b"\0", 1)[0].decode("utf-8", "ignore")
        print(f"\n--- driver {i}: {name}")
        for off in range(64, size, 4):
            iv, = struct.unpack_from("<i", raw, base + off)
            fv, = struct.unpack_from("<f", raw, base + off)
            print(f"{off:4d}  int {iv:>11d}  float {fv:>14.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
