# Third-party software

The desktop environment installs the versions in [requirements.txt](../skill/esp-agentkit/requirements.txt). The board profile links to the [official firmware image](../skill/esp-agentkit/boards/esp32-devkitc-v4.md#usb-firmware-baseline). Both the Python distributions and the firmware are obtained from upstream.

| Component | Version | Use in ESP Agent Kit | Upstream license |
| --- | --- | --- | --- |
| MicroPython | 1.29.0 | Interpreter and WebREPL on the reference board | [MIT and bundled component notices](https://github.com/micropython/micropython/blob/v1.29.0/LICENSE) |
| esptool | 5.1.0 | Flash identification and initial USB flashing | [GPL-2.0-or-later](https://pypi.org/project/esptool/5.1.0/) |
| mpremote | 1.29.0 | USB provisioning, raw REPL execution and file transfers | [MIT](https://github.com/micropython/micropython/blob/v1.29.0/tools/mpremote/LICENSE) |
| websockets | 15.0.1 | Desktop WebSocket connection to WebREPL | [BSD-3-Clause](https://pypi.org/project/websockets/15.0.1/) |
