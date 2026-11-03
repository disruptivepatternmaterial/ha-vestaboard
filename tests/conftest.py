"""Fixtures for Vestaboard tests."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading the Vestaboard custom integration in every test."""
    return
