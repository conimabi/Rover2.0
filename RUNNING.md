# 运行说明（Windows）

## 1. 准备环境

```powershell
cd F:\RoverControl
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. 无硬件自检

```powershell
.\.venv\Scripts\python.exe selftest.py          # 协议层
.\.venv\Scripts\python.exe test_connection.py   # 连接层（假 socket）
```

## 3. 连接 Rover

1. 打开 Rover 电源。
2. **把手机 WiFi 关掉**（Rover 2.0 是单客户端 AP，手机连着会把电脑挤下线）。
3. 电脑连到 Rover 的 ad-hoc 网络，确认 `ping 192.168.1.100` 通。
4. 插好手柄（Xbox / GameSir 的 XInput 模式）。

## 4. 手柄探测（编号不符时）

```powershell
.\.venv\Scripts\python.exe joysticktest.py
```

按提示推摇杆/按按钮，记录「轴 N / 按钮 N」，改 `app\main.py` 顶部的 `BUTTON_*` 常量。

## 5. 运行

```powershell
.\.venv\Scripts\python.exe app\main.py
```

- 左/右摇杆 → 左右履带（坦克式）
- **Y** 开关灯，**B** 切红外夜视，**A/X** 升降摄像头
- OpenCV 窗口按 **ESC** 退出

## 6. 断线自愈观察

驾驶中若命令通道断开，控制台会打印：

```
command channel dropped; reconnecting...
command channel reconnected
rebuilding media channel after command reconnect
```

随后视频应自动恢复、驾驶可继续——这正是新架构相对旧移植版的核心改进。

## 常见问题

- **连不上 / TimeoutError**：确认已连上 Rover WiFi 且 `ping 192.168.1.100` 通。
- **看不到视频**：确认装了 `opencv-python`；车端媒体流是否被手机抢占。
- **手柄没反应**：跑 `joysticktest.py` 校正 `BUTTON_*`；确认手柄在 XInput 模式。
## 7. 日志

每次运行会在 `logs\` 下生成 `rover-<时间戳>.log`（同时打印到控制台），记录：
握手三步、电量变化、媒体首帧与帧率、命令通道断开/重连、媒体掉线等事件，便于实机复盘。

> 提示：用文本编辑器（UTF-8）打开；PowerShell 的 `Get-Content` 在中文系统会按 GBK
> 解码 UTF-8 而显示乱码，属正常现象。