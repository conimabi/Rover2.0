#!/usr/bin/env python
'''joysticktest.py - 探测手柄的轴/按钮编号，用于校正 app/main.py 顶部常量。'''
import time
import pygame

pygame.init()
pygame.joystick.init()
n = pygame.joystick.get_count()
print('detected joysticks:', n)
if n == 0:
    raise SystemExit('no joystick found')
j = pygame.joystick.Joystick(0)
j.init()
print('name:', j.get_name(), '| axes:', j.get_numaxes(), '| buttons:', j.get_numbuttons())
print('press buttons / move sticks (Ctrl-C to quit):')
try:
    while True:
        pygame.event.pump()
        axes = [round(j.get_axis(i), 2) for i in range(j.get_numaxes())]
        btns = [i for i in range(j.get_numbuttons()) if j.get_button(i)]
        print('axes=%s  buttons=%s' % (axes, btns), end='\r')
        time.sleep(0.05)
except KeyboardInterrupt:
    pass