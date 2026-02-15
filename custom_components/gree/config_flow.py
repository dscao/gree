"""Config flow for gree ac integration."""

from __future__ import annotations

import logging
import re
import voluptuous as vol
import socket
import base64
import asyncio

from collections import OrderedDict
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_MAC
from homeassistant.helpers.selector import (
    SelectSelector, 
    SelectSelectorConfig, 
    SelectSelectorMode,
    EntitySelector, 
    EntitySelectorConfig
)
from Crypto.Cipher import AES
try: 
    import simplejson as json
except ImportError: 
    import json

from .const import (
    DOMAIN, 
    CONF_SWITCHS, 
    CONF_TARGET_TEMP_STEP, 
    CONF_TEMP_SENSOR, 
    CONF_HUM_SENSOR,
    CONF_UPDATE_INTERVAL, 
    CONF_ENCRYPTION_KEY, 
    CONF_UID,
    CONF_VERSION,
    CONF_ENCRYPTION_VERSION,
)

from .data_fetcher import (
    FetchResult, Pad, EncryptGCM, GetGCMCipher
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 7000
DEFAULT_TIMEOUT = 5 # 缩短超时时间，避免用户等待过久
DEFAULT_TARGET_TEMP_STEP = 1
GENERIC_GREE_DEVICE_KEY = "a3K8Bx%2r8Y7#xDh"
GENERIC_GREE_DEVICE_KEY_GCM = b'{yxAHAY_Lm6pbC/<'

@config_entries.HANDLERS.register(DOMAIN)
class FlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """handle config flow for this integration"""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlow(config_entry)
        
    def __init__(self):
        """Initialize."""
        self._errors = {}

    def _get_device_info_sync(self, host, port):
        """同步方法：获取设备MAC (运行在线程池中)"""
        _LOGGER.info('Get Mac')
        cipher = AES.new(GENERIC_GREE_DEVICE_KEY.encode("utf8"), AES.MODE_ECB)
        jsonPayloadToSend = '{"t": "scan"}'
        # 复用 data_fetcher.FetchResult
        return FetchResult(cipher, host, port, DEFAULT_TIMEOUT, jsonPayloadToSend, encryption_version=1)

    def _get_device_key_sync(self, mac_addr, host, port):
        """同步方法：获取设备Key (运行在线程池中)"""
        _LOGGER.info('Retrieving HVAC encryption key (V1)')
        cipher = AES.new(GENERIC_GREE_DEVICE_KEY.encode("utf8"), AES.MODE_ECB)
        # 复用 data_fetcher.Pad
        pack = base64.b64encode(cipher.encrypt(Pad('{"mac":"' + str(mac_addr) + '","t":"bind","uid":0}').encode("utf8"))).decode('utf-8')
        jsonPayloadToSend = '{"cid": "app","i": 1,"pack": "' + pack + '","t":"pack","tcid":"' + str(mac_addr) + '","uid": 0}'
        
        return FetchResult(cipher, host, port, DEFAULT_TIMEOUT, jsonPayloadToSend, encryption_version=1)['key']

    def _get_device_key_gcm_sync(self, mac_addr, host, port):
        """同步方法：获取设备Key V2 (运行在线程池中)"""
        _LOGGER.info('Retrieving HVAC encryption key (V2)')
        plaintext = '{"cid":"' + str(mac_addr) + '", "mac":"' + str(mac_addr) + '","t":"bind","uid":0}'
        pack, tag = EncryptGCM(GENERIC_GREE_DEVICE_KEY_GCM, plaintext)
        jsonPayloadToSend = '{"cid": "app","i": 1,"pack": "' + pack + '","t":"pack","tcid":"' + str(mac_addr) + '","uid": 0, "tag" : "' + tag + '"}'
        cipher = GetGCMCipher(GENERIC_GREE_DEVICE_KEY_GCM)
        return FetchResult(cipher, host, port, DEFAULT_TIMEOUT, jsonPayloadToSend, encryption_version=2)['key']

    def _request_version_sync(self, host, port, mac, key, encryption_version):
        """同步方法：获取版本号，用于验证连接 (运行在线程池中)"""
        _LOGGER.info("Verifying connection and getting version...")
        prop_names = ["hid", "TemSen"]
        
        plaintext = '{"cols":' + json.dumps(prop_names) + ',"mac":"' + str(mac) + '","t":"status"}'
        
        if encryption_version == 1:
            cipher = AES.new(key.encode("utf8"), AES.MODE_ECB)
            pack = base64.b64encode(cipher.encrypt(Pad(plaintext).encode("utf8"))).decode('utf-8')
            payload = '{"cid":"app","i":0,"pack":"' + pack + '","t":"pack","tcid":"' + str(mac) + '","uid":0}'
        else:
            pack, tag = EncryptGCM(key.encode("utf8"), plaintext)
            payload = '{"cid":"app","i":0,"pack":"' + pack + '","t":"pack","tcid":"' + str(mac) + '","uid":0,"tag":"' + tag + '"}'
            cipher = GetGCMCipher(key.encode("utf8"))

        ret = FetchResult(cipher, host, port, DEFAULT_TIMEOUT, payload, encryption_version)
        
        # 解析版本号
        hid = ret.get("dat")[0]
        version = "0"
        if hid:
            match = re.search(r"(?<=V)([\d.]+)\.bin$", hid)
            version = match and match.group(1)
        
        return version

    async def _validate_and_connect(self, host, port):
        """
        验证连接并获取设备信息的异步包装器
        """
        try:
            # 1. 获取设备信息 (MAC) 
            device_info = await self.hass.async_add_executor_job(
                self._get_device_info_sync, host, port
            )
            mac_addr = device_info.get("mac")
            if not mac_addr:
                raise Exception("Could not find mac address in response")
            
            # 2. 尝试获取 Key (V1 或 V2)
            encryption_version = 1
            key = None
            try:
                key = await self.hass.async_add_executor_job(
                    self._get_device_key_sync, mac_addr, host, port
                )
            except Exception:
                encryption_version = 2
                key = await self.hass.async_add_executor_job(
                    self._get_device_key_gcm_sync, mac_addr, host, port
                )
            
            if not key:
                raise Exception("Cannot retrieve key")

            # 3. 获取版本号 (验证 Key 是否有效)
            version = await self.hass.async_add_executor_job(
                self._request_version_sync, host, port, mac_addr, key, encryption_version
            )

            return {
                "mac": mac_addr,
                "key": key,
                "version": version,
                "encryption_version": encryption_version
            }

        except Exception as e:
            _LOGGER.error("Connection failed: %s", e)
            raise e

    async def async_step_user(self, user_input=None):
        self._errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            target_temp_step = user_input[CONF_TARGET_TEMP_STEP]

            try:
                info = await self._validate_and_connect(host, port)
                
                await self.async_set_unique_id(f"climate.gree-{info['mac']}")
                self._abort_if_unique_id_configured()

                config_data = {
                    CONF_HOST: host,
                    CONF_PORT: port,
                    CONF_MAC: info['mac'],
                    CONF_TARGET_TEMP_STEP: target_temp_step,
                    CONF_ENCRYPTION_KEY: str(info['key']),
                    CONF_ENCRYPTION_VERSION: info['encryption_version'],
                    CONF_VERSION: info['version']
                }
                return self.async_create_entry(title=f"gree-{host}", data=config_data)

            except Exception:
                self._errors["base"] = "cannot_connect"

        return await self._show_config_form(user_input)

    async def _show_config_form(self, user_input):
        data_schema = OrderedDict()
        data_schema[vol.Required(CONF_HOST, default="192.168.1.x")] = str
        data_schema[vol.Required(CONF_PORT, default=DEFAULT_PORT)] = int
        data_schema[vol.Required(CONF_TARGET_TEMP_STEP, default=DEFAULT_TARGET_TEMP_STEP)] = vol.All(vol.Coerce(float), vol.In([0.5, 1]))
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(data_schema), errors=self._errors
        )


class OptionsFlow(config_entries.OptionsFlow):
    """Config flow options for gree."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        return await self.async_step_user(user_input)

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_temp_sensor = self._config_entry.options.get(CONF_TEMP_SENSOR, self._config_entry.data.get(CONF_TEMP_SENSOR))
        if current_temp_sensor == "None": current_temp_sensor = None

        current_hum_sensor = self._config_entry.options.get(CONF_HUM_SENSOR, self._config_entry.data.get(CONF_HUM_SENSOR))
        if current_hum_sensor == "None": current_hum_sensor = None
        
        current_update_interval = self._config_entry.options.get(CONF_UPDATE_INTERVAL, 5)
        current_enc_ver = self._config_entry.options.get(CONF_ENCRYPTION_VERSION, self._config_entry.data.get(CONF_ENCRYPTION_VERSION, 1))
        current_enc_key = self._config_entry.options.get(CONF_ENCRYPTION_KEY, self._config_entry.data.get(CONF_ENCRYPTION_KEY))
        current_uid = self._config_entry.options.get(CONF_UID, 0)
        current_switches = self._config_entry.options.get(CONF_SWITCHS, [])

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TEMP_SENSOR,
                        description={"suggested_value": current_temp_sensor}
                    ): EntitySelector(
                        EntitySelectorConfig(
                            domain="sensor",
                            device_class="temperature",
                            multiple=False
                        )
                    ),
                    vol.Optional(
                        CONF_HUM_SENSOR,
                        description={"suggested_value": current_hum_sensor}
                    ): EntitySelector(
                        EntitySelectorConfig(
                            domain="sensor",
                            device_class="humidity",
                            multiple=False
                        )
                    ),
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=current_update_interval,
                    ): vol.All(vol.Coerce(int), vol.Range(min=2, max=60)),
                    vol.Optional(
                        CONF_ENCRYPTION_VERSION,
                        default=current_enc_ver
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=2)),
                    vol.Optional(
                        CONF_ENCRYPTION_KEY,
                        default=current_enc_key
                    ): vol.All(vol.Coerce(str)),
                    vol.Optional(CONF_SWITCHS, default=current_switches): SelectSelector(
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
                                multiple=True,
                                translation_key="switchs"
                            )
                        ),
                    vol.Optional(
                        CONF_UID,
                        default=current_uid
                    ): vol.All(vol.Coerce(int)),
                }
            ),
        )