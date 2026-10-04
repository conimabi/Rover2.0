'''
Rover 2.0 协议层（纯逻辑：帧构造/解析 + 握手消息 + Blowfish）。
逐字节对齐官方 Android App（com.rover3）的 CommandEncoder.java。

帧格式（23 字节头 + N 字节内容）:
  偏移  长度  含义
  0     3     b"MO_" 固定前缀
  3     1     通道: ord("O")=命令, ord("V")=媒体
  4     2     op (uint16 LE)
  6     9     保留(0)
  15    4     contentLen (uint32 LE)
  19    4     保留(0)
  23    N     内容
'''
import struct

from .blowfish import Blowfish

HEAD_LEN = 23
CH_CMD = 'O'
CH_MEDIA = 'V'

# ---- 命令通道 op ----
OP_LOGIN            = 0
OP_LOGIN_RESP       = 1
OP_VERIFY           = 2
OP_VERIFY_RESP      = 3
OP_VIDEO_START      = 4
OP_VIDEO_START_RESP = 5
OP_VIDEO_END        = 6
OP_VIDEO_FRAME_INT  = 7
OP_AUDIO_START      = 8
OP_AUDIO_START_RESP = 9
OP_AUDIO_END        = 10
OP_TALK_START       = 11
OP_TALK_START_RESP  = 12
OP_TALK_END         = 13
OP_CAMERA_IR        = 14    # decoder control: 摄像头升降 / 红外
OP_DRIVE            = 250   # device control: 履带 / 灯
OP_BATTERY          = 251
OP_BATTERY_RESP     = 252
OP_KEEPALIVE        = 255

# ---- 媒体通道 op ----
OP_MEDIA_VIDEO      = 1
OP_MEDIA_AUDIO      = 2


# ======================= 帧构造 / 切割 =======================

def build_frame(channel, op, content=b''):
    """构造一帧：23 字节头 + 内容。"""
    content = bytes(content)
    header = bytearray(b'MO_' + bytes([ord(channel)]) + b'\x00' * 19)
    struct.pack_into('<H', header, 4, op & 0xFFFF)
    struct.pack_into('<I', header, 15, len(content))
    return bytes(header) + content


def split_frames(buffer):
    """把累积字节流按帧切开。
    返回 (frames, remainder)，其中 frames = [(op, content_bytes), ...]。
    不完整的尾帧留到 remainder，下次 feed 再拼。"""
    frames = []
    pos = 0
    n = len(buffer)
    while n - pos >= HEAD_LEN:
        op = struct.unpack_from('<H', buffer, pos + 4)[0]
        clen = struct.unpack_from('<I', buffer, pos + 15)[0]
        total = HEAD_LEN + clen
        if n - pos < total:
            break
        frames.append((op, bytes(buffer[pos + HEAD_LEN:pos + total])))
        pos += total
    return frames, bytes(buffer[pos:])


# ======================= 命令构造 =======================

def login_request(vals=(0, 0, 0, 0)):
    return build_frame(CH_CMD, OP_LOGIN, struct.pack('<4i', *vals))

def verify_request(vals):
    return build_frame(CH_CMD, OP_VERIFY,
                       struct.pack('<4I', *[v & 0xFFFFFFFF for v in vals]))

def video_start_request():
    return build_frame(CH_CMD, OP_VIDEO_START, struct.pack('<i', 1))

def video_end_request():
    return build_frame(CH_CMD, OP_VIDEO_END, b'')

def audio_start_request():
    return build_frame(CH_CMD, OP_AUDIO_START, struct.pack('<i', 1))

def keepalive_request():
    return build_frame(CH_CMD, OP_KEEPALIVE, b'')

def battery_request():
    return build_frame(CH_CMD, OP_BATTERY, b'')

def device_control_request(key, val):
    """op=250: key=1右前/2右后/4左前/5左后/8灯开/9灯关, val=速度。"""
    return build_frame(CH_CMD, OP_DRIVE, bytes([key & 0xFF, val & 0xFF]))

def decoder_control_request(val):
    """op=14: 摄像头升降(0上/1停/2下) 与 红外(94开/95关)。"""
    return build_frame(CH_CMD, OP_CAMERA_IR, bytes([val & 0xFF]))

def media_login_request(link_bytes):
    """媒体通道登录（op=0），内容为视频启动应答里的 linkId 原始 4 字节。"""
    return build_frame(CH_MEDIA, 0, bytes(link_bytes))


# ======================= 应答解析 =======================

def parse_login_reply(content):
    """登录应答内容 -> (camera_id, L1, R1, L2, R2)。"""
    camera_id = bytes(content[2:15]).split(b'\x00', 1)[0].decode('utf-8', 'replace')  # 字段以 0 填充, 遇首个 0 截断(二代车实测)
    L1 = struct.unpack_from('<i', content, 43)[0]
    R1 = struct.unpack_from('<i', content, 47)[0]
    L2 = struct.unpack_from('<i', content, 51)[0]
    R2 = struct.unpack_from('<i', content, 55)[0]
    return camera_id, L1, R1, L2, R2

def parse_battery_reply(content):
    """电池应答内容 -> 百分比(int)，内容为空则 None。"""
    return 15 * content[0] if content else None

def parse_video_start_reply(content):
    """视频启动应答内容 -> linkId 原始 4 字节。"""
    return bytes(content[2:6])


# ======================= Blowfish =======================

def derive_key(target_id, camera_id, target_password):
    """官方密钥拼接：targetId + ':' + cameraId + '-save-private:' + targetPassword。"""
    return target_id + ':' + camera_id + '-save-private:' + target_password


class RoverBlowfish(Blowfish):
    """官方变体：P-array 初始化为全 0（而非 Pi 的小数位）。"""
    def __init__(self, key):
        self._keygen(key, [0] * 18)

# ======================= 语音对讲（上行麦克风） =======================
OP_TALK_DATA = 3   # 媒体通道(op=3): 上行麦克风音频

def talk_start_request(arg=1):
    return build_frame(CH_CMD, OP_TALK_START, bytes([arg & 0xFF]))

def talk_end_request():
    return build_frame(CH_CMD, OP_TALK_END, b'')

def talk_data_frame(ticktime, serial, timestamp, adpcm):
    """MO_V 通道 op=3: ticktime(4) serial(4) timestamp(4) format(1) length(4) + adpcm。"""
    content = (struct.pack('<iii', ticktime, serial, timestamp)
               + b'\x00'
               + struct.pack('<i', len(adpcm))
               + bytes(adpcm))
    return build_frame(CH_MEDIA, OP_TALK_DATA, content)