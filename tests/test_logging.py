"""Tracebacks in the logs must not include local variable values (visitor data)."""

from loguru import logger

from src.core.logging import configure_logging


def test_exception_logs_omit_local_variable_values(capsys):
    handler = configure_logging()
    try:
        visitor_message = "private-visitor-text-1234"
        try:
            raise ConnectionRefusedError(len(visitor_message))
        except ConnectionRefusedError:
            logger.exception("Delivery failed")
        err = capsys.readouterr().err
    finally:
        logger.remove(handler)
        configure_logging()

    assert "Delivery failed" in err
    assert "ConnectionRefusedError" in err
    assert "private-visitor-text-1234" not in err
