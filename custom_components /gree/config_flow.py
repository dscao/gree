"""Config flow for gree ac integration."""

from __future__ import annotations

import logging
import uuid
import voluptuous as vol
import requests
import binascii
import importlib.util
import socket
import base64
import re
import sys

import asyncio
import logging
import binascii
import os.path

import json,base64
from hashlib import md5

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode
from collections import OrderedDict
from .data_fetcher import DataFetcher
from .const import (
    DOMAIN, 
    CONF_SWITCHS, 
    CONF_HOST, 
    CONF_PORT, 
    CONF_MAC, 
    CONF_TARGET_TEMP_STEP, 
    CONF_TEMP_SENSOR, 
    CONF_UPDATE_INTERVAL, 
    CONF_LIGHTS, 
    CONF_XFAN, 
    CONF_HEALTH, 
    CONF_POWERSAVE, 
    CONF_SLEEP, 
    CONF_QUIET,
    CONF_EIGHTDEGHEAT, 
    CONF_AIR, 
    CONF_ENCRYPTION_KEY, 
    CONF_UID,
    CONF_AUX_HEAT,
    )
from configparser import ConfigParser
from Crypto.Cipher import AES
try: import simplejson
except ImportError: import json as simplejson

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 7000
DEFAULT_TIMEOUT = 10
DEFAULT_TARGET_TEMP_STEP = 1
GENERIC_GREE_DEVICE_KEY = "a3K8Bx%2r8Y7#xDh"

@config_entries.HANDLERS.register(DOMAIN)
class FlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """handle config flow for this integration"""
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlow(config_entry)
        
        
    def __init__(self):
        """Initialize."""
        self._errors = {}
        
    # Pad helper method to help us get the right string for encrypting
    def Pad(self, s):
        aesBlockSize = 16
        return s + (aesBlockSize - len(s) % aesBlockSize) * chr(aesBlockSize - len(s) % aesBlockSize)            

    def FetchResult(self, cipher, ip_addr, port, timeout_s, json):
        _LOGGER.info('Fetching(%s, %s, %s, %s)' % (ip_addr, port, timeout_s, json))
        clientSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        clientSock.settimeout(timeout_s)
        clientSock.sendto(bytes(json, "utf-8"), (ip_addr, port))
        data, addr = clientSock.recvfrom(64000)
        receivedJson = simplejson.loads(data)
        clientSock.close()
        pack = receivedJson['pack']
        base64decodedPack = base64.b64decode(pack)
        decryptedPack = cipher.decrypt(base64decodedPack)
        decodedPack = decryptedPack.decode("utf-8")
        replacedPack = decodedPack.replace('\x0f', '').replace(decodedPack[decodedPack.rindex('}')+1:], '')
        loadedJsonPack = simplejson.loads(replacedPack)        
        return loadedJsonPack

    def GetDeviceKey(self, mac_addr, host, port, timeout_s):
        _LOGGER.info('Retrieving HVAC encryption key')
        cipher = AES.new(GENERIC_GREE_DEVICE_KEY.encode("utf8"), AES.MODE_ECB)
        pack = base64.b64encode(cipher.encrypt(self.Pad('{"mac":"' + str(mac_addr) + '","t":"bind","uid":0}').encode("utf8"))).decode('utf-8')
        jsonPayloadToSend = '{"cid": "app","i": 1,"pack": "' + pack + '","t":"pack","tcid":"' + str(mac_addr) + '","uid": 0}'
        return self.FetchResult(cipher, host, port, timeout_s, jsonPayloadToSend)['key']
        
    def GetDeviceInfo(self, host, port, timeout_s):
        _LOGGER.info('Get Mac')
        cipher = AES.new(GENERIC_GREE_DEVICE_KEY.encode("utf8"), AES.MODE_ECB)
        jsonPayloadToSend = '{"t": "scan"}'
        return self.FetchResult(cipher, host, port, timeout_s, jsonPayloadToSend)
        
    # def GetDeviceMAC(self, bcast_iface):
        # await self.send({"t": "scan"}, (str(bcast_iface), 7000))

    async def async_step_user(self, user_input={}):
        self._errors = {}
        if user_input is not None:
            config_data = {}
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]            
            target_temp_step = user_input[CONF_TARGET_TEMP_STEP]            
            
            self._host = host
            self._port = port
            self._timeout = DEFAULT_TIMEOUT
            
            self._deviceinfo = self.GetDeviceInfo(self._host, self._port, self._timeout)
            _LOGGER.debug(self._deviceinfo)

            self._mac_addr = self._deviceinfo["mac"]            
            
            self._encryption_key = self.GetDeviceKey(self._mac_addr, self._host, self._port, self._timeout)
            _LOGGER.info('Fetched device encrytion key: %s' % str(self._encryption_key))

            if not self._encryption_key:
                self._errors["base"] = "unkown"
                return await self._show_config_form(user_input)

            _LOGGER.debug(
                "gree successfully, save data for gree: %s",
                host,
            )
            await self.async_set_unique_id(f"climate.gree-{self._mac_addr}")
            self._abort_if_unique_id_configured()

            config_data[CONF_HOST] = host
            config_data[CONF_PORT] = port
            config_data[CONF_MAC] = self._mac_addr
            config_data[CONF_TARGET_TEMP_STEP] = target_temp_step
            config_data[CONF_ENCRYPTION_KEY] = str(self._encryption_key)
            return self.async_create_entry(title=f"gree-{host}", data=config_data)

        return await self._show_config_form(user_input)

    async def _show_config_form(self, user_input):

        data_schema = OrderedDict()
        data_schema[vol.Required(CONF_HOST, default = "192.168.8.48")] = str
        data_schema[vol.Required(CONF_PORT, default = DEFAULT_PORT)] = int
        data_schema[vol.Required(CONF_TARGET_TEMP_STEP, default = DEFAULT_TARGET_TEMP_STEP)] = int
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(data_schema), errors=self._errors
        )


class OptionsFlow(config_entries.OptionsFlow):
    """Config flow options for autoamap."""

    def __init__(self, config_entry):
        """Initialize autoamap options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TEMP_SENSOR,
                        default=self.config_entry.options.get(CONF_TEMP_SENSOR, self.config_entry.data.get(CONF_TEMP_SENSOR, "None"))
                    ): vol.All(vol.Coerce(str)),
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=self.config_entry.options.get(CONF_UPDATE_INTERVAL, 5),
                    ): vol.All(vol.Coerce(int), vol.Range(min=2, max=60)),
                    vol.Optional(
                        CONF_ENCRYPTION_KEY,
                        default=self.config_entry.options.get(CONF_ENCRYPTION_KEY, self.config_entry.data.get(CONF_ENCRYPTION_KEY))
                    ): vol.All(vol.Coerce(str)),
                    vol.Optional(CONF_SWITCHS, default=self.config_entry.options.get(CONF_SWITCHS)): SelectSelector(
                            SelectSelectorConfig(
                                options=[
                                    {"value": "Lig", "label": "Panel Light"},
                                    {"value": "Quiet", "label": "Quiet"},
                                    {"value": "Air", "label": "Fresh Air"},
                                    {"value": "Blo", "label": "XFan"},
                                    {"value": "Health", "label": "Health mode"},
                                    # {"value": CONF_AUX_HEAT, "label": CONF_AUX_HEAT},
                                ], 
                                multiple=True,translation_key=CONF_SWITCHS
                            )
                        ),
                    vol.Optional(
                        CONF_UID,
                        default=self.config_entry.options.get(CONF_UID, 0)
                    ): vol.All(vol.Coerce(int)),
                }
            ),
        )
