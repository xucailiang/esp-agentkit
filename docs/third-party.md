# Third-party software

Desktop dependencies are declared in [pyproject.toml](../skill/esp-agentkit/pyproject.toml), with resolved versions recorded in [uv.lock](../skill/esp-agentkit/uv.lock). The Skill's [firmware installation guide](../skill/esp-agentkit/SKILL.md#install-the-firmware-on-a-new-board) lists the official MicroPython release images, checksums and layout requirements used by the USB installer.

| Component | Version | Use in ESP Agent Kit | Upstream license |
| --- | --- | --- | --- |
| MicroPython | 1.29.0 | Interpreter and WebREPL on ESP32 and ESP8266 | [MIT and bundled component notices](https://github.com/micropython/micropython/blob/v1.29.0/LICENSE) |
| esptool | 5.1.0 | Chip identification, USB flashing and image verification | [GPL-2.0-or-later](https://pypi.org/project/esptool/5.1.0/) |
| mpremote | 1.29.0 | USB provisioning, raw REPL execution and file transfers | [MIT](https://github.com/micropython/micropython/blob/v1.29.0/tools/mpremote/LICENSE) |
| websockets | 15.0.1 | Desktop WebSocket connection to WebREPL | [BSD-3-Clause](https://pypi.org/project/websockets/15.0.1/) |
| ESP-IDF | Selected and pinned by each native project; initial integration uses v5.5.1 | Optional native SDK, installed separately from the Kit's Python environment | [Apache-2.0 and bundled component notices](https://github.com/espressif/esp-idf/blob/v5.5.1/LICENSE) |

Native project components and SDK tools are not bundled with this repository.
Keep their exact versions and licenses with the device project and its build
archives; SDK installation alone does not establish hardware validation.
