"""Row-by-row check of data/processed/trees.geojson against data/raw/.

Expected values are derived from the raw files directly, never from etl/ code.
Run `python etl/join.py` first, then `python -m pytest tests -q`.
"""
import csv
import json
import re
from collections import Counter
from pathlib import Path

import pytest
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import Point, shape
from shapely.wkt import loads as wkt_loads

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed" / "trees.geojson"

TO_4326 = Transformer.from_crs(3857, 4326, always_xy=True)
MGA_TO_4326 = Transformer.from_crs(7854, 4326, always_xy=True)
INSPECTION = ["Circum (Inspection)", "Vigour (Inspection)", "Structure (Inspection)",
              "Age (Inspection)", "Height (Inspection)"]
KEY = INSPECTION + ["Species Name (Inspection)"]
EXPECTED_KEYS = {
    "road", "asset_id", "circumference", "vigour", "structure", "age_class", "height",
    "species", "common_name", "duplicate_location_flag", "missing_species_flag",
    "species_genus_only_flag", "missing_inspection_flag", "data_completeness_score",
    "age_size_anomaly_flag", "suburb_name",
}


def blank(v):
    return None if v == "" else v


@pytest.fixture(scope="module")
def raw_rows():
    with open(RAW / "street_trees.csv", newline="") as f:
        rows = [r for r in csv.DictReader(f) if any(v != "" for v in r.values())]
    return {int(float(r["Asset ID (Identifier)"])): r for r in rows}


@pytest.fixture(scope="module")
def out_features():
    feats = json.loads(OUT.read_text())["features"]
    by_id = {f["properties"]["asset_id"]: f for f in feats}
    assert len(by_id) == len(feats), "duplicate asset_id in output"
    return by_id


def test_same_rows(raw_rows, out_features):
    assert set(out_features) == set(raw_rows)
    assert len(out_features) == 6030


def test_property_keys(out_features):
    for f in out_features.values():
        assert set(f["properties"]) == EXPECTED_KEYS


def test_attributes(raw_rows, out_features):
    cols = {"road": "Roadname and Road ID", "circumference": "Circum (Inspection)",
            "vigour": "Vigour (Inspection)", "structure": "Structure (Inspection)",
            "age_class": "Age (Inspection)", "species": "Species Name (Inspection)",
            "common_name": "Common Name (Inspection)"}
    bad = []
    for aid, r in raw_rows.items():
        p = out_features[aid]["properties"]
        for key, col in cols.items():
            if p[key] != blank(r[col]):
                bad.append((aid, key, p[key], r[col]))
        h = blank(r["Height (Inspection)"])
        if p["height"] != (None if h is None else float(h)):
            bad.append((aid, "height", p["height"], h))
    assert not bad, bad[:5]


def test_coordinates(raw_rows, out_features):
    bad = []
    for aid, r in raw_rows.items():
        x, y = wkt_loads(r["Geometry"]).coords[0]
        lon, lat = TO_4326.transform(x, y)
        ox, oy = out_features[aid]["geometry"]["coordinates"]
        if abs(ox - lon) > 1e-6 or abs(oy - lat) > 1e-6:
            bad.append((aid, (ox, oy), (lon, lat)))
        if r["Easting (Location/Map Coordinates)"]:  # second, independent source
            elon, elat = MGA_TO_4326.transform(
                float(r["Easting (Location/Map Coordinates)"]),
                float(r["Northing (Location/Map Coordinates)"]))
            if abs(ox - elon) > 1e-6 or abs(oy - elat) > 1e-6:
                bad.append((aid, "E/N disagrees", (ox, oy), (elon, elat)))
    assert not bad, bad[:5]


def test_flags_and_score(raw_rows, out_features):
    geom_count = Counter(r["Geometry"] for r in raw_rows.values())
    bad = []
    for aid, r in raw_rows.items():
        p = out_features[aid]["properties"]
        species = blank(r["Species Name (Inspection)"])
        age, circ = r["Age (Inspection)"], r["Circum (Inspection)"]
        expected = {
            "duplicate_location_flag": geom_count[r["Geometry"]] > 1,
            "missing_species_flag": species is None,
            "species_genus_only_flag": bool(species and re.search(r"\bSp\.$", species)),
            "missing_inspection_flag": all(r[c] == "" for c in INSPECTION),
            "data_completeness_score": round(sum(r[c] != "" for c in KEY) / 6, 2),
            "age_size_anomaly_flag": (age == "Juvenile" and circ in ("1m - 2m", "2m - 3m", ">3m"))
            or (age == "Semi mature" and circ in ("2m - 3m", ">3m")),
        }
        for k, v in expected.items():
            if p[k] != v:
                bad.append((aid, k, p[k], v))
    assert not bad, bad[:5]


def test_suburb_by_point_in_polygon(raw_rows, out_features):
    # independent of geopandas.sjoin: raw suburb JSON + shapely STRtree
    subs = json.loads((RAW / "Suburbs_GDA2020.geojson").read_text())["features"]
    geoms = [shape(s["geometry"]) for s in subs]
    names = [s["properties"]["suburb"] for s in subs]
    tree = STRtree(geoms)
    bad = []
    for aid, r in raw_rows.items():
        x, y = wkt_loads(r["Geometry"]).coords[0]
        pt = Point(*TO_4326.transform(x, y))
        hits = [names[i] for i in tree.query(pt, predicate="within")]
        if hits != [out_features[aid]["properties"]["suburb_name"]]:
            bad.append((aid, hits, out_features[aid]["properties"]["suburb_name"]))
    assert not bad, bad[:5]


def test_web_copy_identical():
    web = ROOT / "web" / "data" / "trees.geojson"
    assert web.read_bytes() == OUT.read_bytes(), "run `python etl/join.py` to refresh the web copy"


def test_size_within_rule():
    assert OUT.stat().st_size < 5_000_000  # CLAUDE.md: a few MB at most
