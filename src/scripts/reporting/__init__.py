"""
Core reporting logic — pure transformations on acoustic event DataFrames.

Separated from generate_report.py so these functions can be unit-tested
without the heavy analysis deps (matplotlib, seaborn, weasyprint, tabulate).
"""

import pandas as pd

# ---------------------------------------------------------------------------
# Category mapping
# ---------------------------------------------------------------------------

DEFAULT_YAMNET_MAP: dict[str, str] = {
    "Dog": "Animal",
    "Bark": "Animal",
    "Howl": "Animal",
    "Yip": "Animal",
    "Cat": "Animal",
    "Meow": "Animal",
    "Bird": "Animal",
    "Shouting": "Vocals",
    "Scream": "Vocals",
    "Yelling": "Vocals",
    "Speech": "Vocals",
    "Child": "Vocals",
    "Laughter": "Vocals",
    "Music": "Music",
    "Beat": "Music",
    "Drum": "Music",
    "Bass": "Music",
    "Knock": "Impact",
    "Door": "Impact",
    "Slam": "Impact",
    "Glass": "Impact",
    "Gunshot": "Impact",
    "Explosion": "Impact",
    "Hammer": "Construction",
    "Drill": "Construction",
    "Saw": "Construction",
    "Engine": "Traffic",
    "Car": "Traffic",
    "Siren": "Traffic",
}


class CategoryManager:
    """Maps raw YAMNet labels to broad report categories."""

    def __init__(self, extra_mapping: dict[str, str] | None = None):
        self.mapping = DEFAULT_YAMNET_MAP.copy()
        if extra_mapping:
            self.mapping.update(extra_mapping)

    def get_category(self, label: str) -> str:
        if label in self.mapping:
            return self.mapping[label]
        for key, cat in self.mapping.items():
            if key in label:
                return cat
        return "Other Noise"


# ---------------------------------------------------------------------------
# Data processing
# ---------------------------------------------------------------------------


def process_data(df: pd.DataFrame, reporting_config) -> pd.DataFrame:
    """
    Clean raw CSV data and enrich with category, limit, and violation columns.

    Expects columns: timestamp, label, dbspl, confidence, cpu, ram, temp, disk, disk_attached
    Returns the same DataFrame with added columns: Category, is_night, limit, violation
    """
    if df.empty:
        return df

    cat_manager = CategoryManager(reporting_config.category_mapping)

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()

    for col in ("dbspl", "confidence", "cpu", "ram", "temp", "disk", "disk_attached"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["Category"] = df["label"].apply(cat_manager.get_category)

    limit_day = reporting_config.limits.day_db
    limit_night = reporting_config.limits.night_db

    df["is_night"] = (df.index.hour >= 22) | (df.index.hour < 7)
    df["limit"] = df["is_night"].apply(lambda x: limit_night if x else limit_day)
    df["violation"] = df["dbspl"] > df["limit"]

    return df
