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
    CONF_HUM_SENSOR,
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
    CONF_ENCRYPTION_VERSION,
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

    def FetchResult(self, cipher, host, port, timeout, json):
        _LOGGER.info('Fetching(%s, %s, %s, %s)' % (host, port, timeout, json))
        clientSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        clientSock.settimeout(timeout)
        clientSock.sendto(bytes(json, "utf-8"), (host, port))
        data, addr = clientSock.recvfrom(64000)
        receivedJson = simplejson.loads(data)
        clientSock.close()
        pack = receivedJson['pack']
        base64decodedPack = base64.b64decode(pack)
        decryptedPack = cipher.decrypt(base64decodedPack)
        if self._encryption_version == 2:
            tag = receivedJson['tag']
            cipher.verify(base64.b64decode(tag))
        decodedPack = decryptedPack.decode("utf-8")
        replacedPack = decodedPack.replace('\x0f', '').replace(decodedPack[decodedPack.rindex('}')+1:], '')
        loadedJsonPack = simplejson.loads(replacedPack)
        return loadedJsonPack

    def GetDeviceKey(self, mac_addr, host, port, timeout):
        _LOGGER.info('Retrieving HVAC encryption key')
        cipher = AES.new(GENERIC_GREE_DEVICE_KEY.encode("utf8"), AES.MODE_ECB)
        pack = base64.b64encode(cipher.encrypt(self.Pad('{"mac":"' + str(mac_addr) + '","t":"bind","uid":0}').encode("utf8"))).decode('utf-8')
        jsonPayloadToSend = '{"cid": "app","i": 1,"pack": "' + pack + '","t":"pack","tcid":"' + str(mac_addr) + '","uid": 0}'
        return self.FetchResult(cipher, host, port, timeout, jsonPayloadToSend)['key']
        
        
    def GetDeviceInfo(self, host, port, timeout):
        _LOGGER.info('Get Mac')
        cipher = AES.new(GENERIC_GREE_DEVICE_KEY.encode("utf8"), AES.MODE_ECB)
        jsonPayloadToSend = '{"t": "scan"}'
        return self.FetchResult(cipher, host, port, timeout, jsonPayloadToSend)
        
    def GreeGetValues(self, propertyNames):
        self.CIPHER = AES.new(self._encryption_key.encode("utf8"), AES.MODE_ECB) 
        jsonPayloadToSend = '{"cid":"app","i":0,"pack":"' + base64.b64encode(self.CIPHER.encrypt(self.Pad('{"cols":' + simplejson.dumps(propertyNames) + ',"mac":"' + str(self._mac_addr) + '","t":"status"}').encode("utf8"))).decode('utf-8') + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(0) + '}'
        return self.FetchResult(self.CIPHER, self._host, self._port, DEFAULT_TIMEOUT, jsonPayloadToSend)
        plaintext = '{"cols":' + simplejson.dumps(propertyNames) + ',"mac":"' + str(self._mac_addr) + '","t":"status"}'
        if self._encryption_version == 1:
            cipher = self.CIPHER
            jsonPayloadToSend = '{"cid":"app","i":0,"pack":"' + base64.b64encode(cipher.encrypt(self.Pad(plaintext).encode("utf8"))).decode('utf-8') + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + '}'
        elif self._encryption_version == 2:
            pack, tag = self.EncryptGCM(self._encryption_key.encode("utf8"), plaintext)
            jsonPayloadToSend = '{"cid":"app","i":0,"pack":"' + pack + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + ',"tag" : "' + tag + '"}'
            cipher = self.GetGCMCipher(self._encryption_key.encode("utf8"))
        return self.FetchResult(cipher, self._host, self._port, self._timeout, jsonPayloadToSend)['dat']
         
    def GetGCMCipher(self, key):
        cipher = AES.new(key, AES.MODE_GCM, nonce=GCM_IV)
        cipher.update(GCM_ADD)
        return cipher

    def EncryptGCM(self, key, plaintext):
        encrypted_data, tag = self.GetGCMCipher(key).encrypt_and_digest(plaintext.encode("utf8"))
        pack = base64.b64encode(encrypted_data).decode('utf-8')
        tag = base64.b64encode(tag).decode('utf-8')
        return (pack, tag)

    def GetDeviceKeyGCM(self, mac_addr, host, port, timeout):
        _LOGGER.info('Retrieving HVAC encryption key')
        GENERIC_GREE_DEVICE_KEY = b'{yxAHAY_Lm6pbC/<'
        plaintext = '{"cid":"' + str(mac_addr) + '", "mac":"' + str(self._mac_addr) + '","t":"bind","uid":0}'
        pack, tag = self.EncryptGCM(GENERIC_GREE_DEVICE_KEY, plaintext)
        jsonPayloadToSend = '{"cid": "app","i": 1,"pack": "' + pack + '","t":"pack","tcid":"' + str(mac_addr) + '","uid": 0, "tag" : "' + tag + '"}'
        return self.FetchResult(self.GetGCMCipher(GENERIC_GREE_DEVICE_KEY), host, port, timeout, jsonPayloadToSend)['key']


            
    def request_version(self):
        """Request the firmware version from the device."""
        ret = self.GreeGetValues(["hid","TemSen"])
        _LOGGER.debug(ret)
        hid = ret.get("dat")[0]
        
        if hid:
            match = re.search(r"(?<=V)([\d.]+)\.bin$", hid)
            version = match and match.group(1)
            _LOGGER.debug("version: %s", version)

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
            self._encryption_version = 1
            self._version = ""
            
            self._deviceinfo = self.GetDeviceInfo(self._host, self._port, DEFAULT_TIMEOUT)
            
            _LOGGER.debug(self._deviceinfo)

            self._mac_addr = self._deviceinfo["mac"]

            try:
                self._encryption_key = self.GetDeviceKey(self._mac_addr, self._host, self._port, DEFAULT_TIMEOUT)
                _LOGGER.info('Fetched device encrytion key: %s' % str(self._encryption_key))
            except:
                self._encryption_version = 2
                self._encryption_key = self.GetDeviceKeyGCM(self._mac_addr, self._host, self._port, DEFAULT_TIMEOUT)    
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
            config_data[CONF_ENCRYPTION_VERSION] = self._encryption_version
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
        self._config_entry = config_entry

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
                        default=self._config_entry.options.get(CONF_TEMP_SENSOR, self._config_entry.data.get(CONF_TEMP_SENSOR, "None"))
                    ): vol.All(vol.Coerce(str)),
                    vol.Optional(
                        CONF_HUM_SENSOR,
                        default=self._config_entry.options.get(CONF_HUM_SENSOR, self._config_entry.data.get(CONF_HUM_SENSOR, "None"))
                    ): vol.All(vol.Coerce(str)),
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=self._config_entry.options.get(CONF_UPDATE_INTERVAL, 5),
                    ): vol.All(vol.Coerce(int), vol.Range(min=2, max=60)),
                    vol.Optional(
                        CONF_ENCRYPTION_VERSION,
                        default=self._config_entry.options.get(CONF_ENCRYPTION_VERSION, self._config_entry.data.get(CONF_ENCRYPTION_VERSION, 1))
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=2)),
                    vol.Optional(
                        CONF_ENCRYPTION_KEY,
                        default=self._config_entry.options.get(CONF_ENCRYPTION_KEY, self._config_entry.data.get(CONF_ENCRYPTION_KEY))
                    ): vol.All(vol.Coerce(str)),
                    vol.Optional(CONF_SWITCHS, default=self._config_entry.options.get(CONF_SWITCHS,[])): SelectSelector(
                            SelectSelectorConfig(
                                options=[
                                    {"value": "Lig", "label": "Panel Light"},                                    
                                    {"value": "Air", "label": "Fresh Air"},
                                    {"value": "Blo", "label": "XFan"},
                                    {"value": "Health", "label": "Health mode"},
                                    {"value": "AssHt", "label": "Aux Heat"},
                                    {"value": "Buzzer_ON_OFF", "label": "Buzzer"},
                                    {"value": "AntiDirectBlow", "label": "Anti Direct Blow"},
                                    {"value": "LigSen", "label": "Panel Auto Light"},
                                ], 
                                multiple=True,translation_key=CONF_SWITCHS
                            )
                        ),
                    vol.Optional(
                        CONF_UID,
                        default=self._config_entry.options.get(CONF_UID, 0)
                    ): vol.All(vol.Coerce(int)),
                }
            ),
        )
