# RoverControl

Brookstone **Rover 2.0** 的 Windows 电脑控制端，用 Python 3 **从零重写**。

- 逻辑以官方 Android App（包名 `com.rover3`，反编译源码见 `F:\RoverPylot\apkmod\jadx_zh15`）为准，协议逐字节一致。
- 通信层采用官方同款模型：**常驻命令读线程 + 按 op 分发 + 断线就地重连**，
  从根本上解决旧移植版「驾驶时命令通道发飘/卡死」的问题。
- 桌面端：pygame 读手柄（Xbox/GameSir XInput），OpenCV 显示 320x240 JPEG 视频。

## 目录结构

```
rover/                核心库（纯协议/通信，无 UI，可离线单测）
  protocol.py         帧构造+解析、op 常量、握手消息、RoverBlowfish  ← 对应官方 CommandEncoder.java
  connection.py       socket 管理 + 命令读线程 + 心跳 + 断线重连    ← 官方 WifiCar.java 模式
  blowfish.py         复用（已实测）
  adpcm.py            复用（已实测）
  byteutils.py        复用（已实测）
  __init__.py         高层 API：Rover / Rover20
app/
  main.py             手柄 + OpenCV 主程序
selftest.py           无硬件自检
requirements.txt
RUNNING.md            运行说明
```

## 快速开始

```powershell
cd F:\RoverControl
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe selftest.py        # 无硬件自检
# 连上车 WiFi、插手柄后：
.\.venv\Scripts\python.exe app\main.py
```

详见 `RUNNING.md`。