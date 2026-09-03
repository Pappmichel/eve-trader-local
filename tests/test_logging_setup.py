from __future__ import annotations

import logging

from eve_trader_local import logging_setup


def test_configure_logging_writes_to_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_TRADER_LOCAL_DATA_DIR", str(tmp_path / "data"))
    logging_setup.reset_for_tests()

    logging_setup.configure_logging()
    logging.getLogger("eve_trader_local.somemodule").warning("test message")

    log_path = tmp_path / "data" / "eve-trader-local.log"
    assert log_path.exists()
    assert "test message" in log_path.read_text()

    logging_setup.reset_for_tests()


def test_configure_logging_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_TRADER_LOCAL_DATA_DIR", str(tmp_path / "data"))
    logging_setup.reset_for_tests()

    logging_setup.configure_logging()
    logging_setup.configure_logging()

    logger = logging.getLogger("eve_trader_local")
    assert len(logger.handlers) == 1

    logging_setup.reset_for_tests()
