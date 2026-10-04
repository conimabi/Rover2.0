#!/usr/bin/env python
r'''
app/main.py - Rover 2.0 主程序：手柄/键盘驱动 + OpenCV 显示视频 + 语音对讲。

手柄（XInput/Xbox：A=0 B=1 X=2 Y=3 LB=4 RB=5）：
  左/右摇杆 履带；B 灯；X 红外；Y 监听；A 对讲；左右扳机 摄像头降/升。
键盘（全局，不受窗口焦点限制）：
  W/S 前进/后退 · A/D 左转/右转 · 上/下 摄像头升/降
  1 灯 · 2 红外 · 3 监听 · 4 对讲 · R 强制重连 · ESC 退出
两者可并用；手柄热插拔自动识别。窗口顶部为半透明状态栏（启用格子变灰、黑字）。
架构：媒体读线程只存最新帧/音频；GUI/pygame 全在主线程（Windows 必须）。
'''
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rover import Rover20
from rover.logger import Logger
from rover.audio import AudioEngine

# XInput/Xbox 布局： A=0  B=1  X=2  Y=3  LB=4  RB=5
BUTTON_TALK    = 0   # A 键：对讲
BUTTON_LIGHTS  = 1   # B 键：车灯
BUTTON_STEALTH = 2   # X 键：红外
BUTTON_LISTEN  = 3   # Y 键：监听
AXIS_CAM_UP        = 5
AXIS_CAM_DOWN      = 4
TRIGGER_THRESHOLD  = 0.5

MIN_BUTTON_LAG_SEC = 0.5
MIN_AXIS_ABSVAL    = 0.01

# Windows 虚拟键码
VK_ESC, VK_UP, VK_DOWN = 0x1B, 0x26, 0x28
VK_W, VK_A, VK_S, VK_D = 0x57, 0x41, 0x53, 0x44
VK_1, VK_2, VK_3, VK_4 = 0x31, 0x32, 0x33, 0x34
VK_R = 0x52

try:
    import ctypes
    _user32 = ctypes.windll.user32
    def _key_down(vk):
        return bool(_user32.GetAsyncKeyState(vk) & 0x8000)
except Exception:
    def _key_down(vk):
        return False

try:
    import cv2
    import numpy as np
except Exception:
    cv2 = None
    np = None

import pygame


class PS3Rover(Rover20):
    def __init__(self, **kw):
        Rover20.__init__(self, **kw)
        self.wname = 'Rover 2.0: Hit ESC to quit'
        self.quit = False
        self.latest_jpeg = None

        pygame.display.init()
        pygame.joystick.init()
        self.controller = None
        self._joy_scan_t = 0.0
        self._open_controller()
        if self.controller is None:
            self.log('WARNING: 未检测到手柄（可用键盘操控，插上手柄会自动识别）。')

        self.lights_are_on = False
        self.stealth_is_on = False
        self.listen_is_on = False
        self.talk_is_on = False
        self.camera_state = '--'
        self.last_button_time = 0.0

        # 键盘状态
        self.kb_left = self.kb_right = self.kb_cam = 0
        self._kb_prev = {}

        # HUD 帧率
        self.fps = 0.0
        self._fps_n = 0
        self._fps_t = time.time()

        self.audio = AudioEngine(self.command, self.log)

        try:
            if cv2:
                cv2.namedWindow(self.wname, cv2.WINDOW_NORMAL)
                cv2.resizeWindow(self.wname, 800, 600)
        except Exception:
            pass

    # ---------- 手柄热插拔 ----------
    def _open_controller(self):
        try:
            if pygame.joystick.get_count() <= 0:
                return
            j = pygame.joystick.Joystick(0)
            j.init()
            self.controller = j
            self.log('joystick: %s' % j.get_name())
        except Exception as e:
            self.log('joystick open failed: %r' % e)

    def _close_controller(self):
        self.controller = None

    def _ensure_controller(self):
        if self.controller is not None:
            return
        now = time.time()
        if now - self._joy_scan_t < 1.0:
            return
        self._joy_scan_t = now
        try:
            pygame.joystick.quit()
            pygame.joystick.init()
        except Exception:
            pass
        self._open_controller()

    # ---------- 媒体回调（媒体线程） ----------
    def processVideo(self, jpegbytes, timestamp_10msec):
        self.latest_jpeg = jpegbytes

    def processAudio(self, pcmsamples, timestamp_10msec):
        self.audio.on_car_audio(pcmsamples, timestamp_10msec)

    def start(self):
        Rover20.start(self)
        self.audio.start()

    def close(self):
        try:
            self.audio.close()
        except Exception:
            pass
        Rover20.close(self)

    # ---------- 主循环（主线程） ----------
    def tick(self):
        try:
            events = pygame.event.get()
        except Exception:
            events = []
        for e in events:
            et = getattr(e, 'type', None)
            if et == pygame.JOYDEVICEADDED:
                self._open_controller()
            elif et == pygame.JOYDEVICEREMOVED:
                self._close_controller()
        if self.controller is None:
            self._ensure_controller()

        gp_left = gp_right = gp_cam = 0
        if self.controller is not None:
            try:
                self.lights_are_on = self._check_button(
                    self.lights_are_on, BUTTON_LIGHTS, self.turnLightsOn, self.turnLightsOff)
                self.stealth_is_on = self._check_button(
                    self.stealth_is_on, BUTTON_STEALTH, self.turnStealthOn, self.turnStealthOff)
                self.listen_is_on = self._check_button(
                    self.listen_is_on, BUTTON_LISTEN, self.audio.start_listen, self.audio.stop_listen)
                self.talk_is_on = self._check_button(
                    self.talk_is_on, BUTTON_TALK, self.audio.start_talk, self.audio.stop_talk)
                up = self.controller.get_axis(AXIS_CAM_UP) > TRIGGER_THRESHOLD
                down = self.controller.get_axis(AXIS_CAM_DOWN) > TRIGGER_THRESHOLD
                gp_cam = 1 if up else (-1 if down else 0)
                gp_left = self._axis(1)
                gp_right = self._axis(3)
            except Exception as e:
                self.log('joystick read error, reset: %r' % e)
                self._close_controller()

        self._kb_scan()

        left = gp_left if gp_left != 0 else self.kb_left
        right = gp_right if gp_right != 0 else self.kb_right
        cam = gp_cam if gp_cam != 0 else self.kb_cam
        self.setTreads(left, right)
        self.moveCameraVertical(cam)
        self.camera_state = 'UP' if cam > 0 else ('DOWN' if cam < 0 else '--')

        if _key_down(VK_ESC):
            self.quit = True

        if cv2:
            jpeg = self.latest_jpeg
            if jpeg is not None:
                self.latest_jpeg = None
                try:
                    arr = np.frombuffer(jpeg, dtype=np.uint8)
                    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if image is not None:
                        if image.shape[1] < 640:
                            s = max(1, 640 // image.shape[1])
                            image = cv2.resize(image, None, fx=s, fy=s, interpolation=cv2.INTER_LINEAR)
                        self._draw_hud(image)
                        cv2.imshow(self.wname, image)
                        self._fps_n += 1
                except Exception:
                    pass
            now = time.time()
            if now - self._fps_t >= 1.0:
                self.fps = self._fps_n / (now - self._fps_t)
                self._fps_n = 0
                self._fps_t = now
            if cv2.waitKey(1) & 0xFF == 27:   # ESC (窗口聚焦时)
                self.quit = True

    # ---------- 键盘 ----------
    def _kb_scan(self):
        d = _key_down
        left = right = 0
        if d(VK_W):
            left += 1; right += 1
        if d(VK_S):
            left -= 1; right -= 1
        if d(VK_A):
            left -= 1; right += 1
        if d(VK_D):
            left += 1; right -= 1
        self.kb_left = max(-1, min(1, left))
        self.kb_right = max(-1, min(1, right))
        if d(VK_UP):
            self.kb_cam = 1
        elif d(VK_DOWN):
            self.kb_cam = -1
        else:
            self.kb_cam = 0
        if d(VK_R) and not self._kb_prev.get(VK_R, False):
            self.log('manual reconnect (R)')
            self.command.request_reconnect()
        self._kb_prev[VK_R] = d(VK_R)
        for vk, name in ((VK_1, 'light'), (VK_2, 'ir'), (VK_3, 'listen'), (VK_4, 'talk')):
            now = d(vk)
            if now and not self._kb_prev.get(vk, False):
                self._kb_toggle(name)
            self._kb_prev[vk] = now

    def _kb_toggle(self, name):
        if name == 'light':
            if self.lights_are_on:
                self.turnLightsOff(); self.lights_are_on = False
            else:
                self.turnLightsOn(); self.lights_are_on = True
        elif name == 'ir':
            if self.stealth_is_on:
                self.turnStealthOff(); self.stealth_is_on = False
            else:
                self.turnStealthOn(); self.stealth_is_on = True
        elif name == 'listen':
            if self.listen_is_on:
                self.audio.stop_listen(); self.listen_is_on = False
            else:
                self.audio.start_listen(); self.listen_is_on = True
        elif name == 'talk':
            if self.talk_is_on:
                self.audio.stop_talk(); self.talk_is_on = False
            else:
                self.audio.start_talk(); self.talk_is_on = True

    # ---------- HUD ----------
    def _draw_hud(self, image):
        h, w = image.shape[:2]
        bat = self.command.battery
        cells = [
            ('LIGHT',  self.lights_are_on),
            ('IR',     self.stealth_is_on),
            ('LISTEN', self.listen_is_on),
            ('TALK',   self.talk_is_on),
            ('CAM:' + self.camera_state, self.camera_state != '--'),
            ('FPS:%.0f' % self.fps, False),
            ('BAT:' + (('%d%%' % bat) if bat is not None else '--'), False),
        ]
        bar_h = max(30, h // 11)
        overlay = image[0:bar_h, :].copy()
        cv2.rectangle(overlay, (0, 0), (w, bar_h), (25, 25, 25), -1)
        n = len(cells)
        pad = 4
        cw = max(1, (w - pad * (n + 1)) // n)
        for i, (label, on) in enumerate(cells):
            x1 = pad + i * (cw + pad)
            color = (130, 130, 130) if on else (205, 205, 205)
            cv2.rectangle(overlay, (x1, 4), (x1 + cw, bar_h - 4), color, -1)
        cv2.addWeighted(overlay, 0.5, image[0:bar_h, :], 0.5, 0, image[0:bar_h, :])
        for i, (label, on) in enumerate(cells):
            x1 = pad + i * (cw + pad)
            ts = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)[0]
            tx = x1 + max(0, (cw - ts[0]) // 2)
            ty = (bar_h + ts[1]) // 2
            cv2.putText(image, label, (tx, ty),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 1, cv2.LINE_AA)

    def _axis(self, index):
        value = -self.controller.get_axis(index)
        if value > MIN_AXIS_ABSVAL:
            return 1
        if value < -MIN_AXIS_ABSVAL:
            return -1
        return 0

    def _check_button(self, flag, button_id, on_routine=None, off_routine=None):
        if self.controller.get_button(button_id):
            if time.time() - self.last_button_time > MIN_BUTTON_LAG_SEC:
                self.last_button_time = time.time()
                if flag:
                    if off_routine:
                        off_routine()
                    flag = False
                else:
                    if on_routine:
                        on_routine()
                    flag = True
        return flag


def main():
    os.makedirs('logs', exist_ok=True)
    logpath = os.path.join('logs', 'rover-%s.log' % time.strftime('%Y%m%d-%H%M%S'))
    log = Logger(logpath)
    log('=== RoverControl start; log=%s ===' % logpath)

    rover = PS3Rover(logger=log)
    try:
        rover.start()
        log('connected. 手柄或键盘均可驾驶; ESC 退出.')
        while not rover.quit:
            rover.tick()
            time.sleep(0.01)
    except KeyboardInterrupt:
        log('interrupted by user')
    except Exception as e:
        log('fatal: %r' % e)
    finally:
        rover.close()
        try:
            if cv2:
                cv2.destroyAllWindows()
        except Exception:
            pass
        log('=== RoverControl stop ===')
        log.close()


if __name__ == '__main__':
    main()