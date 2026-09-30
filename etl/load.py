"""Load raw street trees and suburbs as GeoDataFrames in EPSG:4326."""
from pathlib import Path

import geopandas as gpd
import pandas as pd

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


def load_trees() -> gpd.GeoDataFrame:
    df = pd.read_csv(RAW / "street_trees.csv")
    empty = df.isna().all(axis=1)  # Q2: drop fully empty rows, log count
    print(f"dropped {empty.sum()} fully empty row(s)")
    df = df[~empty]
    geom = gpd.GeoSeries.from_wkt(df["Geometry"], index=df.index, crs="EPSG:3857")  # Q3
    gdf = gpd.GeoDataFrame(df.drop(columns=["Geometry"]), geometry=geom)
    return gdf.to_crs("EPSG:4326")


def load_suburbs() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(RAW / "Suburbs_GDA2020.geojson")  # Q6: GDA2020, not GDA94
    assert gdf.crs == "EPSG:4326", gdf.crs  # file has no crs key, so check
    return gdf.rename(columns={"suburb": "suburb_name"})


if __name__ == "__main__":
    for name, g in [("trees", load_trees()), ("suburbs", load_suburbs())]:
        print(name, len(g), g.crs)
        print(g.columns.tolist())
        print(g.head(3))
