"""Clean street trees, add QA flags, write data/processed/trees_clean.geojson."""
from pathlib import Path

from load import load_trees

OUT = Path(__file__).resolve().parent.parent / "data" / "processed" / "trees_clean.geojson"

RENAME = {
    "Roadname and Road ID": "road",
    "Asset ID (Identifier)": "asset_id",
    "Circum (Inspection)": "circumference",
    "Vigour (Inspection)": "vigour",
    "Structure (Inspection)": "structure",
    "Age (Inspection)": "age_class",
    "Height (Inspection)": "height",
    "Species Name (Inspection)": "species",
    "Common Name (Inspection)": "common_name",
}
INSPECTION = ["circumference", "vigour", "structure", "age_class", "height"]
KEY = INSPECTION + ["species"]  # common_name left out: null exactly when species is null


def clean_trees():
    # Q3: E/N matches Geometry to 1e-6 m on every row that has both, so drop them
    t = load_trees().drop(
        columns=["Easting (Location/Map Coordinates)", "Northing (Location/Map Coordinates)"]
    ).rename(columns=RENAME)
    t["asset_id"] = t["asset_id"].astype(int)

    minx, miny, maxx, maxy = t.total_bounds
    assert t.geometry.is_valid.all() and not t.geometry.is_empty.any()
    assert 138.5 < minx and maxx < 138.7 and -35.0 < miny and maxy < -34.85, t.total_bounds

    t["duplicate_location_flag"] = t.geometry.to_wkb().duplicated(keep=False)  # Q1
    t["missing_species_flag"] = t["species"].isna()
    t["species_genus_only_flag"] = t["species"].str.contains(r"\bSp\.$", na=False)  # Q4
    t["missing_inspection_flag"] = t[INSPECTION].isna().all(axis=1)  # Q5
    t["data_completeness_score"] = t[KEY].notna().mean(axis=1).round(2)
    juvenile_big = (t["age_class"] == "Juvenile") & t["circumference"].isin(["1m - 2m", "2m - 3m", ">3m"])
    semi_big = (t["age_class"] == "Semi mature") & t["circumference"].isin(["2m - 3m", ">3m"])
    t["age_size_anomaly_flag"] = juvenile_big | semi_big
    return t


if __name__ == "__main__":
    trees = clean_trees()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    trees.to_file(OUT, driver="GeoJSON")
    print(len(trees), "trees written to", OUT)
    print(trees.filter(like="_flag").sum())
