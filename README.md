# ESP Agent Kit

English · [简体中文](README.zh-CN.md)

ESP Agent Kit gives a desktop coding agent the tools to develop MicroPython applications on an ESP32 with you. You supply the wiring and describe what the device should do. The agent writes the application, deploys it over Wi-Fi, and uses pin readings and your observations to make the next change. The installed application starts on the board when you power it up without the computer.

The kit ships as a portable agent skill with a Python CLI. Point your agent at the [skill guide](skill/esp-agentkit/SKILL.md), or use the same CLI from any agent that can run local commands.

## What a development session looks like

> My motion sensor drives GPIO27 high. An active-high buzzer is on GPIO26. Keep it quiet at startup. Once the sensor is ready, buzz for one second on each rising edge. If the sensor stays high, buzz only once.

You wire the device and try it. If you hear repeated beeps while moving, the agent can sample GPIO27 while you repeat the motion to find out whether the sensor stayed high or produced several pulses. It can then change the application and deploy it to the same board. When the device behaves as agreed on independent power, keep the accepted source, dependencies, wiring and observations in your device project. A different agent can pick up the next change there.

## Project status

USB provisioning, wireless probes, application deployment and recovery are implemented. Desktop integration runs on Unix MicroPython exercised the wireless protocol and started a two-file application. Flashing, Wi-Fi reconnects, power-loss recovery and the motion-sensor example still await verification on a physical ESP32. [Architecture and staged development plan (Chinese)](docs/architecture-and-development.md)

## Quick start

The reference board is a classic **ESP32-DevKitC V4** with an ESP32-WROOM-32E module and 4 MiB flash. Use a Micro-B USB data cable and a trusted 2.4 GHz LAN that lets the desktop reach the board. MicroPython WebREPL sends traffic unencrypted.

### 1. Prepare the desktop

With Git and Python 3.12 installed, run the following in a macOS or Linux shell. You can do this before the board arrives.

```sh
git clone https://github.com/xucailiang/esp-agentkit.git
cd esp-agentkit/skill/esp-agentkit
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/agentkit.py --help
```

### 2. Set up the board once over USB

Connect the board. Follow the USB guide to identify its serial port, verify the firmware image and flash a new board with the official MicroPython v1.29.0 `ESP32_GENERIC` OTA variant. The initial flash command erases existing contents. The board profile includes the image digest, and the guide covers CP2102N and CH340/CH341 serial bridges. [USB setup instructions](skill/esp-agentkit/SKILL.md#find-the-boards-serial-port) · [Reference board profile](skill/esp-agentkit/boards/esp32-devkitc-v4.md)

Back in the skill directory, run the installer with your board's serial port in place of `PORT`.

```sh
python scripts/provision.py --port PORT
```

Enter the Wi-Fi credentials and a separate WebREPL password in the local terminal. Once the installer confirms a wireless login, save the `address` and `device_id` from its JSON output in your device project.

### 3. Deploy an application over Wi-Fi

Replace `BOARD_IP` and `DEVICE_ID` with the values from setup. The example needs only the board.

```sh
python scripts/agentkit.py info --address BOARD_IP
python scripts/agentkit.py deploy --address BOARD_IP --device-id DEVICE_ID examples/startup
```

The example imports a helper and prints the available heap at startup. To see it run, open a serial monitor at 115200 baud and press EN. Look for `Application started. Free heap:` followed by a byte count. For your own device, pass a directory containing `app.py` and its dependencies to the same deploy command. [Example source](skill/esp-agentkit/examples/startup/app.py)

## CLI reference

| Command | What it does |
| --- | --- |
| `info` | Read the device ID, firmware version, deployed manifest ID and any captured startup exception. |
| `run FILE` | Execute a short Python probe, return its output and report any uncaught exception. |
| `stop` | Interrupt the foreground program and return to the prompt. The installed application starts again on reset. |
| `put FILE` | Upload a single file for experiments. Include it in your local application directory if the released application needs it. |
| `deploy DIR` | Install the complete application, restart the board and check that wireless login returns. |

All five commands use the board's foreground interpreter and interrupt a running application. During a physical trial, use the login-only check if you need to confirm connectivity. For an interrupted deployment, reconnect to the same board and redeploy the complete application. [Login-only check](skill/esp-agentkit/SKILL.md#retry-a-lan-login) · [Application recovery](skill/esp-agentkit/SKILL.md#recover-an-application)

## Contributing

Board reports are especially welcome while the reference hardware awaits validation. Include the board and module markings, firmware version, command, expected behavior and what you observed. Keep credentials out of logs and screenshots. [Open an issue](https://github.com/xucailiang/esp-agentkit/issues)

## License

ESP Agent Kit is distributed under the [MIT License](LICENSE). The firmware and desktop dependencies have their own licenses. [Third-party software](docs/third-party.md)
