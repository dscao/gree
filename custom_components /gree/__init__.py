"""gree integration."""
from __future__ import annotations
from async_timeout import timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, Config
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .data_fetcher import DataFetcher
from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_MAC,
    CONF_UPDATE_INTERVAL,
    COORDINATOR,
    UNDO_UPDATE_LISTENER,
    CONF_TARGET_TEMP_STEP,
    CONF_TEMP_SENSOR,
    CONF_SWITCHS,
    CONF_LIGHTS,
    CONF_XFAN,
    CONF_HEALTH,
    CONF_POWERSAVE,
    CONF_SLEEP,
    CONF_EIGHTDEGHEAT,
    CONF_AIR,
    CONF_ENCRYPTION_KEY,
    CONF_UID,
    CONF_AUX_HEAT,
    CONF_VERSION,
)
from homeassistant.exceptions import ConfigEntryNotReady

import datetime
import asyncio
import logging

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SWITCH]


async def async_setup(hass: HomeAssistant, config: Config) -> bool:
    """Set up configured bjtoon health code."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    mac = entry.data[CONF_MAC]
    encryption_key = entry.options.get(CONF_ENCRYPTION_KEY, entry.data[CONF_ENCRYPTION_KEY]).encode('utf-8')
    update_interval_seconds = entry.options.get(CONF_UPDATE_INTERVAL, 5)
    uid = entry.options.get(CONF_UID, 0)
    version = entry.options.get(CONF_VERSION, 0)

    #uid = entry.data.options.get[CONF_UID,""]

    coordinator = DataUpdateCoordinator(hass, host, port, mac, update_interval_seconds, uid, encryption_key)
    
    await coordinator.async_refresh()

    if not coordinator.last_update_success:
        raise ConfigEntryNotReady

    undo_listener = entry.add_update_listener(update_listener)

    hass.data[DOMAIN][entry.entry_id] = {
        COORDINATOR: coordinator,
        UNDO_UPDATE_LISTENER: undo_listener,
    }

    for component in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, component)
        )

    return True

async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )

    hass.data[DOMAIN][entry.entry_id][UNDO_UPDATE_LISTENER]()

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
    

async def update_listener(hass, entry):
    """Update listener."""
    await hass.config_entries.async_reload(entry.entry_id)


class DataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data."""

    def __init__(self, hass, host, port, mac, update_interval_seconds, uid, encryption_key):
        """Initialize."""
        update_interval = datetime.timedelta(seconds=update_interval_seconds)
        timeout_s = 3
        self._encryption_key = encryption_key
        _LOGGER.debug("Data will be update every %s", update_interval)
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=update_interval)
        self._mac_addr = mac
        self._fetcher = DataFetcher(host, port, self._mac_addr, timeout_s, uid, self._encryption_key, hass)

    async def _async_update_data(self):
        """Update data via DataFetcher."""
        try:
            async with timeout(5):                
                Values = await self._fetcher.get_data()
                currentValues = Values.get("currentValues")
                if not currentValues:
                    raise UpdateFailed("failed in getting data")                
                data = {'Pow': currentValues[0], 'Mod': currentValues[1], 'SetTem': currentValues[2], 'WdSpd': currentValues[3], 'Air': currentValues[4], 'Blo': currentValues[5], 'Health': currentValues[6], 'SwhSlp': currentValues[7], 'Lig': currentValues[8], 'SwingLfRig': currentValues[9], 'SwUpDn': currentValues[10], 'Quiet': currentValues[11], 'Tur': currentValues[12], 'StHt': currentValues[13], 'TemUn': currentValues[14], 'HeatCoolType': currentValues[15], 'TemRec': currentValues[16], 'SvSt': currentValues[17], 'SlpMod': currentValues[18], 'AssHt': currentValues[19], 'TemSen': currentValues[20]}
                _LOGGER.debug(data)
                return data
        except Exception as error:
            raise UpdateFailed(error) from error
