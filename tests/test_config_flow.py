"""Tests for the Vestaboard config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vestaboard.client import EndpointStatus
from custom_components.vestaboard.const import DOMAIN
from homeassistant.config_entries import SOURCE_DHCP, SOURCE_IGNORE
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo


async def test_dhcp_moves_board_despite_ignored_discovery(
    hass: HomeAssistant,
) -> None:
    """An ignored discovery, which has no API key, doesn't stop a board's host updating."""
    MockConfigEntry(
        domain=DOMAIN,
        source=SOURCE_IGNORE,
        unique_id="4c93a6000001",
        data={CONF_HOST: "192.0.2.1"},
    ).add_to_hass(hass)
    board = MockConfigEntry(
        domain=DOMAIN,
        unique_id="4c93a6000002",
        data={CONF_HOST: "192.0.2.2", CONF_API_KEY: "key"},
    )
    board.add_to_hass(hass)

    with (
        patch(
            "custom_components.vestaboard.client.VestaboardLocalClient.check_endpoint",
            AsyncMock(return_value=EndpointStatus.VALID),
        ),
        patch("custom_components.vestaboard.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_DHCP},
            data=DhcpServiceInfo(
                ip="192.0.2.3", hostname="vestaboard", macaddress="4c93a6000003"
            ),
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert board.data[CONF_HOST] == "192.0.2.3"
