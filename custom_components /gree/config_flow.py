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
    CONF_VERSION,
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

# from the remote control and gree app
TEMP_MIN = 8
TEMP_MAX = 30
TEMP_OFFSET = 40
TEMP_MIN_F = 46
TEMP_MAX_F = 86

HUMIDITY_MIN = 30
HUMIDITY_MAX = 80



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
        
    def GreeGetValues(self, propertyNames):
        self.CIPHER = AES.new(self._encryption_key.encode('utf-8'), AES.MODE_ECB) 
        jsonPayloadToSend = '{"cid":"app","i":0,"pack":"' + base64.b64encode(self.CIPHER.encrypt(self.Pad('{"cols":' + simplejson.dumps(propertyNames) + ',"mac":"' + str(self._mac_addr) + '","t":"status"}').encode("utf8"))).decode('utf-8') + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(0) + '}'
        return self.FetchResult(self.CIPHER, self._host, self._port, DEFAULT_TIMEOUT, jsonPayloadToSend)
         
        
    def request_version(self):
        """Request the firmware version from the device."""
        ret = self.GreeGetValues(["hid","TemSen"])
        _LOGGER.debug(ret)
        hid = ret.get("dat")[0]
        
        # Ex: hid = 362001000762+U-CS532AE(LT)V3.31.bin
        #          ['362001060297+U-CS532AF(MTK)V4.bin']
        if hid:
            match = re.search(r"(?<=V)([\d.]+)\.bin$", hid)
            version = match and match.group(1)
            _LOGGER.debug("version: %s", version)
            

            # Special case firmwares ...
            # if (
            #     self.hid.endswith("_JDV1.bin")
            #     or self.hid.endswith("362001000967V2.bin")
            #     or re.match("^.*\(MTK\)V[1-3]{1}\.bin", self.hid)  # (MTK)V[1-3].bin
            # ):
            #     self.version = "4.0" 
            
        temp = ret.get("dat")[1]
        if temp and temp <= TEMP_OFFSET:
            version = "4.0"
            
        return version

        
    async def async_step_user(self, user_input={}):
        self._errors = {}
        if user_input is not None:
            config_data = {}
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]            
            target_temp_step = user_input[CONF_TARGET_TEMP_STEP]            
            
            self._host = host
            self._port = port
            self._version = ""
            
            self._deviceinfo = self.GetDeviceInfo(self._host, self._port, DEFAULT_TIMEOUT)
            _LOGGER.debug(self._deviceinfo)

            self._mac_addr = self._deviceinfo["mac"]            
            
            self._encryption_key = self.GetDeviceKey(self._mac_addr, self._host, self._port, DEFAULT_TIMEOUT)
            _LOGGER.info('Fetched device encrytion key: %s' % str(self._encryption_key))
                   
            self._version = self.request_version()
            _LOGGER.info('Fetched device version: %s' % str(self._version))            
            
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
            config_data[CONF_VERSION] = self._version
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
                    vol.Optional(CONF_SWITCHS, default=self.config_entry.options.get(CONF_SWITCHS,[])): SelectSelector(
                            SelectSelectorConfig(
                                options=[
                                    {"value": "Lig", "label": "Panel Light"},                                    
                                    {"value": "Air", "label": "Fresh Air"},
                                    {"value": "Blo", "label": "XFan"},
                                    {"value": "Health", "label": "Health mode"},
                                    {"value": "AssHt", "label": "Aux Heat"},
                                    # {"value": "Quiet", "label": "Quiet"},
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
