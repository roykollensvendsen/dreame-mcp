"""Turning the vacuum's map into the files robot software reads: PGM, YAML, PNG and the raw blob."""

from __future__ import annotations

import base64

from dreame_mcp.map_export import (
    map_response_to_png_bytes,
    map_response_to_raw_bytes,
    occupancy_to_pgm,
    occupancy_to_yaml,
)

PNG = b"\x89PNG\r\n\x1a\nrest"


def test_free_occupied_and_unknown_cells_become_white_black_and_grey():
    pgm = occupancy_to_pgm([0, 100, -1, 50, -7, 250], 3, 2)
    header, pixels = pgm[:11], pgm[11:]
    assert header == b"P5\n3 2\n255\n"
    assert list(pixels) == [254, 0, 205, 127, 205, 0]


def test_the_yaml_names_the_image_and_where_it_lies():
    yaml = occupancy_to_yaml("map.pgm", 0.05, (-1.5, 2.0, 0.0))
    assert yaml.splitlines() == [
        "image: map.pgm",
        "resolution: 0.05",
        "origin: [-1.5, 2.0, 0.0]",
        "occupied_thresh: 0.65",
        "free_thresh: 0.196",
        "negate: 0",
        "mode: trinary",
    ]


def test_a_png_picture_is_taken_from_the_answer():
    assert map_response_to_png_bytes({"image": base64.b64encode(PNG).decode()}) == PNG


def test_no_png_without_a_picture_or_when_it_is_not_one():
    assert map_response_to_png_bytes({}) is None
    assert map_response_to_png_bytes({"image": 5}) is None
    assert map_response_to_png_bytes({"image": base64.b64encode(b"GIF89a").decode()}) is None
    assert map_response_to_png_bytes({"image": "not base64!"}) is None


def test_the_raw_blob_is_decoded():
    assert map_response_to_raw_bytes({"raw_b64": base64.b64encode(b"blob").decode()}) == b"blob"


def test_no_raw_blob_when_missing_or_broken():
    assert map_response_to_raw_bytes({}) is None
    assert map_response_to_raw_bytes({"raw_b64": ["x"]}) is None
    assert map_response_to_raw_bytes({"raw_b64": "a"}) is None
