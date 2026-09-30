"""Fixed-JSON tests for the natural-language filter contract in web/api/query.py.

No API call. Live model output is not deterministic (see CLAUDE.md), so the validator is tested here
with hand-written model replies, good and hostile.
"""
import importlib.util
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("query", ROOT / "web" / "api" / "query.py")
query = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query)
validate = query.validate_filter


def test_valid_filter_passes_through():
    raw = {
        "suburb_name": "NORTH ADELAIDE",
        "species_text": "London Plane",
        "age_class": ["Mature", "Scenescent"],
        "height_min": 5,
        "height_max": 12.5,
        "completeness_max": 0.5,
        "missing_species_flag": True,
        "duplicate_location_flag": False,
        "unsupported": None,
    }
    result = validate(raw)
    assert result["unsupported"] is None
    assert result["filter"] == {
        "suburb_name": "NORTH ADELAIDE",
        "species_text": "London Plane",
        "age_class": ["Mature", "Scenescent"],
        "height_min": 5,
        "height_max": 12.5,
        "completeness_max": 0.5,
        "missing_species_flag": True,
        "duplicate_location_flag": False,
    }


def test_unknown_keys_are_dropped():
    raw = {"suburb_name": "ADELAIDE", "drop_table": "trees", "__proto__": {"x": 1}, "geometry": "POINT(0 0)"}
    assert validate(raw)["filter"] == {"suburb_name": "ADELAIDE"}


def test_null_values_are_ignored():
    raw = {k: None for k in query.FILTER_SCHEMA["properties"]}
    result = validate(raw)
    assert result["filter"] == {}
    assert result["unsupported"] == query.FALLBACK_MESSAGE


def test_suburb_must_be_allowed_and_is_normalised():
    assert validate({"suburb_name": " north adelaide "})["filter"] == {"suburb_name": "NORTH ADELAIDE"}
    assert validate({"suburb_name": "Unley"})["filter"] == {}
    assert validate({"suburb_name": ["ADELAIDE"]})["filter"] == {}


def test_list_values_are_filtered_and_deduplicated():
    raw = {"vigour": ["Low vigour", "Dying tree", "Low vigour", "Super vigour", 7, {"a": 1}, None]}
    assert validate(raw)["filter"] == {"vigour": ["Low vigour", "Dying tree"]}
    assert validate({"vigour": ["nonsense"]})["filter"] == {}
    assert validate({"vigour": "Low vigour"})["filter"] == {}  # a bare string is not a list


def test_numbers_are_range_and_type_checked():
    assert validate({"height_min": -1})["filter"] == {}
    assert validate({"height_max": 51})["filter"] == {}
    assert validate({"completeness_max": 1.5})["filter"] == {}
    assert validate({"height_min": "5"})["filter"] == {}
    assert validate({"height_min": True})["filter"] == {}  # bool is not a number here
    for bad in (math.nan, math.inf, -math.inf):
        assert validate({"height_max": bad})["filter"] == {}


def test_contradictory_height_range_is_dropped():
    assert validate({"height_min": 12, "height_max": 5})["filter"] == {}
    assert validate({"height_min": 5, "height_max": 5})["filter"] == {"height_min": 5, "height_max": 5}


def test_flags_must_be_real_booleans():
    raw = {"missing_species_flag": "true", "duplicate_location_flag": 1, "age_size_anomaly_flag": "yes"}
    assert validate(raw)["filter"] == {}
    assert validate({"missing_inspection_flag": False})["filter"] == {"missing_inspection_flag": False}


@pytest.mark.parametrize(
    "text",
    ["<script>alert(1)</script>", "x" * 61, "a\nb", "'; DROP TABLE trees;--", "", "   ", "..\\..\\etc", "café$"],
)
def test_species_text_rejects_hostile_or_oversize_input(text):
    assert "species_text" not in validate({"species_text": text})["filter"]


@pytest.mark.parametrize("text", ["London Plane", "  Jacaranda  ", "Fraxinus Angustifolia", "Callery-Pear", "O'Brien"])
def test_species_text_accepts_plain_names(text):
    assert validate({"species_text": text})["filter"] == {"species_text": text.strip()}


@pytest.mark.parametrize("raw", [None, [], "trees in Adelaide", 42, [{"suburb_name": "ADELAIDE"}]])
def test_non_object_reply_gives_empty_filter_and_message(raw):
    result = validate(raw)
    assert result == {"filter": {}, "unsupported": query.FALLBACK_MESSAGE}


def test_unsupported_message_is_cleaned_and_capped():
    result = validate({"unsupported": "Cannot\x00 predict\n\tdeath.  " + "z" * 500})
    assert "\x00" not in result["unsupported"] and "\n" not in result["unsupported"]
    assert len(result["unsupported"]) <= query.MAX_MESSAGE
    assert result["unsupported"].startswith("Cannot predict death.")


def test_unsupported_can_sit_beside_a_partial_filter():
    result = validate({"suburb_name": "ADELAIDE", "unsupported": "Cannot filter by owner."})
    assert result == {"filter": {"suburb_name": "ADELAIDE"}, "unsupported": "Cannot filter by owner."}


def test_blank_unsupported_message_is_treated_as_missing():
    assert validate({"suburb_name": "ADELAIDE", "unsupported": " \n "})["unsupported"] is None


# Contract checks against the real data, so the allowlists cannot drift from trees.geojson.
@pytest.fixture(scope="module")
def properties():
    feats = json.loads((ROOT / "web" / "data" / "trees.geojson").read_text())["features"]
    return [f["properties"] for f in feats]


def test_allowed_values_equal_the_values_in_the_data(properties):
    assert set(query.SUBURBS) == {p["suburb_name"] for p in properties}
    for key, allowed in query.LIST_VALUES.items():
        assert set(allowed) == {p[key] for p in properties if p[key] is not None}, key


def test_flag_keys_are_real_properties(properties):
    flags_in_data = {k for k in properties[0] if k.endswith("_flag")}
    assert set(query.FLAG_KEYS) == flags_in_data


def test_data_ranges_fit_the_numeric_bounds(properties):
    heights = [p["height"] for p in properties if p["height"] is not None]
    assert 0 <= min(heights) and max(heights) <= query.NUMBER_KEYS["height_max"][1]
    assert all(0 <= p["data_completeness_score"] <= 1 for p in properties)


def test_schema_keys_match_the_validator():
    schema = query.FILTER_SCHEMA
    expected = {"suburb_name", "species_text", "unsupported", *query.LIST_VALUES, *query.NUMBER_KEYS, *query.FLAG_KEYS}
    assert set(schema["properties"]) == expected
    assert set(schema["required"]) == expected
    assert schema["additionalProperties"] is False
