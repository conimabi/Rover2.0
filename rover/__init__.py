'''
Rover 2.0 高层控制 API。

组合 protocol.py（协议）+ connection.py（连接）。
- 媒体流是可靠心跳: media watchdog 超时 -> 触发**整链重连**(命令+媒体)。
- 每次重连后**复位执行器状态**, 保证能重新下发当前指令(否则"车不动")。
'''
import threading
import time

from . import protocol as p
from .connection import CommandChannel, MediaChannel

HOST = '192.168.1.100'
PORT = 80
TARGET_ID = 'AC13'
TARGET_PASSWORD = 'AC13'

DRIVE_RIGHT_FWD  = 1
DRIVE_RIGHT_BACK = 2
DRIVE_LEFT_FWD   = 4
DRIVE_LEFT_BACK  = 5
DRIVE_LEFT_STOP  = 3
DRIVE_RIGHT_STOP = 0
LIGHTS_ON        = 8
LIGHTS_OFF       = 9

CAM_UP   = 0
CAM_STOP = 1
CAM_DOWN = 2
IR_ON    = 94
IR_OFF   = 95


class Rover:
    def __init__(self, host=HOST, port=PORT, target_id=TARGET_ID,
                 target_password=TARGET_PASSWORD, logger=print):
        self.host = host
        self.port = port
        self.log = logger
        self.command = CommandChannel(host, port, target_id, target_password, logger)
        self.media = MediaChannel(host, port, logger)
        self.media.on_video = self.processVideo
        self.media.on_audio = self.processAudio
        self.media.on_dropped = self._on_media_dropped
        self.command.on_before_reconnect = self._before_command_reconnect
        self.command.on_reconnect = self._on_command_reconnect
        self._reconnect_lock = threading.Lock()
        self.tread_delay = 1.0
        self.is_active = False

    # ---------------- 生命周期 ----------------
    def start(self):
        link_bytes = self.command.connect()
        self.media.connect(link_bytes)
        try:
            self.command.send(p.audio_start_request())
        except Exception:
            pass
        self.is_active = True

    def close(self):
        self.is_active = False
        for ch in (self.command, self.media):
            try:
                ch.close()
            except Exception:
                pass

    # ---------------- 媒体哑死 -> 整链重连 ----------------
    def _on_media_dropped(self):
        threading.Thread(target=self._full_reconnect, daemon=True).start()

    def _full_reconnect(self):
        if not self.command.is_active:
            return
        with self._reconnect_lock:
            time.sleep(0.5)
            self.log('media dropped; full reconnect')
            self.command._reconnect()      # 重握手命令通道, 并经 on_reconnect 重建媒体

    # ---------------- 命令通道重连 -> 复位执行器 + 重建媒体 ----------------
    def _on_command_reconnect(self, link_bytes):
        self._reset_actuators()
        self.log('rebuilding media channel after command reconnect')
        self._open_media(link_bytes)

    def _open_media(self, link_bytes):
        old = self.media
        if old:
            old.on_dropped = None
            try:
                old.close()
            except Exception:
                pass
        m = MediaChannel(self.host, self.port, self.log)
        m.on_video = self.processVideo
        m.on_audio = self.processAudio
        m.on_dropped = self._on_media_dropped
        try:
            m.connect(link_bytes)
            self.media = m
            self.log('media channel rebuilt')
        except Exception as e:
            self.log('media rebuild failed: %r' % e)

    def _before_command_reconnect(self):
        self.log('closing media before command reconnect')
        try:
            if self.media:
                self.media.on_dropped = None
                self.media.close()
        except Exception:
            pass
        self.media = None

    def _reset_actuators(self):
        '''重连后清空"上次已发"状态, 触发重新下发当前指令。子类覆盖。'''
        pass

    # ---------------- 发送辅助 ----------------
    def _drive(self, key, speed):
        self.command.send(p.device_control_request(key, int(speed) & 0xFF))

    def _camera(self, cmd):
        self.command.send(p.decoder_control_request(cmd))

    # ---------------- 媒体回调（默认空；子类覆盖） ----------------
    def processVideo(self, jpegbytes, timestamp_10msec):
        pass

    def processAudio(self, pcmsamples, timestamp_10msec):
        pass

    def getBatteryPercentage(self):
        self.command.send(p.battery_request())
        time.sleep(0.05)
        return self.command.battery


class Rover20(Rover):
    def __init__(self, *args, **kwargs):
        Rover.__init__(self, *args, **kwargs)
        self.leftTread = _Tread(self, DRIVE_LEFT_FWD, DRIVE_LEFT_BACK, DRIVE_LEFT_STOP)
        self.rightTread = _Tread(self, DRIVE_RIGHT_FWD, DRIVE_RIGHT_BACK, DRIVE_RIGHT_STOP)
        self.cameraVertical = _Camera(self, CAM_STOP)

    def _reset_actuators(self):
        self.leftTread._last_cmd = None
        self.rightTread._last_cmd = None
        self.cameraVertical.is_moving = False

    def setTreads(self, left, right):
        self.leftTread.update(left)
        self.rightTread.update(right)

    def turnLightsOn(self):
        self.command.send(p.device_control_request(LIGHTS_ON, 0))

    def turnLightsOff(self):
        self.command.send(p.device_control_request(LIGHTS_OFF, 0))

    def moveCameraVertical(self, where):
        self.cameraVertical.move(where)

    def turnStealthOn(self):
        self.command.send(p.decoder_control_request(IR_ON))

    def turnStealthOff(self):
        self.command.send(p.decoder_control_request(IR_OFF))


class _Tread(object):
    def __init__(self, rover, fwd, back, stop_key):
        self.rover = rover
        self.fwd = fwd
        self.back = back
        self.stop_key = stop_key
        self._last_cmd = None

    def update(self, value):
        r = self.rover
        if value == 0:
            cmd = (self.stop_key, 0)
        else:
            wheel = self.fwd if value > 0 else self.back
            cmd = (wheel, int(round(abs(value) * 10)))
        if cmd != self._last_cmd:
            r._drive(cmd[0], cmd[1])
            self._last_cmd = cmd


class _Camera(object):
    def __init__(self, rover, stopcmd):
        self.rover = rover
        self.stopcmd = stopcmd
        self.is_moving = False

    def move(self, where):
        if where == 0:
            if self.is_moving:
                self.rover._camera(self.stopcmd)
                self.is_moving = False
        elif not self.is_moving:
            cmd = self.stopcmd - 1 if where == 1 else self.stopcmd + 1
            self.rover._camera(cmd)
            self.is_moving = True