<h1><img src="docs/assets/esp-agent-kit-logo.webp" width="360" alt="ESP Agent Kit"></h1>

[English](README.md) · 简体中文

用你选的桌面 Agent 在 ESP32 上开发 MicroPython 应用。你接线、试实物，Agent 读引脚、改代码、通过 Wi-Fi 部署下一版。应用留在板上，重新上电即可运行。

![开发者用手触发 ESP32 上的移动传感器，试用蜂鸣器，桌面电脑通过 Wi-Fi 更新程序。](docs/assets/workbench.webp)

> “我一直在动，蜂鸣器就一直响。让它只响一次，等传感器恢复低电平后才能再次触发。”

把接线和 [Skill 指南](skill/esp-agentkit/SKILL.md) 交给 Agent。验收通过后，把源码、接线和实测结果留在设备项目里，下次换一个 Agent 也能接着改。

**当前进度**　无线调试和应用部署已在 Unix 版 MicroPython 上联调。USB 首刷、Wi-Fi 恢复和独立运行仍待参考板实测。

## 快速开始

安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，创建 Python 3.12 虚拟环境。

```sh
git clone https://github.com/xucailiang/esp-agentkit.git
cd esp-agentkit/skill/esp-agentkit
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
```

参考板为 **ESP32-DevKitC V4，WROOM-32E，4 MiB**。新板先按 [USB 首刷指南](skill/esp-agentkit/SKILL.md#install-the-firmware-on-a-new-board) 写入固件，再在可信的 2.4 GHz 局域网内首配并部署示例。将 `PORT` 换成实际串口，`BOARD_IP` 和 `DEVICE_ID` 使用首配输出中的 `address` 和 `device_id`。

```sh
python scripts/provision.py --port PORT
python scripts/agentkit.py deploy --address BOARD_IP --device-id DEVICE_ID examples/startup
```

以 115200 波特率打开串口监视器，按 EN 键，预期会看到 `Application started. Free heap:`。部署自己的应用时，将 `examples/startup` 换成包含 `app.py` 和依赖的目录。

[CLI 与故障恢复](skill/esp-agentkit/SKILL.md#wireless-debugging) · [参考板资料](skill/esp-agentkit/boards/esp32-devkitc-v4.md) · [开发计划](docs/architecture-and-development.md) · [提交问题](https://github.com/xucailiang/esp-agentkit/issues) · [MIT 许可证](LICENSE) · [第三方许可](docs/third-party.md)
