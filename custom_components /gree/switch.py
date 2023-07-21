import importlib.util
import socket
import base64
import re
import sys

import asyncio
import logging
import binascii
import os.path
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_MAC,
    COORDINATOR,
    UNDO_UPDATE_LISTENER,
    CONF_SWITCHS,
    CONF_LIGHTS,
    CONF_XFAN,
    CONF_HEALTH,
    CONF_QUIET,
    CONF_EIGHTDEGHEAT,
    CONF_AIR,
    CONF_ENCRYPTION_KEY,
    CONF_UID,
    CONF_AUX_HEAT,
    CONF_VERSION,
)
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo
from configparser import ConfigParser
from Crypto.Cipher import AES
try: import simplejson
except ImportError: import json as simplejson

from .data_fetcher import DataFetcher
import datetime
import logging

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
    
GREE_SWITCHES: tuple[SwitchEntityDescription, ...] = (
    SwitchEntityDescription(
        icon="mdi:lightbulb",
        name="Panel Light",
        key="Lig",
    ),
    # SwitchEntityDescription(
        # name="Quiet",
        # key="Quiet",
    # ),
    SwitchEntityDescription(
        name="Fresh Air",
        key="Air",
    ),
    SwitchEntityDescription(
        name="XFan",
        key="Blo",
    ),
    SwitchEntityDescription(
        icon="mdi:pine-tree",
        name="Health mode",
        key="Health",
        entity_registry_enabled_default=True,
    ),
)
SWITCH_TYPES_MAP = { description.key: description for description in GREE_SWITCHES }

SWITCH_TYPES_KEYS = { description.key for description in GREE_SWITCHES }


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Add Switchentities from a config_entry."""      
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    host = config_entry.data[CONF_HOST]
    port = config_entry.data[CONF_PORT]
    mac = config_entry.data[CONF_MAC]    
    encryption_key = config_entry.options.get(CONF_ENCRYPTION_KEY, config_entry.data[CONF_ENCRYPTION_KEY]).encode('utf-8')
    version = config_entry.data.get(CONF_VERSION, 0)
    uid = config_entry.options.get(CONF_UID, 0) 
    

    switchs = []
    
    _LOGGER.debug(config_entry.options.get(CONF_SWITCHS))
    
    enabled_switchs = [s for s in config_entry.options.get(
        CONF_SWITCHS, []) if s in SWITCH_TYPES_KEYS]
        
    for switch in enabled_switchs:
        _LOGGER.debug( SWITCH_TYPES_MAP[switch])
        switchs.append(GreeSwitch(hass, SWITCH_TYPES_MAP[switch], coordinator, host, port, mac, encryption_key, uid, version))
    async_add_entities(switchs, False)
           

class GreeSwitch(SwitchEntity):
    _attr_has_entity_name = True
    def __init__(self, hass, description, coordinator, host, port, mac, encryption_key, uid, version):
        """Initialize."""
        super().__init__()
        self.entity_description = description
        self._attr_translation_key = self.entity_description.key.lower()
        self.coordinator = coordinator
        self._hass = hass
        self._host = host
        self._port = port
        self._encryption_key = encryption_key
        self._uid = uid
        self._mac_addr = mac
        self._unique_id = f"{DOMAIN}-switch-{self._mac_addr}-{self.entity_description.key}"
        self._name = self.entity_description.name
        self._change = True
        self._switchonoff = None
        self._state = None
        self._version = version
        
        self._fetcher = DataFetcher(self._host, self._port, self._mac_addr, DEFAULT_TIMEOUT, self._uid, self._encryption_key, self._hass)
        
        self._switchonoff = self.coordinator.data[self.entity_description.key]
        
        self._is_on = self._switchonoff == 1
        self._state = "on" if self._is_on == True else "off"
        
        self._attr_device_info = {
            "identifiers": {(DOMAIN, host)},
            "name": f"Gree AC {self._host}", 
            "manufacturer": "gree",
            "model": "Gree AC",
            "sw_version": self._version,
        }
   
    @property
    def name(self):
        """Return the name."""
        return f"{self._name}"

    @property
    def unique_id(self):
        return self._unique_id
       
    @property
    def should_poll(self):
        """Return the polling requirement of the entity."""
        return False

    @property
    def is_on(self):
        """Check if switch is on."""        
        return self._is_on

    async def async_turn_on(self, **kwargs):
        """Turn switch on."""
        self._is_on = True
        self._change = False
        await self._fetcher.SyncState({self.entity_description.key: 1})
        self._switchonoff = "on"
        self.async_write_ha_state()


    async def async_turn_off(self, **kwargs):
        """Turn switch off."""
        self._is_on = False
        self._change = False
        await self._fetcher.SyncState({self.entity_description.key: 0})
        self._switchonoff = "off"
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Connect to dispatcher listening for entity data notifications."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_update(self):
        """Update entity."""
        await self.coordinator.async_request_refresh()
