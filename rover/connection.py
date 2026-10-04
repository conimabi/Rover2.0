'''
连接层：命令通道 + 媒体通道（无 UI 依赖）。

复刻官方 Android App（WifiCar.java）的模型，并加**看门狗**以应对"哑死"连接：
命令通道用电池应答(op=252, 每 5s)当心跳；媒体通道用视频帧当心跳；超时即强制断开重连。
（半开 TCP 上 recv 永不报错，只能靠"多久没收到数据"来判断。）
'''
import socket
import struct
import threading
import time

from . import protocol as p
from .adpcm import decodeADPCMToPCM


class CommandChannel:
    def __init__(self, host, port, target_id, target_password, logger=print):
        self.host = host
        self.port = port
        self.target_id = target_id
        self.target_password = target_password
        self.log = logger

        self.sock = None
        self.rx = b''
        self.battery = None
        self._last_battery = None
        self.is_active = False
        self.last_link_bytes = None
        self.on_reconnect = None
        self.on_before_reconnect = None

        self._reconnecting = False
        self._send_lock = threading.Lock()
        self._reader = None
        self._keepalive_timer = None
        self._battery_timer = None
        self._watchdog = None
        self._last_rx_time = time.time()
        self.alive_timeout = 8.0     # 电池应答每 5s 一次, 超 8s 没收到视为半开

        self.connect_timeout = 5.0
        self.handshake_timeout = 5.0
        self.keepalive_period = 30.0
        self.battery_period = 5.0

    # ---------------- 建立 ----------------
    def connect(self):
        self.log('command channel: connecting to %s:%d' % (self.host, self.port))
        self.sock = self._new_socket()
        link_bytes = self._handshake(self.sock)
        self.last_link_bytes = link_bytes
        self.is_active = True
        self._last_rx_time = time.time()
        self._reader = _CommandReader(self)
        self._reader.start()
        self._start_keepalive()
        self._start_battery()
        # 说明: 车端握手后不在命令通道回任何数据(电池/keepalive 均无应答),
        # 故不能靠"收不到应答"判活; 命令通道存活由媒体流心跳(media watchdog)间接保障。
        return link_bytes

    def _new_socket(self, retries=8):
        last = None
        for _ in range(retries):
            try:
                return socket.create_connection((self.host, self.port), self.connect_timeout)
            except OSError as e:
                last = e
                time.sleep(0.7)
        raise last

    def _recv_exact(self, sock, n, timeout):
        sock.settimeout(timeout)
        buf = b''
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError('connection closed during handshake')
            buf += chunk
        return buf

    def _handshake(self, sock):
        self._send_raw(sock, p.login_request())
        reply = self._recv_exact(sock, 82, self.handshake_timeout)
        frames, _ = p.split_frames(reply)
        _, content = frames[0]
        camera_id, L1, R1, L2, R2 = p.parse_login_reply(content)
        self.log('handshake: login ok (camera=%s)' % camera_id)
        bf = p.RoverBlowfish(p.derive_key(self.target_id, camera_id, self.target_password))
        L1, R1 = bf.encrypt(L1, R1)
        L2, R2 = bf.encrypt(L2, R2)
        self._send_raw(sock, p.verify_request([L1, R1, L2, R2]))
        self._recv_exact(sock, 26, self.handshake_timeout)
        self.log('handshake: verify ok')
        self._send_raw(sock, p.video_start_request())
        reply = self._recv_exact(sock, 29, self.handshake_timeout)
        frames, _ = p.split_frames(reply)
        _, content = frames[0]
        link_bytes = p.parse_video_start_reply(content)
        self.log('handshake: video-start ok (link=%s)' % link_bytes.hex())
        sock.settimeout(None)
        return link_bytes

    # ---------------- 发送 ----------------
    def _send_raw(self, sock, frame):
        sock.sendall(frame)

    def send(self, frame):
        with self._send_lock:
            if not self.sock:
                return
            try:
                self.sock.sendall(frame)
            except OSError:
                pass

    # ---------------- 读线程回调 ----------------
    def _dispatch(self):
        frames, self.rx = p.split_frames(self.rx)
        for op, content in frames:
            if op == p.OP_BATTERY_RESP:
                self.battery = p.parse_battery_reply(content)
                if self.battery != self._last_battery:
                    self.log('battery: %s%%' % self.battery)
                    self._last_battery = self.battery

    def _watchdog_loop(self):
        while self.is_active:
            time.sleep(2.0)
            if not self.is_active:
                break
            if self._reconnecting:
                continue
            if time.time() - self._last_rx_time > self.alive_timeout:
                self.log('command watchdog: no reply for %.0fs; forcing reconnect' % self.alive_timeout)
                try:
                    self.sock.close()      # 让读线程 recv 出错 -> 触发重连
                except Exception:
                    pass

    def _reconnect(self):
        if self._reconnecting:
            return
        self._reconnecting = True
        try:
            self.log('command channel dropped; reconnecting...')
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass
            if self.on_before_reconnect:
                try:
                    self.on_before_reconnect()
                except Exception as e:
                    self.log('on_before_reconnect error: %r' % e)
            time.sleep(2.0)   # 给车端释放旧会话的时间(绿灯复位)
            self.sock = self._new_socket()
            link_bytes = self._handshake(self.sock)
            self.rx = b''
            self._last_rx_time = time.time()
            self.last_link_bytes = link_bytes
            self.log('command channel reconnected')
            if self.on_reconnect:
                try:
                    self.on_reconnect(link_bytes)
                except Exception as e:
                    self.log('on_reconnect error: %r' % e)
        except Exception as e:
            self.log('command reconnect failed: %r' % e)
            time.sleep(1.0)
        finally:
            self._reconnecting = False

    # ---------------- 心跳 ----------------
    def _start_keepalive(self):
        self.send(p.keepalive_request())
        self._keepalive_timer = threading.Timer(self.keepalive_period, self._start_keepalive)
        self._keepalive_timer.daemon = True
        self._keepalive_timer.start()

    def _start_battery(self):
        self.send(p.battery_request())
        self._battery_timer = threading.Timer(self.battery_period, self._start_battery)
        self._battery_timer.daemon = True
        self._battery_timer.start()

    # ---------------- 关闭 ----------------
    def close(self):
        self.is_active = False
        for t in (self._keepalive_timer, self._battery_timer):
            if t:
                t.cancel()
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


class MediaChannel:
    def __init__(self, host, port, logger=print):
        self.host = host
        self.port = port
        self.log = logger
        self.sock = None
        self.rx = b''
        self.is_active = False
        self.on_video = None
        self.on_audio = None
        self.on_dropped = None
        self._reader = None
        self._watchdog = None
        self.connect_timeout = 5.0
        self._last_rx_time = time.time()
        self.alive_timeout = 4.0     # 超过 4s 没有视频帧视为媒体哑死

        self._frames = 0
        self._last_stats = time.time()
        self._first_frame_logged = False

    def connect(self, link_bytes):
        self.log('media channel: connecting to %s:%d' % (self.host, self.port))
        self.sock = self._new_socket()
        self.sock.sendall(p.media_login_request(link_bytes))
        self.sock.settimeout(None)
        self.is_active = True
        self._last_rx_time = time.time()
        self._reader = _MediaReader(self)
        self._reader.start()
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True, name='MediaWatchdog')
        self._watchdog.start()

    def _new_socket(self, retries=8):
        last = None
        for _ in range(retries):
            try:
                return socket.create_connection((self.host, self.port), self.connect_timeout)
            except OSError as e:
                last = e
                time.sleep(0.7)
        raise last

    def _watchdog_loop(self):
        while self.is_active:
            time.sleep(1.0)
            if not self.is_active:
                break
            if time.time() - self._last_rx_time > self.alive_timeout:
                self.log('media watchdog: no frame for %.0fs; resetting' % self.alive_timeout)
                try:
                    self.sock.close()
                except Exception:
                    pass
                break

    def _dispatch(self):
        while True:
            k = self.rx.find(b'MO_V')
            if k < 0:
                if len(self.rx) > 3:
                    self.rx = self.rx[-3:]
                return
            if k > 0:
                self.rx = self.rx[k:]
            if len(self.rx) < p.HEAD_LEN:
                return
            clen = struct.unpack_from('<I', self.rx, 15)[0]
            total = p.HEAD_LEN + clen
            if len(self.rx) < total:
                return
            op = struct.unpack_from('<H', self.rx, 4)[0]
            content = self.rx[p.HEAD_LEN:total]
            self.rx = self.rx[total:]
            ts = struct.unpack_from('<I', content, 0)[0]
            if op == p.OP_MEDIA_VIDEO:
                if not self._first_frame_logged:
                    self.log('media: first video frame received')
                    self._first_frame_logged = True
                self._frames += 1
                now = time.time()
                if now - self._last_stats >= 5.0:
                    self.log('media: %.1f fps' % (self._frames / (now - self._last_stats)))
                    self._frames = 0
                    self._last_stats = now
                if self.on_video:
                    self.on_video(content[13:], ts)
            elif op == p.OP_MEDIA_AUDIO:
                self._handle_audio(content, ts)

    def _handle_audio(self, content, ts):
        audsize = struct.unpack_from('<I', content, 13)[0]
        data = content[17:17 + audsize]
        pre = struct.unpack_from('<h', content, 17 + audsize)[0]
        index = content[17 + audsize + 2]
        pcm = decodeADPCMToPCM(data, pre, index)
        if self.on_audio:
            self.on_audio(pcm, ts)

    def close(self):
        self.is_active = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


class _CommandReader(threading.Thread):
    def __init__(self, ch):
        threading.Thread.__init__(self, name='CommandReader')
        self.daemon = True
        self.ch = ch

    def run(self):
        ch = self.ch
        while ch.is_active:
            if ch._reconnecting:
                time.sleep(0.05)
                continue
            try:
                buf = ch.sock.recv(1024)
            except OSError:
                if ch.is_active:
                    ch._reconnect()
                    continue
                break
            if not buf:
                if ch.is_active:
                    ch._reconnect()
                    continue
                break
            ch._last_rx_time = time.time()
            ch.rx += buf
            ch._dispatch()


class _MediaReader(threading.Thread):
    def __init__(self, m):
        threading.Thread.__init__(self, name='MediaReader')
        self.daemon = True
        self.m = m

    def run(self):
        m = self.m
        while m.is_active:
            try:
                buf = m.sock.recv(8192)
            except OSError:
                if m.is_active and m.on_dropped:
                    m.on_dropped()
                break
            if not buf:
                if m.is_active and m.on_dropped:
                    m.on_dropped()
                break
            m._last_rx_time = time.time()
            m.rx += buf
            m._dispatch()