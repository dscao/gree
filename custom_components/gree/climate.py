import importlib.util
import socket
import base64
import re
import sys
import enum
from enum import IntEnum, unique
from typing import Any

import asyncio
import logging
import binascii
import os.path
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_NONE,
    PRESET_SLEEP,
    SWING_BOTH,
    SWING_HORIZONTAL,
    SWING_OFF,
    SWING_VERTICAL,
    ClimateEntity,
)


from homeassistant.components.climate.const import (
    HVACMode, 
    ClimateEntityFeature,
)

from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT, ATTR_TEMPERATURE, 
    CONF_NAME, CONF_HOST, CONF_PORT, CONF_MAC, CONF_TIMEOUT, CONF_CUSTOMIZE, 
    STATE_ON, STATE_OFF, STATE_UNKNOWN, 
    UnitOfTemperature, PRECISION_WHOLE, PRECISION_TENTHS)

from homeassistant.helpers.event import (async_track_state_change_event)
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo
from configparser import ConfigParser
from Crypto.Cipher import AES
try: import simplejson
except ImportError: import json as simplejson

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
    CONF_HUM_SENSOR,
    CONF_SWITCHS,
    CONF_LIGHTS,
    CONF_XFAN,
    CONF_HEALTH,
    CONF_POWERSAVE,
    CONF_QUIET,
    CONF_SLEEP,
    CONF_EIGHTDEGHEAT,
    CONF_AIR,
    CONF_ENCRYPTION_KEY,
    CONF_UID,
    CONF_AUX_HEAT,
    CONF_DISABLE_AVAILABLE_CHECK,
    CONF_MAX_ONLINE_ATTEMPTS,
    FAN_MEDIUM_HIGH,
    FAN_MEDIUM_LOW,
    CONF_VERSION,
    CONF_ENCRYPTION_VERSION,
)
from homeassistant.exceptions import ConfigEntryNotReady

import datetime
import logging

_LOGGER = logging.getLogger(__name__)

SUPPORT_FLAGS = (
    ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.FAN_MODE
    | ClimateEntityFeature.PRESET_MODE
    | ClimateEntityFeature.SWING_MODE
    | ClimateEntityFeature.TURN_OFF
    | ClimateEntityFeature.TURN_ON
)

DEFAULT_NAME = 'Gree Climate'

DEFAULT_PORT = 7000
DEFAULT_TIMEOUT = 10

class Props(enum.Enum):
    POWER = "Pow"
    MODE = "Mod"

    # Dehumidifier fields
    HUM_SET = "Dwet"
    HUM_SENSOR = "DwatSen"
    CLEAN_FILTER = "Dfltr"
    WATER_FULL = "DwatFul"
    DEHUMIDIFIER_MODE = "Dmod"

    TEMP_SET = "SetTem"
    TEMP_SENSOR = "TemSen"
    TEMP_UNIT = "TemUn"
    TEMP_BIT = "TemRec"
    FAN_SPEED = "WdSpd"
    FRESH_AIR = "Air"
    XFAN = "Blo"
    ANION = "Health"
    SLEEP = "SwhSlp"
    SLEEP_MODE = "SlpMod"
    LIGHT = "Lig"
    SWING_HORIZ = "SwingLfRig"
    SWING_VERT = "SwUpDn"
    QUIET = "Quiet"
    TURBO = "Tur"
    STEADY_HEAT = "StHt"
    POWER_SAVE = "SvSt"
    AUX_HEAT = "AssHt"
    UNKNOWN_HEATCOOLTYPE = "HeatCoolType"


@unique
class TemperatureUnits(IntEnum):
    C = 0
    F = 1


@unique
class Mode(IntEnum):
    Auto = 0
    Cool = 1
    Dry = 2
    Fan = 3
    Heat = 4


@unique
class FanSpeed(IntEnum):
    Auto = 0
    Low = 1
    MediumLow = 2
    Medium = 3
    MediumHigh = 4
    High = 5


@unique
class HorizontalSwing(IntEnum):
    Default = 0
    FullSwing = 1
    Left = 2
    LeftCenter = 3
    Center = 4
    RightCenter = 5
    Right = 6


@unique
class VerticalSwing(IntEnum):
    Default = 0
    FullSwing = 1
    FixedUpper = 2
    FixedUpperMiddle = 3
    FixedMiddle = 4
    FixedLowerMiddle = 5
    FixedLower = 6
    SwingUpper = 7
    SwingUpperMiddle = 8
    SwingMiddle = 9
    SwingLowerMiddle = 10
    SwingLower = 11

class DehumidifierMode(IntEnum):
    Default = 0
    AnionOnly = 9
    
def generate_temperature_record(temp_f):
    temSet = round((temp_f - 32.0) * 5.0 / 9.0)
    temRec = (int)((((temp_f - 32.0) * 5.0 / 9.0) - temSet) > 0)
    return {"f": temp_f, "temSet": temSet, "temRec": temRec}
    
# from the remote control and gree app
TEMP_MIN = 8
TEMP_MAX = 30
TEMP_OFFSET = 40
TEMP_MIN_F = 46
TEMP_MAX_F = 86
TEMP_TABLE = [generate_temperature_record(x) for x in range(TEMP_MIN_F, TEMP_MAX_F + 1)]
HUMIDITY_MIN = 30
HUMIDITY_MAX = 80


PRESET_MODES = [
    PRESET_ECO,  # Power saving mode
    PRESET_AWAY,  # Steady heat, or 8C mode on gree units
    PRESET_NONE,  # Default operating mode
    PRESET_SLEEP,  # Sleep mode
]

PRESET_MODES_NOAWAY = [
    PRESET_ECO,  # Power saving mode
    PRESET_NONE,  # Default operating mode
    PRESET_SLEEP,  # Sleep mode
]

SWING_MODES = [SWING_OFF, SWING_VERTICAL, SWING_HORIZONTAL, SWING_BOTH]

# fixed values in gree mode lists
HVAC_MODES = [HVACMode.AUTO, HVACMode.COOL, HVACMode.DRY, HVACMode.FAN_ONLY, HVACMode.HEAT, HVACMode.OFF]

FAN_MODES = ['auto', 'low', 'medium-low', 'medium', 'medium-high', 'high', 'Turbo', 'Quiet']


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Add Switchentities from a config_entry."""      
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    host = config_entry.data[CONF_HOST]
    port = config_entry.data[CONF_PORT]
    mac = config_entry.data[CONF_MAC]
    target_temp_step = config_entry.data[CONF_TARGET_TEMP_STEP]
    
    encryption_key = config_entry.options.get(CONF_ENCRYPTION_KEY, config_entry.data[CONF_ENCRYPTION_KEY]).encode('utf-8')
    encryption_version = config_entry.options.get(CONF_ENCRYPTION_VERSION, config_entry.data.get(CONF_ENCRYPTION_VERSION, 1))
    version = config_entry.data.get(CONF_VERSION, 0)
    uid = config_entry.options.get(CONF_UID, 0)    
    temp_sensor_entity_id = config_entry.options.get(CONF_TEMP_SENSOR)
    hum_sensor_entity_id = config_entry.options.get(CONF_HUM_SENSOR)
    climates = []
    
    climates.append(GreeClimate(hass, coordinator, mac, host, port, target_temp_step, temp_sensor_entity_id, hum_sensor_entity_id, encryption_version, encryption_key, version, uid))
    async_add_entities(climates, False)    
         
            

class GreeClimate(ClimateEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "gree_ac"
    _attr_precision = PRECISION_WHOLE
    _attr_supported_features = SUPPORT_FLAGS
    _enable_turn_on_off_backwards_compatibility = False
    
    def __init__(self, hass, coordinator, mac, host, port, target_temp_step, temp_sensor_entity_id, hum_sensor_entity_id, encryption_version, encryption_key, version, uid):
        """Initialize."""
        _LOGGER.info('Initialize the GREE climate device')
        super().__init__()
        self.coordinator = coordinator
        self._host = host
        self._port = port
        self._encryption_key = encryption_key
        self._encryption_version = encryption_version
        self._version = version
        self._mac_addr = mac
        self._unique_id = f"climate.gree-{self._mac_addr}"
        self._name = None #f"{DEFAULT_NAME}-{host}"
        self._state = None
        
        if uid:
            self._uid = uid
        else:
            self._uid = 0

        self._attr_device_class = "climate"
        self._attr_icon = "mdi:air-conditioner"
        self._attr_entity_registry_enabled_default = True
        self._hass = hass

        self._target_temperature = None
        self._target_temperature_step = target_temp_step
        self._unit_of_measurement = hass.config.units.temperature_unit
        
        self._current_temperature = None
        self._temp_sensor_entity_id = temp_sensor_entity_id
        
        self._current_humidity = None
        self._hum_sensor_entity_id = hum_sensor_entity_id

        self._hvac_mode = None
        self._fan_mode = None
        self._swing_mode = None
        self._current_lights = None
        self._current_xfan = None
        self._current_health = None
        self._current_powersave = None
        self._current_sleep = None
        self._current_eightdegheat = None
        self._current_air = None
        
        self._properties = None

        self._fetcher = DataFetcher(self._host, self._port, self._mac_addr, DEFAULT_TIMEOUT, self._uid, self._encryption_version, self._encryption_key, self._hass)
        
        self._attr_device_info = {
            "identifiers": {(DOMAIN, host)},
            "name": f"Gree AC {self._host}", 
            "manufacturer": "gree",
            "model": "Gree AC",
            "sw_version": self._version,
        }
        
        if temp_sensor_entity_id and ("sensor." in temp_sensor_entity_id) and ("temperature" in temp_sensor_entity_id):                    
            state_temperature = self._hass.states.get(temp_sensor_entity_id)
            if state_temperature is not None:
                self._async_update_current_temp(state_temperature)
                _LOGGER.info('安装外部温度传感器实体: ' + str(temp_sensor_entity_id))
            async_track_state_change_event(
                hass, temp_sensor_entity_id, self._async_temp_sensor_changed)
                
        if hum_sensor_entity_id and ("sensor." in hum_sensor_entity_id) and ("humidity" in hum_sensor_entity_id):                    
            state_humidity = self._hass.states.get(hum_sensor_entity_id)
            if state_humidity is not None:
                self._async_update_current_hum(state_humidity)
                _LOGGER.info('安装外部湿度传感器实体: ' + str(hum_sensor_entity_id))
            async_track_state_change_event(
                hass, hum_sensor_entity_id, self._async_hum_sensor_changed)
 
    async def _async_temp_sensor_changed(self, event):
        entity_id = event.data.get('entity_id')
        old_state = event.data.get('old_state')
        new_state = event.data.get('new_state')
        _LOGGER.info('temp_sensor state changed |' + str(entity_id) + '|' + str(old_state) + '|' + str(new_state))
        
        # Handle temperature changes.
        if new_state is None:
            return
        self._async_update_current_temp(new_state)
        self.async_write_ha_state()
    
    @callback    
    def _async_update_current_temp(self, state):
        _LOGGER.info('Thermostat updated with changed temp_sensor state |' + str(state))
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        try:
            _state = state.state
            _LOGGER.info('Current state temp_sensor: ' + _state)
            if self.represents_float(_state):
                self._current_temperature = self._hass.config.units.temperature(
                    float(_state), unit)
                _LOGGER.info('Current temp: ' + str(self._current_temperature))
        except ValueError as ex:
            _LOGGER.error('Unable to update from temp_sensor: %s' % ex) 
            
    async def _async_hum_sensor_changed(self, event):
        entity_id = event.data.get('entity_id')
        old_state = event.data.get('old_state')
        new_state = event.data.get('new_state')
        _LOGGER.info('hum_sensor state changed |' + str(entity_id) + '|' + str(old_state) + '|' + str(new_state))
        
        # Handle temperature changes.
        if new_state is None:
            return
        self._async_update_current_hum(new_state)
        self.async_write_ha_state()
    
    @callback    
    def _async_update_current_hum(self, state):
        _LOGGER.info('Thermostat updated with changed hum_sensor state |' + str(state))
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        try:
            _state = state.state
            _LOGGER.info('Current state hum_sensor: ' + _state)
            if self.represents_float(_state):
                self._current_humidity = float(_state)
                _LOGGER.info('Current hum: ' + str(self._current_humidity))
        except ValueError as ex:
            _LOGGER.error('Unable to update from hum_sensor: %s' % ex)   

    def represents_float(self, s):
        _LOGGER.info('temp_sensor state represents_float |' + str(s))
        try: 
            float(s)
            return True
        except ValueError:
            return False  
    
        
    def _convert_to_units(self, value, bit):
        if self.coordinator.data["TemUn"] != TemperatureUnits.F.value:
            return value

        if value < TEMP_MIN or value > TEMP_MAX:
            raise ValueError(f"Specified temperature {value} is out of range.")

        f = next(t for t in TEMP_TABLE if t["temSet"] == value and t["temRec"] == bit)
        return f["f"]    
            
    @property
    def name(self) -> str:
        """Return the name of the device."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return a unique id for the device."""
        return self._unique_id
        
    @property
    def available(self):
        """Return True if entity is available."""
        return self.coordinator.last_update_success

    @property
    def temperature_unit(self) -> str:
        """Return the temperature units for the device."""
        units = self.coordinator.data["TemUn"]
        if units == TemperatureUnits.C:
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT

    @property
    def current_humidity(self) -> float:
        """Return the reported current current_humidity for the device."""
        return self._current_humidity
        
    @property
    def current_temperature(self) -> float:
        """Return the reported current temperature for the device."""
        if self.coordinator.data['TemSen'] and (self._temp_sensor_entity_id == "" or self._temp_sensor_entity_id == "None" or self._temp_sensor_entity_id == None):
            self._current_temperature = self.coordinator.data['TemSen']            
            prop = self.coordinator.data['TemSen']
            bit = self.coordinator.data['TemRec']
            if prop is not None:
                v = self._version and int(self._version.split(".")[0])
                try:
                    if v == 4:
                        return self._convert_to_units(prop, bit)
                    elif prop != 0:
                        return self._convert_to_units(prop - TEMP_OFFSET, bit)
                except ValueError:
                    logging.warning("Converting unexpected set temperature value %s", prop)
            return self.target_temperature
        
        return self._current_temperature

    @property
    def target_temperature(self) -> float:
        """Return the target temperature for the device."""
        if (int(self.coordinator.data["StHt"]) == 1):
            self._target_temperature = 8
            _LOGGER.info('HA target temp set according to HVAC state to 8℃ since 8℃ heating mode is active')
        else:
            self._target_temperature = self.coordinator.data["SetTem"]
        return self._target_temperature

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if ATTR_TEMPERATURE not in kwargs:
            raise ValueError(f"Missing parameter {ATTR_TEMPERATURE}")

        temperature = kwargs[ATTR_TEMPERATURE]
        _LOGGER.debug(
            "Setting temperature to %d for Gree AC %s",
            temperature,
            self._host,
        )
        
        await self._fetcher.SyncState({'SetTem': int(kwargs.get(ATTR_TEMPERATURE))})
        #self.schedule_update_ha_state()
        self.async_write_ha_state()
        


    @property
    def min_temp(self) -> float:
        """Return the minimum temperature supported by the device."""
        if self.temperature_unit == UnitOfTemperature.CELSIUS:
            return TEMP_MIN
        return TEMP_MIN_F

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature supported by the device."""
        if self.temperature_unit == UnitOfTemperature.CELSIUS:
            return TEMP_MAX
        return TEMP_MAX_F

    @property
    def target_temperature_step(self) -> float:
        """Return the target temperature step support by the device."""
        return self._target_temperature_step

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current HVAC mode for the device."""         
        if (self.coordinator.data['Pow'] == 0):
            return HVACMode.OFF

        return HVAC_MODES[self.coordinator.data["Mod"]]

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode not in HVAC_MODES:
            raise ValueError(f"Invalid hvac_mode: {hvac_mode}")

        _LOGGER.debug(
            "Setting HVAC mode to %s for device %s",
            hvac_mode,
            self._name,
        )

        if (hvac_mode == HVACMode.OFF):
            await self._fetcher.SyncState({'Pow': 0})
        else:
            await self._fetcher.SyncState({'Mod': HVAC_MODES.index(hvac_mode), 'Pow': 1})
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Turn on the device."""
        _LOGGER.debug("Turning on HVAC for device %s", self._name)
        await self._fetcher.SyncState({'Pow': 1})
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Turn off the device."""
        _LOGGER.debug("Turning off HVAC for device %s", self._name)
        await self._fetcher.SyncState({'Pow': 0})
        self.async_write_ha_state()


    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the HVAC modes support by the device."""
        #_LOGGER.debug('hvac_modes(): ' + str(HVAC_MODES))
        # Return the list of available operation modes.
        return HVAC_MODES

    @property
    def preset_mode(self) -> str:
        """Return the current preset mode for the device."""
        if self.coordinator.data["StHt"]:
            return PRESET_AWAY
        if self.coordinator.data["SvSt"]:
            return PRESET_ECO
        if self.coordinator.data["SlpMod"]:
            return PRESET_SLEEP
        return PRESET_NONE

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode not in PRESET_MODES:
            raise ValueError(f"Invalid preset mode: {preset_mode}")

        _LOGGER.debug(
            "Setting preset mode to %s for device %s",
            preset_mode,
            self._name,
        )

        if preset_mode == PRESET_AWAY and self.coordinator.data.get('Mod') == 4: #保持室内温度8摄氏度只能在制热模式
            await self._fetcher.SyncState({'StHt': 1, 'SvSt': 0, 'SlpMod': 0, 'Tur': 0,'Quiet': 0})
        elif preset_mode == PRESET_ECO:
            await self._fetcher.SyncState({'StHt': 0, 'SvSt': 1, 'SlpMod': 0, 'Tur': 0,'Quiet': 0})
        elif preset_mode == PRESET_BOOST:
            await self._fetcher.SyncState({'StHt': 0, 'SvSt': 0, 'SlpMod': 0, 'Tur': 0,'Quiet': 0})
        elif preset_mode == PRESET_SLEEP:
            await self._fetcher.SyncState({'StHt': 0, 'SvSt': 0, 'SlpMod': 1, 'Tur': 0,'Quiet': 0})
        else:
            await self._fetcher.SyncState({'StHt': 0, 'SvSt': 0, 'SlpMod': 0, 'Tur': 0,'Quiet': 0})
        self.async_write_ha_state()

    @property
    def preset_modes(self) -> list[str]:
        """Return the preset modes support by the device."""
        if self.coordinator.data.get('Mod') == 4:
            return PRESET_MODES
        else:
            #非制热模式时不能开启保持室内温度8摄氏度模式
            return PRESET_MODES_NOAWAY

    @property
    def fan_mode(self) -> str | None:
        """Return the current fan mode for the device."""
        if (int(self.coordinator.data['Quiet']) >= 1):
            return 'Quiet'
        elif (int(self.coordinator.data['Tur']) == 1):
            return 'Turbo'
        else:
            speed = self.coordinator.data["WdSpd"]
            return FAN_MODES[speed]

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        if fan_mode == "Quiet":
            await self._fetcher.SyncState({'StHt': 0, 'SvSt': 0, 'SlpMod': 0, 'Tur': 0,'Quiet': 1})
        elif fan_mode == "Turbo":
            await self._fetcher.SyncState({'StHt': 0, 'SvSt': 0, 'SlpMod': 0, 'Tur': 1,'Quiet': 0})
        else:            
            if fan_mode not in FAN_MODES:
                raise ValueError(f"Invalid fan mode: {fan_mode}")
            await self._fetcher.SyncState({'WdSpd': FAN_MODES.index(fan_mode), 'Tur': 0,'Quiet': 0})
        self.async_write_ha_state()

    @property
    def fan_modes(self) -> list[str]:
        """Return the fan modes support by the device."""
        return FAN_MODES

    @property
    def swing_mode(self) -> str:
        """Return the current swing mode for the device."""
        h_swing = self.coordinator.data["SwingLfRig"] == 1
        v_swing = self.coordinator.data["SwUpDn"] == 1

        if h_swing and v_swing:
            return SWING_BOTH
        if h_swing:
            return SWING_HORIZONTAL
        if v_swing:
            return SWING_VERTICAL
        return SWING_OFF

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set new target swing operation."""
        if swing_mode not in SWING_MODES:
            raise ValueError(f"Invalid swing mode: {swing_mode}")

        _LOGGER.debug(
            "Setting swing mode to %s for device %s",
            swing_mode,
            self._name,
        )

        if swing_mode == SWING_BOTH:
            await self._fetcher.SyncState({'SwingLfRig': 1,'SwUpDn': 1})
        elif swing_mode == SWING_VERTICAL:
            await self._fetcher.SyncState({'SwingLfRig': 0,'SwUpDn': 1})
        elif swing_mode == SWING_HORIZONTAL:
            await self._fetcher.SyncState({'SwingLfRig': 1,'SwUpDn': 0})
        else:
            await self._fetcher.SyncState({'SwingLfRig': 0,'SwUpDn': 0})
        self.async_write_ha_state()
        
    @property
    def swing_modes(self) -> list[str]:
        """Return the swing modes currently supported for this device."""
        return SWING_MODES

    async def async_added_to_hass(self):
        """Connect to dispatcher listening for entity data notifications."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
        _LOGGER.info('Gree climate device added to hass()')

    async def async_update(self):
        """Update entity."""
        await self.coordinator.async_request_refresh()
        _LOGGER.debug(self.coordinator)
                
