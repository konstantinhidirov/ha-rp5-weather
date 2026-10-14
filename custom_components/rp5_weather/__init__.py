"""RP5 Weather custom integration."""
from __future__ import annotations

from datetime import timedelta
import logging

import aiohttp
from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import RP5Client
from .const import CONF_INTERVAL, CONF_URL, DEFAULT_INTERVAL
from .parser import ParseError

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.WEATHER, Platform.SENSOR]


class RP5Coordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name='RP5 Weather', config_entry=entry,
                         update_interval=timedelta(minutes=entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)))
        self.client = RP5Client(async_get_clientsession(hass), entry.data[CONF_URL])

    async def _async_update_data(self):
        try:
            return await self.client.fetch()
        except (aiohttp.ClientError, TimeoutError, ParseError) as err:
            raise UpdateFailed(str(err)) from err


async def async_setup_entry(hass, entry):
    coordinator = RP5Coordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
