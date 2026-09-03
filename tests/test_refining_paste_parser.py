"""Inventory-paste parser tests (GitHub issue #92). No network - this module
does its own SDE-independent tab-column parsing, confirmed against
evepraisal.com's `evepaste` reference format (9 tab-separated columns, no
header row): Name, Quantity, Group, Category, Size, Slot, Volume, Meta Level,
Tech Level.
"""
from __future__ import annotations

from eve_trader_local.refining.paste_parser import merge_duplicate_stacks, parse_paste

# A realistic pasted "Inventory" list-view copy: some rows have every column,
# some have several trailing columns empty (a real client's own copy still
# emits the tab characters for those, so a naive fields[N] index would raise
# rather than treat them as blank).
SAMPLE_PASTE = (
    "Veldspar\t1250\tVeldspar\tAsteroid\t\t\t0.1 m3\t\t\n"
    "Tritanium\t500\tMineral\tMaterial\t\t\t0.01 m3\t\t\n"
    "Damage Control II\t2\tDamage Controls\tModule\tMedium\tLow\t5 m3\t2\t2\n"
)


def test_parse_paste_reads_all_columns():
    lines = parse_paste(SAMPLE_PASTE)
    assert [l.name for l in lines] == ["Veldspar", "Tritanium", "Damage Control II"]
    assert lines[0].quantity == 1250
    assert lines[0].volume_m3 == 0.1
    assert lines[0].category == "Asteroid"
    assert lines[2].quantity == 2
    assert lines[2].category == "Module"
    assert all(l.error is None for l in lines)


def test_parse_paste_skips_blank_lines():
    text = "Veldspar\t1250\tVeldspar\tAsteroid\t\t\t0.1 m3\t\t\n\n\n"
    lines = parse_paste(text)
    assert len(lines) == 1


def test_parse_paste_pads_short_trailing_columns():
    """A short split() result (client omitted trailing empty tabs) is padded
    with empty strings rather than raising an IndexError."""
    lines = parse_paste("Veldspar\t1250\n")
    assert lines[0].name == "Veldspar"
    assert lines[0].quantity == 1250
    assert lines[0].error is None


def test_parse_paste_flags_non_tab_separated_line():
    """A single bad line is recorded with its own error, not dropped and not
    aborting the whole paste (#92's "flag rather than silently drop")."""
    lines = parse_paste("just some free text, not a paste\n")
    assert lines[0].error is not None
    assert "tab-separated" in lines[0].error


def test_parse_paste_flags_unreadable_name_or_quantity():
    lines = parse_paste("\t\tGroup\tCategory\t\t\t\t\t\n")
    assert lines[0].error is not None


def test_parse_paste_quantity_strips_thousands_separators():
    lines = parse_paste("Tritanium\t1,234,567\tMineral\tMaterial\t\t\t\t\t\n")
    assert lines[0].quantity == 1234567


def test_parse_paste_volume_strips_m3_suffix_and_commas():
    lines = parse_paste("Freighter\t1\tFreighter\tShip\t\t\t1,000,000.0 m3\t\t\n")
    assert lines[0].volume_m3 == 1000000.0


def test_merge_duplicate_stacks_sums_case_insensitively():
    """Multiple stacks of the same item in different cargo slots sum together,
    case-insensitively - EVE names are unique regardless of case."""
    text = "veldspar\t100\tVeldspar\tAsteroid\t\t\t0.1 m3\t\t\nVeldspar\t50\tVeldspar\tAsteroid\t\t\t0.1 m3\t\t\n"
    lines = parse_paste(text)
    merged = merge_duplicate_stacks(lines)
    assert len(merged) == 1
    assert merged[0].quantity == 150
    assert merged[0].name == "veldspar"  # first-seen casing wins


def test_merge_duplicate_stacks_drops_errored_lines():
    lines = parse_paste(SAMPLE_PASTE + "bad line with no tabs\n")
    merged = merge_duplicate_stacks(lines)
    assert all(l.error is None for l in merged)
    assert len(merged) == 3


def test_merge_duplicate_stacks_preserves_first_seen_order():
    text = "B Item\t1\tG\tC\t\t\t\t\t\nA Item\t1\tG\tC\t\t\t\t\t\n"
    merged = merge_duplicate_stacks(parse_paste(text))
    assert [l.name for l in merged] == ["B Item", "A Item"]
