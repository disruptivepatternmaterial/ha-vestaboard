"""Tests for how the Vestaboard coordinator handles failed reads and writes."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientConnectionError, ClientResponseError
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vestaboard.client import InvalidApiKeyError
from custom_components.vestaboard.const import DOMAIN
from custom_components.vestaboard.coordinator import (
    AUTH_FAILURE_GRACE,
    READ_FAILURE_GRACE,
    WRITE_ATTEMPTS,
    VestaboardCoordinator,
)
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant

MESSAGE = [[0] * 22 for _ in range(6)]
POLL = timedelta(seconds=15)


def _client() -> MagicMock:
    client = MagicMock()
    client.base_url = "http://board:7000"
    client.firmware_version = None
    client.read_message = AsyncMock(return_value=MESSAGE)
    client.write_message = AsyncMock(return_value=True)
    return client


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a physical board's config entry."""
    hass.data[DOMAIN] = {}
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "board", CONF_API_KEY: "key"}
    )
    entry.add_to_hass(hass)
    return entry


async def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock
) -> VestaboardCoordinator:
    coordinator = VestaboardCoordinator(hass, entry, client)
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    return coordinator


def _reauth_flows(hass: HomeAssistant, entry: MockConfigEntry) -> list:
    return hass.config_entries.flow.async_progress_by_handler(
        DOMAIN, match_context={"entry_id": entry.entry_id, "source": SOURCE_REAUTH}
    )


async def _poll(
    hass: HomeAssistant,
    coordinator: VestaboardCoordinator,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.tick(POLL)
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_empty_response_is_not_an_auth_failure(
    hass: HomeAssistant, entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """A board answering with no message keeps its state instead of reauthenticating."""
    client = _client()
    coordinator = await _coordinator(hass, entry, client)

    client.read_message.return_value = None
    await _poll(hass, coordinator, freezer)
    assert coordinator.last_update_success
    assert coordinator.data == MESSAGE

    freezer.tick(READ_FAILURE_GRACE)
    await _poll(hass, coordinator, freezer)
    assert not coordinator.last_update_success
    assert not _reauth_flows(hass, entry)


async def test_rejected_key_reauthenticates_only_once_persistent(
    hass: HomeAssistant, entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """Reauthentication starts only after the key is rejected for the grace period."""
    client = _client()
    coordinator = await _coordinator(hass, entry, client)

    client.read_message.side_effect = InvalidApiKeyError
    await _poll(hass, coordinator, freezer)
    assert coordinator.last_update_success
    assert coordinator.data == MESSAGE

    freezer.tick(READ_FAILURE_GRACE)
    await _poll(hass, coordinator, freezer)
    assert not coordinator.last_update_success
    assert not _reauth_flows(hass, entry)

    freezer.tick(AUTH_FAILURE_GRACE)
    await _poll(hass, coordinator, freezer)
    assert len(_reauth_flows(hass, entry)) == 1


async def test_accepted_key_dismisses_reauth(
    hass: HomeAssistant, entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """Reauthentication is dismissed once the board accepts its key again."""
    client = _client()
    coordinator = await _coordinator(hass, entry, client)

    client.read_message.side_effect = InvalidApiKeyError
    await _poll(hass, coordinator, freezer)
    freezer.tick(AUTH_FAILURE_GRACE)
    await _poll(hass, coordinator, freezer)
    assert len(_reauth_flows(hass, entry)) == 1

    client.read_message.side_effect = None
    await _poll(hass, coordinator, freezer)
    assert coordinator.last_update_success
    assert not _reauth_flows(hass, entry)


async def test_rejected_key_grace_survives_setup_retries(
    hass: HomeAssistant, entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """Setup retries, which each create a coordinator, don't restart the grace period."""
    client = _client()
    client.read_message.side_effect = InvalidApiKeyError

    first = VestaboardCoordinator(hass, entry, client)
    await first.async_refresh()
    await hass.async_block_till_done()
    assert not _reauth_flows(hass, entry)

    freezer.tick(AUTH_FAILURE_GRACE)
    second = VestaboardCoordinator(hass, entry, client)
    await second.async_refresh()
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass, entry)) == 1


@pytest.mark.parametrize(
    "error",
    [
        ClientConnectionError(),
        TimeoutError(),
        ClientResponseError(None, (), status=503),
    ],
)
async def test_write_retries_temporary_failures(
    hass: HomeAssistant, entry: MockConfigEntry, error: Exception
) -> None:
    """A write that fails temporarily is tried again."""
    client = _client()
    coordinator = await _coordinator(hass, entry, client)
    client.write_message.side_effect = [error, error, True]

    with patch("custom_components.vestaboard.coordinator.asyncio.sleep") as sleep:
        await coordinator.write_and_update_state({"characters": MESSAGE})

    assert client.write_message.await_count == WRITE_ATTEMPTS
    assert [call.args[0] for call in sleep.await_args_list] == [2, 4]


async def test_write_gives_up_after_attempts(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A write that keeps failing raises once out of attempts."""
    client = _client()
    coordinator = await _coordinator(hass, entry, client)
    client.write_message.side_effect = ClientConnectionError

    with (
        patch("custom_components.vestaboard.coordinator.asyncio.sleep"),
        pytest.raises(ClientConnectionError),
    ):
        await coordinator.write_and_update_state({"characters": MESSAGE})

    assert client.write_message.await_count == WRITE_ATTEMPTS


async def test_write_does_not_retry_rejections(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A write the board rejects isn't tried again."""
    client = _client()
    coordinator = await _coordinator(hass, entry, client)
    client.write_message.side_effect = ClientResponseError(None, (), status=401)

    with pytest.raises(ClientResponseError):
        await coordinator.write_and_update_state({"characters": MESSAGE})

    assert client.write_message.await_count == 1
