"""Sanity tests for doctrine/models.py's plain dataclasses - mostly guarding
against a field/default regressing silently on a future edit, matching the
level of test other pure-dataclass ports in this repo get (e.g.
production/models.py has no dedicated test file of its own; these are worth
having here specifically because ParsedFitting/ParsedItem/ParsedIssue are
parser.py's actual return shape, exercised implicitly by
test_doctrine_parser.py, but not otherwise asserted on directly)."""
from __future__ import annotations

from eve_trader_local.doctrine.models import Doctrine, Fitting, FittingItem, ParsedFitting, ParsedIssue, ParsedItem, ParseIssue


def test_parsed_item_defaults_to_online():
    item = ParsedItem(line_no=1, slot_section="low", type_id=2, quantity=1.0)
    assert item.is_offline is False


def test_parsed_fitting_defaults_to_empty_items_and_issues():
    fitting = ParsedFitting(hull_type_id=1, hull_name="Rifter", fit_name="Fit")
    assert fitting.items == []
    assert fitting.issues == []


def test_parsed_fitting_holds_its_own_items_and_issues_lists():
    # Two separate ParsedFitting instances must not share a mutable default
    # list (the classic dataclass footgun) - field(default_factory=list)
    # guards against exactly this.
    a = ParsedFitting(hull_type_id=1, hull_name="Rifter", fit_name="A")
    b = ParsedFitting(hull_type_id=1, hull_name="Rifter", fit_name="B")
    a.items.append(ParsedItem(line_no=1, slot_section="low", type_id=2, quantity=1.0))
    assert b.items == []


def test_parsed_issue_fields():
    issue = ParsedIssue(line_no=3, raw_line="Bogus Module", issue_kind="unresolved_name", message="Unknown item.")
    assert issue.line_no == 3
    assert issue.issue_kind == "unresolved_name"


def test_doctrine_defaults():
    d = Doctrine(doctrine_id="d1", name="HAC Fleet")
    assert d.active is True
    assert d.description is None


def test_fitting_defaults_including_bay_text_fields():
    f = Fitting(fitting_id="f1", doctrine_id="d1", name="Standard", hull_type_id=1, raw_eft="[Rifter, Standard]\n")
    assert f.active is True
    assert f.contract_target == 0
    assert f.stockpile_target == 0
    assert f.fuel_bay_text is None
    assert f.ship_maintenance_bay_text is None


def test_fitting_item_and_parse_issue_are_persistable_row_shapes():
    row = FittingItem(fitting_id="f1", line_no=1, slot_section="low", type_id=2, quantity=1.0)
    assert row.is_offline is False
    issue_row = ParseIssue(fitting_id="f1", line_no=1, raw_line="x", issue_kind="malformed", message="bad")
    assert issue_row.fitting_id == "f1"
