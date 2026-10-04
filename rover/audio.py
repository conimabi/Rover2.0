'''
音频：车端麦克风 -> 电脑喇叭（监听，Y 键）；电脑麦克风 -> 车喇叭（对讲，A 键）。
依赖 sounddevice（缺失则自动降级为静默，不报错）。8kHz 单声道 16-bit，与车端一致。
'''
import queue
import threading
import time

try:
    import sounddevice as sd
    import numpy as np
    _SD = True
except Exception:
    sd = None
    np = None
    _SD = False

SAMPLE_RATE = 8000


class AudioEngine(object):
    def __init__(self, command_channel, logger=print):
        self.command = command_channel
        self.log = logger
        self.ok = _SD
        self.is_active = False
        self.listen_on = False    # Y 键: 车端麦 -> 电脑喇叭
        self.talk_on = False      # A 键: 电脑麦 -> 车喇叭
        self._q = queue.Queue(maxsize=128)
        self._play_thread = None
        self._talk_thread = None
        if not _SD:
            self.log('audio: sounddevice/numpy 不可用，语音对讲已禁用（pip install sounddevice）')

    # ---------- 播放线程（车 -> 电脑喇叭） ----------
    def start(self):
        if not _SD:
            return
        self.is_active = True
        self._play_thread = threading.Thread(target=self._play_loop, daemon=True, name='AudioPlay')
        self._play_thread.start()
        self.log('audio: 就绪（Y=监听车麦, A=对讲）')

    def on_car_audio(self, pcm_samples, ts):
        '''MediaChannel 读线程调用：车端 PCM 入队播放。pcm_samples 为 int 列表。'''
        if not (self.is_active and self.listen_on):
            return
        try:
            self._q.put_nowait(np.asarray(pcm_samples, dtype=np.int16).tobytes())
        except Exception:
            pass

    def _drain(self):
        try:
            while True:
                self._q.get_nowait()
        except Exception:
            pass

    def _play_loop(self):
        try:
            stream = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16')
            stream.start()
        except Exception as e:
            self.log('audio: 打开输出设备失败 %r' % e)
            return
        while self.is_active:
            try:
                pcm = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if not pcm:
                continue
            try:
                stream.write(pcm)
            except Exception:
                pass
        try:
            stream.stop(); stream.close()
        except Exception:
            pass

    # ---------- 监听：车 -> 电脑喇叭（Y 键） ----------
    def start_listen(self):
        self.listen_on = True
        self._drain()
        self.log('audio: 监听 开（车端麦克风 -> 电脑喇叭）')

    def stop_listen(self):
        self.listen_on = False
        self._drain()
        self.log('audio: 监听 关')

    # ---------- 对讲：电脑麦 -> 车喇叭（A 键） ----------
    def start_talk(self):
        if not _SD or self.talk_on:
            return
        from . import protocol as p
        try:
            self.command.send(p.talk_start_request(1))
        except Exception as e:
            self.log('talk start send failed: %r' % e)
        self.talk_on = True
        self._talk_thread = threading.Thread(target=self._talk_loop, daemon=True, name='AudioTalk')
        self._talk_thread.start()
        self.log('audio: 对讲 开（电脑麦克风 -> 车喇叭）')

    def stop_talk(self):
        if not self.talk_on:
            return
        self.talk_on = False
        from . import protocol as p
        try:
            self.command.send(p.talk_end_request())
        except Exception:
            pass
        self.log('audio: 对讲 关')

    def _talk_loop(self):
        from . import protocol as p
        from .adpcm import encode_adpcm
        sample = 0
        index = 0
        serial = 0
        ticktime = 0
        try:
            stream = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16', blocksize=320)
            stream.start()
        except Exception as e:
            self.log('audio: 打开输入设备失败 %r' % e)
            self.talk_on = False
            return
        try:
            while self.talk_on and self.is_active:
                data, _ = stream.read(320)
                if len(data) < 640:
                    continue
                adpcm, sample, index = encode_adpcm(bytes(data), sample, index)
                self.command.send(p.talk_data_frame(ticktime, serial, int(time.time()), adpcm))
                ticktime += 40
                serial += 1
        except Exception as e:
            self.log('audio: 对讲线程异常 %r' % e)
        finally:
            try:
                stream.stop(); stream.close()
            except Exception:
                pass

    def close(self):
        self.is_active = False
        if self.talk_on:
            self.stop_talk()
        try:
            self._q.put_nowait(b'')
        except Exception:
            pass