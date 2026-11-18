"""The fork is GPL-3.0-or-later, and the upstream MIT notice travels with it.

MIT lets the code be relicensed, but only if its copyright and permission
notice stay with every copy. These tests keep both halves true together.
"""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_licence_file_is_the_gpl_version_3():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert text.lstrip().startswith("GNU GENERAL PUBLIC LICENSE")
    assert "Version 3, 29 June 2007" in text


def test_the_package_says_gpl_3_or_later_and_ships_both_licence_files():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["license"] == "GPL-3.0-or-later"
    assert project["license-files"] == ["LICENSE", "LICENSE-MIT"]


def test_the_upstream_mit_notice_is_kept_in_full():
    text = (ROOT / "LICENSE-MIT").read_text(encoding="utf-8")

    assert "Copyright (c) 2026 Sandra Schipal" in text
    assert "The above copyright notice and this permission notice shall be included" in text
    assert "used under the MIT License" in text


def test_the_server_listing_names_the_same_licence():
    listing = json.loads((ROOT / "glama.json").read_text(encoding="utf-8"))

    assert listing["license"] == "GPL-3.0-or-later"
