#!/usr/bin/env python
'''
test_connection.py - 无硬件测试连接层：用假 socket 验证握手字节流与应答分发。
运行：python test_connection.py
'''
import struct

from rover import protocol as p
from rover.connection import CommandChannel


class FakeSock(object):
    def __init__(self, script):
        self.script = list(script)     # 依次返回的字节块
        self.sent = b''
        self.closed = False
    def settimeout(self, t):
        pass
    def sendall(self, data):
        self.sent += data
    def recv(self, n):
        if not self.script:
            return b''
        return self.script.pop(0)[:n]
    def close(self):
        self.closed = True


def login_reply():
    """构造 82 字节登录应答：content 59 字节 = result(2) + cameraId(12) + ... + challenge(43..)。"""
    content = bytearray(59)
    content[0:2] = struct.pack('<h', 0)         # result = 0
    content[2:14] = b'00E04C0C90CA'             # cameraId
    struct.pack_into('<i', content, 43, 11)     # == reply[66]
    struct.pack_into('<i', content, 47, 22)     # == reply[70]
    struct.pack_into('<i', content, 51, 33)     # == reply[74]
    struct.pack_into('<i', content, 55, 44)     # == reply[78]
    return p.build_frame('O', p.OP_LOGIN_RESP, bytes(content))


def reply(op, content):
    return p.build_frame('O', op, content)


def check(name, cond):
    print('%-44s %s' % (name, 'OK' if cond else 'FAIL'))
    if not cond:
        raise SystemExit('FAILED: ' + name)


silent = lambda *a: None

# ---- 握手：login(82) -> verify(26) -> video-start(29) ----
ch = CommandChannel('h', 1, 'AC13', 'pw', logger=silent)
sock = FakeSock([
    login_reply(),
    reply(p.OP_VERIFY_RESP, b'\x00\x00\x00'),                            # 26B
    reply(p.OP_VIDEO_START_RESP, b'\x00\x00' + b'\x01\x02\x03\x04'),     # 29B
])
link = ch._handshake(sock)
check('handshake returns linkId', link == b'\x01\x02\x03\x04')

frames, _ = p.split_frames(sock.sent)
check('handshake sent 3 frames', len(frames) == 3)
check('frame#1 == LOGIN', frames[0][0] == p.OP_LOGIN)
check('frame#2 == VERIFY', frames[1][0] == p.OP_VERIFY)
check('frame#3 == VIDEO_START', frames[2][0] == p.OP_VIDEO_START)
check('login content 16B', frames[0][1] == b'\x00' * 16)
check('verify content 16B', len(frames[1][1]) == 16)

# ---- 分发：电池应答 + 无关 op ----
ch2 = CommandChannel('h', 1, 'AC13', 'pw', logger=silent)
ch2.rx = (reply(p.OP_BATTERY_RESP, b'\x0a')     # 10 * 15 = 150
          + reply(p.OP_KEEPALIVE, b'')
          + reply(p.OP_BATTERY_RESP, b'\x14'))   # 20 * 15 = 300
ch2._dispatch()
check('dispatch sets battery to last', ch2.battery == 300)
check('dispatch drains rx buffer', ch2.rx == b'')

# ---- 部分帧滞留 ----
whole = reply(p.OP_BATTERY_RESP, b'\x0c')
ch3 = CommandChannel('h', 1, 'AC13', 'pw', logger=silent)
ch3.rx = whole[:-1]
ch3._dispatch()
check('partial reply not consumed', ch3.rx == whole[:-1])
ch3.rx += whole[-1:]
ch3._dispatch()
check('completed reply consumed', ch3.battery == 180 and ch3.rx == b'')

print('\nAll connection checks passed.')