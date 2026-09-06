"""Production's Invention Estimator tab: `do_estimate_invention`, a
standalone form producing the recipe/decryptor comparison table
(`invention.compare_recipes_and_decryptors`/`best_recipe_for_decryptor`) -
kept separate from `production_item_lookup.py` because it resolves a T2/T3
*product* name to its invention recipe (`storage.
find_invention_recipe_by_product_name`), not the general type_id-or-name
lookup the other three item-report tools share, and produces one comparison
table rather than a single-item report."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton

from ...production import actions as production_actions
from ...production.constants import DECRYPTORS
from .. import icons
from .base import TableView
from .production_common import fmt_isk, fmt_pct

_COLUMNS = ["T1 Blueprint / Relic", "Decryptor", "Probability", "Output Runs", "ME", "TE",
           "Datacore Cost", "Decryptor Cost", "Relic Cost", "Total Attempt Cost",
           "Expected Cost/Run", "Material Savings/Run", "Net Cost/Run"]
_COLUMN_WIDTHS = [220, 140, None, None, None, None, None, None, None, None, None, None, None]
# compare_recipes_and_decryptors/best_recipe_for_decryptor's own docstrings:
# "cheapest net cost per run first".
_DEFAULT_SORT = (12, Qt.SortOrder.AscendingOrder)

_NO_DECRYPTOR = "(compare all decryptors)"


def _row_to_cells(r) -> list:
    return [r.t1_blueprint_name, r.decryptor or "None", fmt_pct(r.probability), r.output_runs, r.me, r.te,
            fmt_isk(r.datacore_cost), fmt_isk(r.decryptor_cost), fmt_isk(r.relic_cost),
            fmt_isk(r.total_attempt_cost), fmt_isk(r.expected_cost_per_run),
            fmt_isk(r.material_savings_per_run), fmt_isk(r.net_cost_per_run)]


class InventionEstimatorView(TableView):
    title = "Production - Invention Estimator"

    def __init__(self, parent=None):
        super().__init__(parent)

        form = QHBoxLayout()
        form.addWidget(QLabel("Product (T2/T3 blueprint or item):"))
        self.product_input = QLineEdit()
        self.product_input.setPlaceholderText("e.g. Small Shield Booster II")
        form.addWidget(self.product_input)
        form.addWidget(QLabel("Decryptor:"))
        self.decryptor_combo = QComboBox()
        self.decryptor_combo.addItem(_NO_DECRYPTOR, None)
        for name in DECRYPTORS:
            self.decryptor_combo.addItem(name, name)
        form.addWidget(self.decryptor_combo)
        estimate_btn = QPushButton(icons.icon("calculate"), "Estimate")
        estimate_btn.clicked.connect(self._estimate)
        form.addWidget(estimate_btn)
        form.addStretch(1)
        self.root_layout.addLayout(form)

        self._build_table(_COLUMNS, column_widths=_COLUMN_WIDTHS, default_sort=_DEFAULT_SORT)
        self._finish_status_row()
        self.show_info("Enter a T2/T3 product name and click Estimate.")

    def _estimate(self) -> None:
        product = self.product_input.text().strip()
        if not product:
            self.show_error("Enter a product name first.")
            return
        decryptor_name = self.decryptor_combo.currentData()
        self.run_action(
            functools.partial(production_actions.do_estimate_invention, product, decryptor_name=decryptor_name),
            self._on_result, busy_message=f"Estimating invention for {product}...")

    def _on_result(self, result: dict) -> None:
        rows = result["results"]
        self.populate_table([_row_to_cells(r) for r in rows])
        if rows:
            self.show_info(f"{len(rows)} combination(s), cheapest net cost/run first.")
        else:
            self.show_info("No invention recipe/decryptor combination found for that product.")
