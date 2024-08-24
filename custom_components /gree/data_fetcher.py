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

    def __init__(self, host, port, mac_addr, timeout, uid, encryption_version, encryption_key, hass):

        self._hass = hass
        self._host = host
        self._port = port
        self._mac_addr = mac_addr
        self._timeout = timeout
        self._uid = uid
        self.encryption_version = encryption_version
        self.CIPHER = None
        
        
        self._data = {}
        self._data["currentValues"] = []
        if encryption_key:
            _LOGGER.info('Using configured encryption key: {}'.format(encryption_key))
            self._encryption_key = encryption_key
            if encryption_version == 1:
                # Cipher to use to encrypt/decrypt
                self.CIPHER = AES.new(self._encryption_key, AES.MODE_ECB)
            elif encryption_version != 2:
                _LOGGER.error('Encryption version %s is not implemented.' % encryption_version)
        else:
            self._encryption_key = None

        
        
        self._acOptions = { 'Pow': None, 'Mod': None, 'SetTem': None, 'WdSpd': None, 'Air': None, 'Blo': None, 'Health': None, 'SwhSlp': None, 'Lig': None, 'SwingLfRig': None, 'SwUpDn': None, 'Quiet': None, 'Tur': None, 'StHt': None, 'TemUn': None, 'HeatCoolType': None, 'TemRec': None, 'SvSt': None, 'SlpMod': None, 'AssHt': None }
        self.target_temperature = None

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
        if self.encryption_version == 2:
            tag = receivedJson['tag']
            cipher.verify(base64.b64decode(tag))
        decodedPack = decryptedPack.decode("utf-8")
        replacedPack = decodedPack.replace('\x0f', '').replace(decodedPack[decodedPack.rindex('}')+1:], '')
        loadedJsonPack = simplejson.loads(replacedPack)
        _LOGGER.debug('FetchResult')
        _LOGGER.debug(loadedJsonPack)
        _LOGGER.debug('FetchResult')
        return loadedJsonPack


    def GreeGetValues(self, propertyNames):
        plaintext = '{"cols":' + simplejson.dumps(propertyNames) + ',"mac":"' + str(self._mac_addr) + '","t":"status"}'
        if self.encryption_version == 1:
            cipher = self.CIPHER
            jsonPayloadToSend = '{"cid":"app","i":0,"pack":"' + base64.b64encode(cipher.encrypt(self.Pad(plaintext).encode("utf8"))).decode('utf-8') + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + '}'
        elif self.encryption_version == 2:
            pack, tag = self.EncryptGCM(self._encryption_key, plaintext)
            jsonPayloadToSend = '{"cid":"app","i":0,"pack":"' + pack + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + ',"tag" : "' + tag + '"}'
            cipher = self.GetGCMCipher(self._encryption_key)
        return self.FetchResult(cipher, self._host, self._port, self._timeout, jsonPayloadToSend)['dat']

        
    async def getcurrentvalues(self):    
        optionsToFetch = ["Pow","Mod","SetTem","WdSpd","Air","Blo","Health","SwhSlp","Lig","SwingLfRig","SwUpDn","Quiet","Tur","StHt","TemUn","HeatCoolType","TemRec","SvSt","SlpMod","AssHt","TemSen"]
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
        
    def GetGCMCipher(self, key):
        cipher = AES.new(key, AES.MODE_GCM, nonce=GCM_IV)
        cipher.update(GCM_ADD)
        return cipher

    def EncryptGCM(self, key, plaintext):
        encrypted_data, tag = self.GetGCMCipher(key).encrypt_and_digest(plaintext.encode("utf8"))
        pack = base64.b64encode(encrypted_data).decode('utf-8')
        tag = base64.b64encode(tag).decode('utf-8')
        return (pack, tag)    

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
        if self.encryption_version == 1:
            cipher = self.CIPHER
            sentJsonPayload = '{"cid":"app","i":0,"pack":"' + base64.b64encode(cipher.encrypt(self.Pad(statePackJson).encode("utf8"))).decode('utf-8') + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + '}'
        elif self.encryption_version == 2:
            pack, tag = self.EncryptGCM(self._encryption_key, statePackJson)
            sentJsonPayload = '{"cid":"app","i":0,"pack":"' + pack + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + ',"tag":"' + tag +'"}'
            cipher = self.GetGCMCipher(self._encryption_key)
        receivedJsonPayload = self.FetchResult(cipher, self._host, self._port, timeout, sentJsonPayload)
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


