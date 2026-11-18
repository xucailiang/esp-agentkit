# Third-party software

Desktop dependencies are declared in [pyproject.toml](../skill/esp-agentkit/pyproject.toml), with resolved versions recorded in [uv.lock](../skill/esp-agentkit/uv.lock). Both board profiles link to their official MicroPython release images. [ESP32 firmware](../skill/esp-agentkit/boards/esp32-devkitc-v4.md#usb-firmware-baseline) · [ESP8266 firmware](../skill/esp-agentkit/boards/esp8266-esp-12f.md#usb-firmware)

| Component | Version | Use in ESP Agent Kit | Upstream license |
| --- | --- | --- | --- |
| MicroPython | 1.29.0 | Interpreter and WebREPL on ESP32 and ESP8266 | [MIT and bundled component notices](https://github.com/micropython/micropython/blob/v1.29.0/LICENSE) |
| esptool | 5.1.0 | Flash identification and initial USB flashing | [GPL-2.0-or-later](https://pypi.org/project/esptool/5.1.0/) |
| mpremote | 1.29.0 | USB provisioning, raw REPL execution and file transfers | [MIT](https://github.com/micropython/micropython/blob/v1.29.0/tools/mpremote/LICENSE) |
| websockets | 15.0.1 | Desktop WebSocket connection to WebREPL | [BSD-3-Clause](https://pypi.org/project/websockets/15.0.1/) |
