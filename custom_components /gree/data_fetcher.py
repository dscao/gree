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

from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT, ATTR_TEMPERATURE, 
    CONF_NAME, CONF_HOST, CONF_PORT, CONF_MAC, CONF_TIMEOUT, CONF_CUSTOMIZE, 
    STATE_ON, STATE_OFF, STATE_UNKNOWN, 
    TEMP_CELSIUS, PRECISION_WHOLE, PRECISION_TENTHS)

from homeassistant.helpers.event import (async_track_state_change)
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity
from configparser import ConfigParser
from Crypto.Cipher import AES
try: import simplejson
except ImportError: import json as simplejson

_LOGGER = logging.getLogger(__name__)

class DataFetcher:
    """fetch the data"""

    def __init__(self, host, port, mac_addr, timeout_s, uid, encryption_key, hass):

        self._host = host
        self._port = port
        self._mac_addr = mac_addr
        self._hass = hass
        self._timeout = timeout_s
        self._uid = uid        
        self._encryption_key = encryption_key
        #self._session_client = async_create_clientsession(hass)
        self._data = {}
        self._data["currentValues"] = []
        
        self._acOptions = { 'Pow': None, 'Mod': None, 'SetTem': None, 'WdSpd': None, 'Air': None, 'Blo': None, 'Health': None, 'SwhSlp': None, 'Lig': None, 'SwingLfRig': None, 'SwUpDn': None, 'Quiet': None, 'Tur': None, 'StHt': None, 'TemUn': None, 'HeatCoolType': None, 'TemRec': None, 'SvSt': None, 'SlpMod': None }
        self.target_temperature = None
        self.version = None


    # Pad helper method to help us get the right string for encrypting
    def Pad(self, s):
        aesBlockSize = 16
        return s + (aesBlockSize - len(s) % aesBlockSize) * chr(aesBlockSize - len(s) % aesBlockSize)            

    def FetchResult(self, cipher, ip_addr, port, timeout_s, json):
        _LOGGER.debug('Fetching(%s, %s, %s, %s)' % (ip_addr, port, timeout_s, json))
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
        _LOGGER.debug(loadedJsonPack)        
        return loadedJsonPack

    def GreeGetValues(self, propertyNames):
        jsonPayloadToSend = '{"cid":"app","i":0,"pack":"' + base64.b64encode(self.CIPHER.encrypt(self.Pad('{"cols":' + simplejson.dumps(propertyNames) + ',"mac":"' + str(self._mac_addr) + '","t":"status"}').encode("utf8"))).decode('utf-8') + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + '}'
        return self.FetchResult(self.CIPHER, self._host, self._port, self._timeout, jsonPayloadToSend)['dat']
                      
    async def getcurrentvalues(self):    
        optionsToFetch = ["Pow","Mod","SetTem","WdSpd","Air","Blo","Health","SwhSlp","Lig","SwingLfRig","SwUpDn","Quiet","Tur","StHt","TemUn","HeatCoolType","TemRec","SvSt","SlpMod","TemSen"]

        # Cipher to use to encrypt/decrypt
        self.CIPHER = AES.new(self._encryption_key, AES.MODE_ECB)        
        currentValues = self.GreeGetValues(optionsToFetch)
        self._data["currentValues"] = currentValues
        return 

    async def get_data(self):
        tasks = [            
            asyncio.create_task(self.getcurrentvalues()),
        ]
        await asyncio.gather(*tasks)
        
        _LOGGER.debug(self._data)
        return self._data
      
    def SetAcOptions(self, acOptions, newOptionsToOverride, optionValuesToOverride = None):
        #_LOGGER.debug("SetAcOptions")
        #_LOGGER.debug(acOptions)
        #_LOGGER.debug(newOptionsToOverride)
        #_LOGGER.debug(optionValuesToOverride)
        if not (optionValuesToOverride is None):
            _LOGGER.info('Setting acOptions with retrieved HVAC values')
            for key in newOptionsToOverride:
                _LOGGER.debug('Setting %s: %s' % (key, optionValuesToOverride[newOptionsToOverride.index(key)]))
                acOptions[key] = optionValuesToOverride[newOptionsToOverride.index(key)]
            _LOGGER.info('Done setting acOptions')
        else:
            _LOGGER.info('Overwriting acOptions with new settings')            
            for key, value in newOptionsToOverride.items():
                _LOGGER.debug('Overwriting %s: %s' % (key, value))
                acOptions[key] = value
            _LOGGER.info('Done overwriting acOptions')
        return acOptions
        
    def SendStateToAc(self, timeout):
        _LOGGER.info('Start sending state to HVAC')
        statePackJson = '{' + '"opt":["Pow","Mod","SetTem","WdSpd","Air","Blo","Health","SwhSlp","Lig","SwingLfRig","SwUpDn","Quiet","Tur","StHt","TemUn","HeatCoolType","TemRec","SvSt","SlpMod"],"p":[{Pow},{Mod},{SetTem},{WdSpd},{Air},{Blo},{Health},{SwhSlp},{Lig},{SwingLfRig},{SwUpDn},{Quiet},{Tur},{StHt},{TemUn},{HeatCoolType},{TemRec},{SvSt},{SlpMod}],"t":"cmd"'.format(**self._acOptions) + '}'
        sentJsonPayload = '{"cid":"app","i":0,"pack":"' + base64.b64encode(self.CIPHER.encrypt(self.Pad(statePackJson).encode("utf8"))).decode('utf-8') + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + '}'
        # Setup UDP Client & start transfering
        clientSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        clientSock.settimeout(timeout)
        clientSock.sendto(bytes(sentJsonPayload, "utf-8"), (self._host, self._port))
        data, addr = clientSock.recvfrom(64000)
        receivedJson = simplejson.loads(data)
        clientSock.close()
        pack = receivedJson['pack']
        base64decodedPack = base64.b64decode(pack)
        decryptedPack = self.CIPHER.decrypt(base64decodedPack)
        decodedPack = decryptedPack.decode("utf-8")
        replacedPack = decodedPack.replace('\x0f', '').replace(decodedPack[decodedPack.rindex('}')+1:], '')
        receivedJsonPayload = simplejson.loads(replacedPack)
        _LOGGER.info('Done sending state to HVAC: ' + str(receivedJsonPayload))
    
    async def SyncState(self, acOptions = {}):
        #Fetch current settings from HVAC
        _LOGGER.debug('Starting SyncState')

        optionsToFetch = ["Pow","Mod","SetTem","WdSpd","Air","Blo","Health","SwhSlp","Lig","SwingLfRig","SwUpDn","Quiet","Tur","StHt","TemUn","HeatCoolType","TemRec","SvSt","SlpMod"]
        await self.getcurrentvalues()
        
        currentValues = self._data["currentValues"]

        # Set latest status from device
        self._acOptions = self.SetAcOptions(self._acOptions, optionsToFetch, currentValues)

        # Overwrite status with our choices
        if not(acOptions == {}):
            self._acOptions = self.SetAcOptions(self._acOptions, acOptions)

        # Initialize the receivedJsonPayload variable (for return)
        receivedJsonPayload = ''

        self.SendStateToAc(self._timeout)

        _LOGGER.debug('Finished SyncState')
        return receivedJsonPayload

class GetDataError(Exception):
    """request error or response data is unexpected"""


