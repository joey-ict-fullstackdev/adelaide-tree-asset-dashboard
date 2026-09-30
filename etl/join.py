"""Join cleaned trees to suburbs, write data/processed/trees.geojson."""
import shutil
from pathlib import Path

import geopandas as gpd

from clean import clean_trees
from load import load_suburbs

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed" / "trees.geojson"
WEB_COPY = ROOT / "web" / "data" / "trees.geojson"  # frontend fetches this; committed for Vercel


def join_suburbs():
    trees = clean_trees()
    suburbs = load_suburbs()
    assert trees.crs == suburbs.crs, (trees.crs, suburbs.crs)  # CRS must match before any join
    joined = gpd.sjoin(
        trees, suburbs[["suburb_name", "geometry"]], predicate="within", how="left"
    ).drop(columns="index_right")
    assert len(joined) == len(trees), "a tree sits in two suburb polygons"
    unmatched = joined["suburb_name"].isna().sum()
    assert unmatched == 0, f"{unmatched} trees match no suburb: decide drop or flag"
    return joined


if __name__ == "__main__":
    trees = join_suburbs()
    trees.to_file(OUT, driver="GeoJSON", COORDINATE_PRECISION=6)
    print(len(trees), "trees written to", OUT)
    WEB_COPY.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(OUT, WEB_COPY)
    print("copied to", WEB_COPY)
    print(trees["suburb_name"].value_counts())
