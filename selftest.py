#!/usr/bin/env python
'''
selftest.py - 无硬件自检：不接车也能验证协议层是否正确。
校验：帧构造/切割、Blowfish 往返、ADPCM 解码、字节工具、握手消息、应答解析。
运行：python selftest.py
'''
import struct

from rover.protocol import (
    build_frame, split_frames, login_request, verify_request,
    video_start_request, device_control_request, media_login_request,
    parse_login_reply, parse_battery_reply, parse_video_start_reply,
    RoverBlowfish, derive_key, HEAD_LEN,
    OP_LOGIN, OP_DRIVE, OP_BATTERY_RESP,
)
from rover.blowfish import Blowfish
from rover.adpcm import decodeADPCMToPCM
from rover.byteutils import bytes_to_int, bytes_to_uint, bytes_to_short


def check(name, cond):
    print('%-46s %s' % (name, 'OK' if cond else 'FAIL'))
    if not cond:
        raise SystemExit('FAILED: ' + name)


# 1) 帧头字面量
f = build_frame('O', 4, b'\x01')
check('frame prefix == b"MO_O"', f[0:4] == b'MO_O')
check('frame len == 23 + 1', len(f) == 24)
check('op at [4:6] LE == 4', struct.unpack_from('<H', f, 4)[0] == 4)
check('contentLen at [15:19] == 1', struct.unpack_from('<I', f, 15)[0] == 1)
check('payload == b"\\x01"', f[HEAD_LEN:] == b'\x01')

# 2) split_frames 往返
blob = login_request((0, 0, 0, 0)) + device_control_request(1, 10) + build_frame('O', 255, b'')
frames, rem = split_frames(blob)
check('split_frames count == 3', len(frames) == 3)
check('split_frames remainder empty', rem == b'')
check('frame0 op == LOGIN', frames[0][0] == OP_LOGIN)
check('frame0 content == 16B zeros', frames[0][1] == b'\x00' * 16)
check('frame1 op == DRIVE', frames[1][0] == OP_DRIVE)
check('frame1 content == [1,10]', frames[1][1] == bytes([1, 10]))

# 3) 不完整帧滞留 + 重拼
partial = build_frame('O', OP_BATTERY_RESP, b'\x2a')
frames, rem = split_frames(partial[:-2])
check('partial frame -> no frames', frames == [])
check('partial frame -> kept in remainder', rem == partial[:-2])
frames, rem = split_frames(rem + partial[-2:])
check('reassembled frame ok', len(frames) == 1 and frames[0][0] == OP_BATTERY_RESP)

# 4) Blowfish 标准往返
bf = Blowfish('my secret key')
eL, eR = bf.encrypt(0x12345678, 0x9ABCDEF0)
dL, dR = bf.decrypt(eL, eR)
check('Blowfish enc/dec round-trip', (dL, dR) == (0x12345678, 0x9ABCDEF0))

# 5) Rover 变体（0 P-array）
rbf = RoverBlowfish(derive_key('AC13', 'camera', 'password'))
e = rbf.encrypt(0x01020304, 0x05060708)
check('RoverBlowfish -> uint32 pair', all(0 <= v <= 0xFFFFFFFF for v in e))

# 6) ADPCM 解码
samples = decodeADPCMToPCM(bytes([0x1F, 0x62, 0x85, 0x40]), 0, 0)
check('ADPCM decode length == 8', len(samples) == 8)
check('ADPCM samples in +/-2^15', all(-32768 <= s <= 32767 for s in samples))

# 7) 字节工具
data = struct.pack('<iIh', -2, 0x01020304, -3)
check('bytes_to_int', bytes_to_int(data, 0) == -2)
check('bytes_to_uint', bytes_to_uint(data, 4) == 0x01020304)
check('bytes_to_short', bytes_to_short(data, 8) == -3)

# 8) 登录应答解析（合成 82 字节应答）
reply = bytearray(82)
reply[23:25] = struct.pack('<h', 0)       # result = 0
reply[25:37] = b'00E04C0C90CA'            # camera id (12B)
struct.pack_into('<i', reply, 66, 11)
struct.pack_into('<i', reply, 70, 22)
struct.pack_into('<i', reply, 74, 33)
struct.pack_into('<i', reply, 78, 44)
cid, L1, R1, L2, R2 = parse_login_reply(bytes(reply[23:]))
check('parse_login_reply camera id', cid == '00E04C0C90CA')
check('parse_login_reply L1/R1/L2/R2', (L1, R1, L2, R2) == (11, 22, 33, 44))

# 9) 电池应答解析
check('parse_battery_reply 0x0a -> 150', parse_battery_reply(b'\x0a') == 150)

# 10) 视频启动应答 -> linkId 4 字节
vsr = bytes([0, 0]) + b'\x01\x02\x03\x04' + b'\x00\x00'
check('parse_video_start_reply link', parse_video_start_reply(vsr) == b'\x01\x02\x03\x04')

# 11) 媒体登录帧走 "V" 通道
mf = media_login_request(b'\x01\x02\x03\x04')
check('media login header == b"MO_V"', mf[0:4] == b'MO_V')

# 12) 登录应答里 cameraId 以 0 填充 -> 解析为空串（二代车实测）
reply0 = bytearray(59)
reply0[0:2] = struct.pack('<h', 0)
cid0, _a, _b, _c, _d = parse_login_reply(bytes(reply0))
check('null-padded cameraId -> empty', cid0 == '')

print('\nAll checks passed.')