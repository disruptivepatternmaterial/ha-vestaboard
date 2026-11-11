"""Tests for the Vestaboard config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from aiohttp import ClientConnectorError

from custom_components.vestaboard.client import EndpointStatus
from custom_components.vestaboard.const import (
    COLOR_BLACK,
    CONF_BOARD_MODEL,
    CONF_ENTRY_TYPE,
    CONF_HEART,
    CONF_MODEL,
    CONF_SHOW_FRAME,
    DOMAIN,
    ENTRY_TYPE_VIRTUAL,
)
from custom_components.vestaboard.vestaboard_model import MODEL_FLAGSHIP, MODEL_NOTE
from homeassistant.config_entries import SOURCE_DHCP, SOURCE_IGNORE, SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from . import MockConfigEntry, setup_virtual_board


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


async def _discover(hass: HomeAssistant, probe: AsyncMock) -> dict:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="4c93a6000002",
        data={CONF_HOST: "192.0.2.2", CONF_API_KEY: "key"},
    ).add_to_hass(hass)
    with patch(
        "custom_components.vestaboard.client.VestaboardLocalClient.check_endpoint",
        probe,
    ):
        return await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_DHCP},
            data=DhcpServiceInfo(
                ip="192.0.2.3", hostname="vestaboard", macaddress="4c93a6000003"
            ),
        )


async def test_dhcp_unreachable_board_does_not_ask_for_key(
    hass: HomeAssistant,
) -> None:
    """A board that can't be reached yet isn't offered as a new board."""
    result = await _discover(
        hass, AsyncMock(side_effect=ClientConnectorError(None, OSError()))
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_dhcp_unanswered_board_does_not_ask_for_key(
    hass: HomeAssistant,
) -> None:
    """A board that doesn't answer like a Vestaboard yet isn't offered as a new board."""
    result = await _discover(hass, AsyncMock(return_value=EndpointStatus.UNKNOWN))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_dhcp_board_rejecting_every_key_asks_for_key(
    hass: HomeAssistant,
) -> None:
    """A board that rejects every stored key is a new board, so its key is asked for."""
    result = await _discover(
        hass, AsyncMock(return_value=EndpointStatus.INVALID_API_KEY)
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "api_key"


async def test_user_menu_without_boards(hass: HomeAssistant) -> None:
    """Test arrays aren't offered until there are boards to put in one."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["device", "virtual"]


async def test_user_menu_offers_array(hass: HomeAssistant) -> None:
    """Test arrays are offered once there are two Notes."""
    await setup_virtual_board(hass, "Note 1", MODEL_NOTE)
    await setup_virtual_board(hass, "Note 2", MODEL_NOTE)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["menu_options"] == ["device", "virtual", "array"]


async def test_user_menu_arrays_need_notes(hass: HomeAssistant) -> None:
    """Test Flagships don't count towards an array."""
    await setup_virtual_board(hass, "Flagship 1")
    await setup_virtual_board(hass, "Flagship 2")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["menu_options"] == ["device", "virtual"]


async def test_virtual_flagship(hass: HomeAssistant) -> None:
    """Test setting up a virtual Flagship."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "virtual"}
    )
    assert result["step_id"] == "virtual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Kitchen", CONF_BOARD_MODEL: MODEL_FLAGSHIP}
    )
    assert result["step_id"] == "appearance"

    options = {CONF_MODEL: COLOR_BLACK, CONF_HEART: True, CONF_SHOW_FRAME: False}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], options)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kitchen"
    assert result["data"] == {
        CONF_ENTRY_TYPE: ENTRY_TYPE_VIRTUAL,
        CONF_BOARD_MODEL: MODEL_FLAGSHIP,
    }
    assert result["options"] == options


async def test_virtual_note_has_no_heart(hass: HomeAssistant) -> None:
    """Test the heart is only offered on a Flagship."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "virtual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Desk", CONF_BOARD_MODEL: MODEL_NOTE}
    )

    assert result["step_id"] == "appearance"
    assert CONF_HEART not in result["data_schema"].schema
