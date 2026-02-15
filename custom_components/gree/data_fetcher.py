import socket
import base64
import logging
from Crypto.Cipher import AES
try: 
    import simplejson as json
except ImportError: 
    import json

_LOGGER = logging.getLogger(__name__)

# --- 常量定义 ---
GCM_IV = b'123456789012'
GCM_ADD = b'12345678'

# --- 通用工具函数 (全局) ---

def Pad(s):
    """AES Pad helper"""
    aesBlockSize = 16
    return s + (aesBlockSize - len(s) % aesBlockSize) * chr(aesBlockSize - len(s) % aesBlockSize)

def GetGCMCipher(key):
    """Get GCM Cipher"""
    cipher = AES.new(key, AES.MODE_GCM, nonce=GCM_IV)
    cipher.update(GCM_ADD)
    return cipher

def EncryptGCM(key, plaintext):
    """Encrypt using GCM"""
    cipher = GetGCMCipher(key)
    encrypted_data, tag = cipher.encrypt_and_digest(plaintext.encode("utf8"))
    pack = base64.b64encode(encrypted_data).decode('utf-8')
    tag = base64.b64encode(tag).decode('utf-8')
    return (pack, tag)

def FetchResult(cipher, host, port, timeout, json_str, encryption_version=1):
    """发送请求并接收解密结果 (同步阻塞IO)"""
    _LOGGER.debug('Fetching(%s, %s, %s, %s)' % (host, port, timeout, json_str))
    clientSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    clientSock.settimeout(timeout)
    try:
        clientSock.sendto(bytes(json_str, "utf-8"), (host, port))
        data, addr = clientSock.recvfrom(64000)
    finally:
        clientSock.close()

    receivedJson = json.loads(data)
    pack = receivedJson['pack']
    base64decodedPack = base64.b64decode(pack)
    decryptedPack = cipher.decrypt(base64decodedPack)
    
    if encryption_version == 2:
        tag = receivedJson['tag']
        cipher.verify(base64.b64decode(tag))
        
    decodedPack = decryptedPack.decode("utf-8")
    replacedPack = decodedPack.replace('\x0f', '').replace(decodedPack[decodedPack.rindex('}')+1:], '')
    loadedJsonPack = json.loads(replacedPack)
    return loadedJsonPack


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
        self._datarefreshtimes = 0
        
        if encryption_key:
            _LOGGER.debug('Using configured encryption key: {}'.format(encryption_key))
            self._encryption_key = encryption_key.encode('utf-8') if isinstance(encryption_key, str) else encryption_key
            if encryption_version == 1:
                self.CIPHER = AES.new(self._encryption_key, AES.MODE_ECB)
            elif encryption_version != 2:
                _LOGGER.error('Encryption version %s is not implemented.' % encryption_version)
        else:
            self._encryption_key = None

        self._acOptions = { 'Pow': None, 'Mod': None, 'SetTem': None, 'WdSpd': None, 'Air': None, 'Blo': None, 'Health': None, 'SwhSlp': None, 'Lig': None, 'SwingLfRig': None, 'SwUpDn': None, 'Quiet': None, 'Tur': None, 'StHt': None, 'TemUn': None, 'HeatCoolType': None, 'TemRec': None, 'SvSt': None, 'SlpMod': None, 'AssHt': None, 'Buzzer_ON_OFF': None, 'AntiDirectBlow': None, 'LigSen': None }
        self.target_temperature = None

    def GreeGetValues(self, propertyNames):
        """构造查询包并发送 (同步方法)"""
        plaintext = '{"cols":' + json.dumps(propertyNames) + ',"mac":"' + str(self._mac_addr) + '","t":"status"}'
        if self.encryption_version == 1:
            cipher = self.CIPHER
            pack = base64.b64encode(cipher.encrypt(Pad(plaintext).encode("utf8"))).decode('utf-8')
            jsonPayloadToSend = '{"cid":"app","i":0,"pack":"' + pack + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + '}'
        elif self.encryption_version == 2:
            pack, tag = EncryptGCM(self._encryption_key, plaintext)
            jsonPayloadToSend = '{"cid":"app","i":0,"pack":"' + pack + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + ',"tag" : "' + tag + '"}'
            cipher = GetGCMCipher(self._encryption_key)
        return FetchResult(cipher, self._host, self._port, self._timeout, jsonPayloadToSend, self.encryption_version)['dat']

        
    async def getcurrentvalues(self): 
        """异步获取当前状态"""   
        optionsToFetch = ["Pow","Mod","SetTem","WdSpd","Air","Blo","Health","SwhSlp","Lig","SwingLfRig","SwUpDn","Quiet","Tur","StHt","TemUn","HeatCoolType","TemRec","SvSt","SlpMod","AssHt","TemSen","Buzzer_ON_OFF","AntiDirectBlow","LigSen"]
        currentValues = None
        try:
            # 关键：将阻塞操作放入后台线程池
            currentValues = await self._hass.async_add_executor_job(
                self.GreeGetValues, optionsToFetch
            )
        except Exception as e:
            _LOGGER.error('Fetch data Error: %s', e)
            
        if currentValues or self._datarefreshtimes > 2:
            self._data["currentValues"] = currentValues
            self._datarefreshtimes = 0
        else:
            self._datarefreshtimes = self._datarefreshtimes + 1
        return

    async def get_data(self):
        """DataUpdateCoordinator 调用的入口"""
        await self.getcurrentvalues()
        _LOGGER.debug(self._data)
        return self._data

    def SendCommandToAc(self, jsondata, timeout):
        """发送控制命令 (同步方法)"""
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
            pack = base64.b64encode(cipher.encrypt(Pad(statePackJson).encode("utf8"))).decode('utf-8')
            sentJsonPayload = '{"cid":"app","i":0,"pack":"' + pack + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + '}'
        elif self.encryption_version == 2:
            pack, tag = EncryptGCM(self._encryption_key, statePackJson)
            sentJsonPayload = '{"cid":"app","i":0,"pack":"' + pack + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + ',"tag":"' + tag +'"}'
            cipher = GetGCMCipher(self._encryption_key)
        receivedJsonPayload = FetchResult(cipher, self._host, self._port, timeout, sentJsonPayload, self.encryption_version)
        _LOGGER.info('Done sending state to HVAC: ' + str(receivedJsonPayload))
    
    async def SyncState(self, acOptions = {}):
        """异步发送控制命令"""
        #Fetch current settings from HVAC
        _LOGGER.debug('Starting SyncState')
        await self.getcurrentvalues()        
        currentValues = self._data["currentValues"]
        receivedJsonPayload = ''
        try:
            await self._hass.async_add_executor_job(
                self.SendCommandToAc, acOptions, self._timeout
            )
        except Exception as e:
            _LOGGER.error("SyncState Error: %s", e)
        _LOGGER.debug('Finished SyncState')
        return receivedJsonPayload