# NodeMCU V3-CH340 with ESP-12F

The supplier's pinout identifies the NodeMCU V3-CH340 carrier and ESP-12F module. A USB probe on this board reported an **ESP8266EX**, **4 MiB flash** and a **26 MHz crystal**. The CH340 bridge enumerated as `1a86:7523`. On another board, check the module marking and carrier pin labels as well as the chip and flash capacity reported by `esptool --chip auto --port PORT flash-id`.

## USB firmware

| Item | Value |
| --- | --- |
| Release | MicroPython v1.29.0, standard `ESP8266_GENERIC` build |
| USB image | [ESP8266_GENERIC-20260824-v1.29.0.bin](https://micropython.org/resources/firmware/ESP8266_GENERIC-20260824-v1.29.0.bin) |
| Download size | 643,424 bytes |
| SHA-256 of the download | `94bfdf94a6c48ac5dfe79203577baad61a58c2525cc39a010b934a6666d4c551` |
| USB write address | `0x0`, with `--flash-size detect` |

Follow the USB setup in the [Skill guide](../SKILL.md). On a blank 4 MiB device, this build creates a littlefs filesystem on first boot. Application updates go there over Wi-Fi. The ESP32 dual-slot firmware layout does not apply to this profile. [Official firmware and variants](https://micropython.org/download/ESP8266_GENERIC/) · [ESP8266 installation guide](https://docs.micropython.org/en/v1.29.0/esp8266/tutorial/intro.html#deploying-the-firmware)

The CH340 supports automatic entry into the ROM loader on this carrier. If the connection stalls at `Connecting`, hold **FLASH**, tap **RST**, then release **FLASH**. Tap **RST** with **FLASH** released for normal startup. The MicroPython USB serial REPL runs at 115200 baud. [Espressif boot mode guide](https://docs.espressif.com/projects/esptool/en/latest/esp8266/advanced-topics/boot-mode-selection.html) · [MicroPython serial REPL](https://docs.micropython.org/en/v1.29.0/esp8266/quickref.html#general-board-control)

## Header pins

`machine.Pin(n)` takes the chip GPIO number. For example, the header marked **D1** is `Pin(5)`.

| Header | GPIO | Use on this carrier |
| --- | --- | --- |
| D0 | 16 | Digital I/O or deep-sleep wake. MicroPython PWM is unavailable here. |
| D1, D2 | 5, 4 | General I/O. Common choices for I²C SCL and SDA, respectively. |
| D3, D4 | 0, 2 | Boot straps. Both must read high at reset for normal startup. |
| D5, D6, D7 | 14, 12, 13 | General I/O or hardware SPI SCK, MISO and MOSI, respectively. |
| D8 | 15 | Boot strap. Keep low at reset. |
| RX/D9, TX/D10 | 3, 1 | UART0 RX and TX. Keep available for USB setup and recovery. |

GPIO6–11 serve the module's flash. Use 3.3 V logic and a common ground for peripherals. [MicroPython ESP8266 pin reference](https://docs.micropython.org/en/v1.29.0/esp8266/quickref.html#pins-and-gpio)

## Onboard blue LED

The carrier schematic connects the blue LED between 3V3 and **D4 (GPIO2)** through a resistor. Driving GPIO2 low turns it on. To try it immediately over USB, run the following from the Skill directory with MicroPython installed.

```sh
python -m mpremote connect PORT run examples/esp8266-led/app.py
```

Look for three short flashes followed by a 1.4-second pause. Press Ctrl-C to close mpremote, then tap RST to end the temporary trial. To start the pattern automatically after reset, [deploy](../SKILL.md#deploy-an-application) the `examples/esp8266-led` directory.
