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
        
        self._acOptions = { 'Pow': None, 'Mod': None, 'SetTem': None, 'WdSpd': None, 'Air': None, 'Blo': None, 'Health': None, 'SwhSlp': None, 'Lig': None, 'SwingLfRig': None, 'SwUpDn': None, 'Quiet': None, 'Tur': None, 'StHt': None, 'TemUn': None, 'HeatCoolType': None, 'TemRec': None, 'SvSt': None, 'SlpMod': None, 'AssHt': None }
        self.target_temperature = None

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
        #receivedJson = {"t":"pack","i":0,"uid":0,"cid":"c8f74218ed83","tcid":"0938e7dcc288","pack":"83yAN+n1y4d4oK0UkhTgC57c69aHR0JubLB8ZN91NJXxtDWCHQYE0vnaBfC/4LzBSNJXFjCJjGgdwK9dWJaEaInY5VGCUfdW/4Rq6u2ERiM="}
        
        pack = receivedJson['pack']
        base64decodedPack = base64.b64decode(pack)
        decryptedPack = cipher.decrypt(base64decodedPack)
        decodedPack = decryptedPack.decode("utf-8")
        replacedPack = decodedPack.replace('\x0f', '').replace(decodedPack[decodedPack.rindex('}')+1:], '')
        loadedJsonPack = simplejson.loads(replacedPack)
        _LOGGER.debug('FetchResult')
        _LOGGER.debug(loadedJsonPack)
        _LOGGER.debug('FetchResult')
        return loadedJsonPack

    def GreeGetValues(self, propertyNames):
        jsonPayloadToSend = '{"cid":"app","i":0,"pack":"' + base64.b64encode(self.CIPHER.encrypt(self.Pad('{"cols":' + simplejson.dumps(propertyNames) + ',"mac":"' + str(self._mac_addr) + '","t":"status"}').encode("utf8"))).decode('utf-8') + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + '}'
        return self.FetchResult(self.CIPHER, self._host, self._port, self._timeout, jsonPayloadToSend)['dat']
                      
    async def getcurrentvalues(self):    
        optionsToFetch = ["Pow","Mod","SetTem","WdSpd","Air","Blo","Health","SwhSlp","Lig","SwingLfRig","SwUpDn","Quiet","Tur","StHt","TemUn","HeatCoolType","TemRec","SvSt","SlpMod","AssHt","TemSen"]

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
      

    def SendCommandToAc(self, jsondata, timeout):
        _LOGGER.info('Start sending state to HVAC')
        opt = "["
        p = "["
        for key, value in jsondata.items():
            _LOGGER.debug('command %s: %s' % (key, value))
            opt += "\"" + key + "\","
            p += str(value) + ","
        opt = opt[:-1] + "]"
        p = p[:-1] + "]"
        statePackJson = '{' + '"mac":"' + str(self._mac_addr) + '","opt":' + opt + ',"p":' + p + ',"t":"cmd"' + '}'
        _LOGGER.debug(statePackJson)
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
        optionsToFetch = ["Pow","Mod","SetTem","WdSpd","Air","Blo","Health","SwhSlp","Lig","SwingLfRig","SwUpDn","Quiet","Tur","StHt","TemUn","HeatCoolType","TemRec","SvSt","SlpMod","AssHt"]
        await self.getcurrentvalues()        
        currentValues = self._data["currentValues"]       
        receivedJsonPayload = ''
        self.SendCommandToAc(acOptions, self._timeout)
        _LOGGER.debug('Finished SyncState')
        return receivedJsonPayload

class GetDataError(Exception):
    """request error or response data is unexpected"""


