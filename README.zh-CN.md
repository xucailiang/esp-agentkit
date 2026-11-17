# ESP Agent Kit

[English](README.md) · 简体中文

ESP Agent Kit 让硬件开发者用自己选的桌面 Agent 在 ESP32 上开发 MicroPython 应用。你给出接线和期望行为，Agent 写程序并通过 Wi-Fi 部署。你拿着实物试用，Agent 结合板端读数和你看到的现象改下一版。应用和驱动保存在开发板上，设计目标是拔掉电脑、重新供电后，设备仍能自行工作。

工具以可移植的 Agent Skill 分发，目录里包含板型资料、操作指南和 Python CLI。让你的 Agent 读取这份指南即可开始。能运行本地命令的 Agent 也可以直接调用 CLI。[Skill 使用指南](skill/esp-agentkit/SKILL.md)

## 一次实物迭代

> 人体红外传感器的 OUT 接 GPIO27，高电平表示检测到移动。有源蜂鸣器的控制脚接 GPIO26，高电平鸣响。上电先保持安静。传感器就绪后，每次从低变高响一秒，持续为高时只响一次。

假设你在传感器前持续走动，却听到好几声。Agent 可以请你再走一次，同时采样 GPIO27，确认传感器持续输出高电平，还是在这段时间给出了多个脉冲。确认原因后，Agent 修改应用并无线部署，你在同一块板上重试。独立供电后也符合预期，就把这一版的源码、依赖、接线和实测结果一起留在设备项目里。下次想改成“十秒内触发两次才响”，换一个 Agent 也能从这版接手。

## 当前进度

USB 首配、无线调试、应用部署和恢复流程已有实现。桌面联调使用 Unix 版 MicroPython，验证了 WebREPL 通信以及包含一个依赖的应用启动。ESP32 实板刷写、无线重连、断电恢复和传感器示例仍待上板验证。[架构与分阶段开发计划](docs/architecture-and-development.md)

## 快速开始

首款参考板为经典 **ESP32-DevKitC V4**，搭载 ESP32-WROOM-32E 模组和 4 MiB Flash。准备一根 Micro-B USB **数据线**，以及电脑可以直连开发板的可信局域网。开发板连接 2.4 GHz Wi-Fi，WebREPL 使用明文传输。

### 1. 准备桌面环境

安装 Git 和 Python 3.12 后，在 macOS 或 Linux 终端运行以下命令。还没拿到开发板也可以完成这一步。

```sh
git clone https://github.com/xucailiang/esp-agentkit.git
cd esp-agentkit/skill/esp-agentkit
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/agentkit.py --help
```

### 2. 通过 USB 首次配置

插上板子后，按 USB 指南识别串口、核对板型和镜像摘要，再给新板烧入官方 MicroPython v1.29.0 的 `ESP32_GENERIC` OTA 变体。首刷命令会擦除板上已有内容。官方参考板使用 CP2102N，兼容板也可能使用 CH340/CH341。如果电脑已识别 USB 设备但没有串口，再按实际芯片排查驱动。[USB 首刷步骤和串口排查](skill/esp-agentkit/SKILL.md#find-the-boards-serial-port) · [参考板与固件资料](skill/esp-agentkit/boards/esp32-devkitc-v4.md)

回到 Skill 目录，将 `PORT` 换成实际串口，运行首配命令。

```sh
python scripts/provision.py --port PORT
```

按提示在本地终端输入 Wi-Fi 凭据和单独的 WebREPL 密码。脚本确认电脑能通过 Wi-Fi 登录后，将 JSON 输出中的 `address` 和 `device_id` 保存到设备项目，供以后连接同一块板使用。

### 3. 通过 Wi-Fi 部署第一个应用

把 `BOARD_IP` 和 `DEVICE_ID` 换成首配记录里的值。随附示例只需要开发板。

```sh
python scripts/agentkit.py info --address BOARD_IP
python scripts/agentkit.py deploy --address BOARD_IP --device-id DEVICE_ID examples/startup
```

示例在启动时导入一个模块并打印可用堆内存。用 115200 波特率打开串口监视器，按一下板上的 EN 键，预期会看到 `Application started. Free heap:` 和字节数。换成自己的应用时，将 `examples/startup` 换为包含 `app.py` 及其依赖的目录即可。[示例源码](skill/esp-agentkit/examples/startup/app.py)

## CLI 命令

| 命令 | 用途 |
| --- | --- |
| `info` | 读取设备标识、固件版本、部署清单标识和本次开机捕获的异常。 |
| `run FILE` | 运行一个短 Python 探针，返回标准输出和板端异常堆栈。 |
| `stop` | 中断前台程序并返回交互提示符。板子下次重启时仍会运行已部署的应用。 |
| `put FILE` | 上传单个文件到试验目录。正式部署时把需要的文件放入本地应用目录。 |
| `deploy DIR` | 安装完整应用、重启开发板，并检查 Wi-Fi 管理连接是否恢复。 |

五个命令都会中断正在运行的前台应用。测量蜂鸣器脉宽时，让程序连续运行。只想检查连通性时，可以使用独立的登录检查。部署中途断线后，重新连接同一设备，再次部署完整应用。[仅验证无线登录](skill/esp-agentkit/SKILL.md#retry-a-lan-login) · [部署恢复](skill/esp-agentkit/SKILL.md#recover-an-application)

## 参与贡献

目前尤其欢迎参考板的实测反馈。提交 Issue 时请写明底板和模组型号、固件版本、执行的命令、期望行为和实际现象。分享日志或截图前请先移除凭据。[提交 Issue](https://github.com/xucailiang/esp-agentkit/issues)

## 许可证

本项目自身的许可证待定。固件与桌面依赖各自采用上游许可证。[第三方软件与许可证](docs/third-party.md)
