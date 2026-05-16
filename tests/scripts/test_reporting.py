"""Tests for scripts.reporting — CategoryManager and process_data."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from scripts.reporting import CategoryManager, process_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(day_db=55.0, night_db=50.0, category_mapping=None):
    limits = SimpleNamespace(day_db=day_db, night_db=night_db)
    return SimpleNamespace(limits=limits, category_mapping=category_mapping or {})


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal DataFrame with the columns written by SmartBufferSink."""
    defaults = {
        "label": "Silence",
        "confidence": "0.50",
        "rms": "0.0010",
        "dbspl": "0.0",
        "flux": "0.0",
        "cpu": "10.0",
        "ram": "30.0",
        "temp": "45.0",
        "disk": "20.0",
        "disk_attached": "5.0",
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _ts(hour: int) -> str:
    """Return a timestamp string for a given hour on a fixed date."""
    return f"2026-05-15 {hour:02d}:00:00.000"


# ---------------------------------------------------------------------------
# CategoryManager
# ---------------------------------------------------------------------------

class TestCategoryManager:
    def test_exact_match(self):
        cm = CategoryManager()
        assert cm.get_category("Dog") == "Animal"

    def test_substring_match(self):
        cm = CategoryManager()
        # "Dog barking" contains the key "Dog" (case-sensitive)
        assert cm.get_category("Dog barking") == "Animal"

    def test_unmapped_label_returns_other(self):
        cm = CategoryManager()
        assert cm.get_category("Thunderstorm") == "Other Noise"

    def test_custom_mapping_overrides_default(self):
        cm = CategoryManager({"Thunderstorm": "Weather"})
        assert cm.get_category("Thunderstorm") == "Weather"

    def test_custom_mapping_does_not_remove_defaults(self):
        cm = CategoryManager({"Thunderstorm": "Weather"})
        assert cm.get_category("Dog") == "Animal"

    def test_known_categories_spot_check(self):
        cm = CategoryManager()
        assert cm.get_category("Gunshot") == "Impact"
        assert cm.get_category("Drill") == "Construction"
        assert cm.get_category("Siren") == "Traffic"
        assert cm.get_category("Speech") == "Vocals"
        assert cm.get_category("Music") == "Music"


# ---------------------------------------------------------------------------
# process_data
# ---------------------------------------------------------------------------

class TestProcessData:
    def test_empty_dataframe_returned_unchanged(self):
        result = process_data(pd.DataFrame(), _make_config())
        assert result.empty

    def test_timestamp_becomes_index(self):
        df = _make_df([{"timestamp": _ts(10), "label": "Dog", "dbspl": "65.0"}])
        result = process_data(df, _make_config())
        assert result.index.name == "timestamp"
        assert pd.api.types.is_datetime64_any_dtype(result.index)

    def test_sorted_by_timestamp(self):
        df = _make_df([
            {"timestamp": _ts(12), "label": "Dog", "dbspl": "60.0"},
            {"timestamp": _ts(8), "label": "Cat", "dbspl": "55.0"},
        ])
        result = process_data(df, _make_config())
        assert result.index[0].hour == 8
        assert result.index[1].hour == 12

    def test_dbspl_coerced_to_numeric(self):
        df = _make_df([{"timestamp": _ts(10), "label": "Dog", "dbspl": "65.3"}])
        result = process_data(df, _make_config())
        assert result["dbspl"].dtype.kind == "f"
        assert result["dbspl"].iloc[0] == pytest.approx(65.3)

    def test_category_column_added(self):
        df = _make_df([{"timestamp": _ts(10), "label": "Dog", "dbspl": "65.0"}])
        result = process_data(df, _make_config())
        assert result["Category"].iloc[0] == "Animal"

    def test_unknown_label_maps_to_other_noise(self):
        df = _make_df([{"timestamp": _ts(10), "label": "Thunderstorm", "dbspl": "50.0"}])
        result = process_data(df, _make_config())
        assert result["Category"].iloc[0] == "Other Noise"

    def test_daytime_hour_is_not_night(self):
        df = _make_df([{"timestamp": _ts(14), "label": "Dog", "dbspl": "50.0"}])
        result = process_data(df, _make_config())
        assert not result["is_night"].iloc[0]

    def test_hour_22_is_night(self):
        df = _make_df([{"timestamp": _ts(22), "label": "Dog", "dbspl": "50.0"}])
        result = process_data(df, _make_config())
        assert result["is_night"].iloc[0]

    def test_hour_3_is_night(self):
        df = _make_df([{"timestamp": _ts(3), "label": "Dog", "dbspl": "50.0"}])
        result = process_data(df, _make_config())
        assert result["is_night"].iloc[0]

    def test_daytime_limit_applied(self):
        df = _make_df([{"timestamp": _ts(14), "label": "Dog", "dbspl": "50.0"}])
        result = process_data(df, _make_config(day_db=55.0, night_db=45.0))
        assert result["limit"].iloc[0] == pytest.approx(55.0)

    def test_nighttime_limit_applied(self):
        df = _make_df([{"timestamp": _ts(23), "label": "Dog", "dbspl": "50.0"}])
        result = process_data(df, _make_config(day_db=55.0, night_db=45.0))
        assert result["limit"].iloc[0] == pytest.approx(45.0)

    def test_violation_flagged_when_dbspl_exceeds_limit(self):
        df = _make_df([{"timestamp": _ts(14), "label": "Dog", "dbspl": "80.0"}])
        result = process_data(df, _make_config(day_db=55.0))
        assert result["violation"].iloc[0]

    def test_no_violation_when_dbspl_within_limit(self):
        df = _make_df([{"timestamp": _ts(14), "label": "Dog", "dbspl": "40.0"}])
        result = process_data(df, _make_config(day_db=55.0))
        assert not result["violation"].iloc[0]

    def test_multiple_rows_processed_correctly(self):
        df = _make_df([
            {"timestamp": _ts(10), "label": "Dog", "dbspl": "80.0"},   # violation (day)
            {"timestamp": _ts(23), "label": "Siren", "dbspl": "30.0"}, # no violation (night)
            {"timestamp": _ts(15), "label": "Unknown", "dbspl": "40.0"},
        ])
        result = process_data(df, _make_config(day_db=55.0, night_db=45.0))
        assert len(result) == 3
        assert result.loc[result["Category"] == "Animal", "violation"].iloc[0]
        assert not result.loc[result["Category"] == "Traffic", "violation"].iloc[0]

    def test_input_dataframe_not_mutated(self):
        df = _make_df([{"timestamp": _ts(10), "label": "Dog", "dbspl": "65.0"}])
        original_cols = set(df.columns)
        process_data(df, _make_config())
        assert set(df.columns) == original_cols
