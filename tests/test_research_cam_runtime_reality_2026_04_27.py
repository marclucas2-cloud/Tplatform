from __future__ import annotations

import pandas as pd

from scripts.research.cam_runtime_reality_2026_04_27 import first_runtime_exit_idx


def test_first_runtime_exit_idx_maps_friday_to_monday() -> None:
    index = pd.to_datetime(
        [
            "2026-04-20",
            "2026-04-21",
            "2026-04-22",
            "2026-04-23",
            "2026-04-24",
            "2026-04-27",
            "2026-04-28",
        ]
    )
    assert first_runtime_exit_idx(index, 4) == 5


def test_first_runtime_exit_idx_maps_monday_to_wednesday() -> None:
    index = pd.to_datetime(
        [
            "2026-04-20",
            "2026-04-21",
            "2026-04-22",
            "2026-04-23",
            "2026-04-24",
        ]
    )
    assert first_runtime_exit_idx(index, 0) == 2
