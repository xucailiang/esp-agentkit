# ESP32-DevKitC V4 with ESP32-WROOM-32E

## Identify the board before flashing

Match the carrier to the DevKitC V4 pinout and read the module shield marking `ESP32-WROOM-32E`. The reference ordering code is `ESP32-DevKitC-32E`. This variant has a classic ESP32, 4 MiB flash, a PCB antenna, and no PSRAM. Check that the USB flash probe reports an ESP32 and 4 MB detected flash. The probe identifies the chip and flash capacity. The shield marking and header layout identify the module and carrier. WROVER, ESP32-S3 and ESP32-C3 boards need separate profiles. [Espressif board guide and ordering table](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html)

Use a Micro-B **data** cable for USB power and the UART0 serial connection. For manual download mode, hold BOOT, press and release EN, then release BOOT. Press EN with BOOT released for normal startup. Power the board from USB, the 5V/GND headers, or regulated 3.3 V on 3V3/GND. Use only one supply at a time. Use 3.3 V GPIO signal levels and a common ground with the peripheral. The onboard LED indicates 5 V power. [Board controls, headers and power options](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html#functional-description)

## USB firmware baseline

The official V4 schematic identifies U1, the USB-to-UART bridge, as a Silicon Labs CP2102N. The ESP32 module marking identifies the radio module. On a compatible carrier, check the bridge separately when choosing a desktop driver. [Espressif V4 schematic, U1](https://dl.espressif.com/dl/schematics/esp32_devkitc_v4_sch.pdf)

| Item | Value |
| --- | --- |
| Release | MicroPython v1.29.0, `ESP32_GENERIC`, OTA variant |
| USB image | [ESP32_GENERIC-OTA-20260824-v1.29.0.bin](https://micropython.org/resources/firmware/ESP32_GENERIC-OTA-20260824-v1.29.0.bin) |
| Complete image size | 1,552,112 bytes |
| SHA-256 of the downloaded image | `e904946b33e2fd21790ee42f2191939119492bf8d962e1fd6013656b450bc414` |
| USB write address | `0x1000` |

Choose the full `.bin` under **Firmware (Support for OTA)**. It contains the bootloader, partition table and initial firmware. The adjacent `.app-bin` contains only the application. The digest above was calculated from the linked release file and can be used to check a local download. [MicroPython download and installation instructions](https://micropython.org/download/ESP32_GENERIC/#firmware-support-for-ota)

| Partition | Flash offset | Size |
| --- | --- | --- |
| `nvs` | `0x9000` | `0x4000` |
| `otadata` | `0xD000` | `0x2000` |
| `phy_init` | `0xF000` | `0x1000` |
| `ota_0` | `0x10000` | `0x180000` (1.5 MiB) |
| `ota_1` | `0x190000` | `0x180000` (1.5 MiB) |
| `vfs` (registered at startup) | `0x310000` | `0xF0000` (960 KiB) |

On the 4 MiB reference board, MicroPython registers the remaining flash as `vfs` at startup. Filesystem metadata and installed files reduce its available capacity. The two OTA slots hold firmware. Boot scripts, management configuration and user files live in the filesystem. [Release partition table](https://github.com/micropython/micropython/blob/v1.29.0/ports/esp32/partitions-4MiB-ota.csv) · [Filesystem partition registration](https://github.com/micropython/micropython/blob/v1.29.0/ports/esp32/main.c#L240-L258)

## Wiring constraints

Use chip GPIO numbers in MicroPython. For the reference PIR and buzzer, `IO27` is GPIO27 at J2 pin 11 and `IO26` is GPIO26 at J2 pin 10. Confirm each peripheral's voltage, signal polarity and pin assignment from the hardware developer's wiring. [DevKitC V4 header map](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32/esp32-devkitc/user_guide.html#header-block)

| Pins on this board | Use or constraint |
| --- | --- |
| GPIO4, 13, 14, 16–19, 21–23, 25–27, 32–33 | Bidirectional digital I/O if available in the user's wiring. GPIO13/14 also serve JTAG. GPIO16/17 are available on this WROOM board. WROVER variants use them internally. |
| GPIO6–11 (`CLK`, `D0`–`D3`, `CMD`) | Reserved for the module's flash bus. |
| GPIO1 (`TX`), GPIO3 (`RX`) | UART0 for USB flashing and the serial REPL at 115200 baud. Keep these available for recovery. |
| GPIO0, 2, 5, 12, 15 | Strapping pins sampled during reset. Check externally driven levels and pulls if using them. |
| GPIO34, 35, 36 (`VP`), 39 (`VN`) | Input only, with no internal pull resistors. Provide an external pull if the input would float. |

For analog input while Wi-Fi is active, choose an exposed ADC1 pin (GPIO32–36 or GPIO39). Wi-Fi uses ADC2. GPIO26 and GPIO27 remain usable for the digital PIR/buzzer signals. [ESP32 GPIO and ADC limitations](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/peripherals/gpio.html#gpio-summary) · [UART0 REPL pinout](https://docs.micropython.org/en/v1.29.0/esp32/quickref.html#general-board-control) · [ESP32 boot configuration](https://www.espressif.com/sites/default/files/documentation/esp32_datasheet_en.pdf)
