"""The SDK's retry lines are INFO; without a handler they never reached the API's logs."""

import logging

from minsky_api.main import _PROVIDER_RETRY_HANDLER, create_app


def test_the_app_shows_the_provider_retry_lines_once():
    create_app()
    create_app()
    provider = logging.getLogger("openai")
    assert provider.isEnabledFor(logging.INFO)
    assert provider.handlers.count(_PROVIDER_RETRY_HANDLER) == 1
    record = provider.makeRecord("openai._base_client", logging.INFO, __file__, 0, "Retrying request", (), None)
    assert _PROVIDER_RETRY_HANDLER.filter(record) and record.levelno >= _PROVIDER_RETRY_HANDLER.level


def test_debug_lines_stay_hidden():
    create_app()
    assert not logging.getLogger("openai").isEnabledFor(logging.DEBUG)
