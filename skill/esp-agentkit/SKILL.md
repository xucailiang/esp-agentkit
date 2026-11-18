---
name: esp-agentkit
description: Help a hardware developer provision an ESP32-DevKitC V4 or an ESP8266 NodeMCU V3 (ESP-12F), run MicroPython probes, deploy applications over Wi-Fi, and recover an interrupted installation.
---

# ESP Agent Kit

For a new board, start with USB setup below. For a configured board, open the developer's device project and find the source and observations from the last accepted version. Use the board's current DHCP address for a [login check](#retry-a-lan-login) while the application runs. Between trials, [run a probe](#wireless-debugging) to inspect a signal or [deploy the requested change](#deploy-an-application) using the recorded device ID.

Use a USB data cable for setup and a trusted 2.4 GHz Wi-Fi network that allows the desktop to reach the board. The developer identifies the board and enters its credentials in a local terminal. WebREPL uses an unencrypted connection. Keep passwords out of chat, command arguments and project records.

Run the commands from the directory containing this file. Use uv to create the desktop Python 3.12 environment. On Windows PowerShell, activate it with `.\.venv\Scripts\Activate.ps1`.

```sh
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
python -m serial.tools.list_ports -v
```

## Find the board's serial port

Once the board is available, connect it with a data cable and list the ports again. Use the newly appearing port as `PORT` below (for example, `/dev/cu.SLAB_USBtoUART`, `/dev/ttyUSB0` or `COM4`). Unplug and reconnect the board if the choice is ambiguous. Bluetooth and debug-console ports may already be present with the board unplugged.

| What changes when you plug it in? | Next step |
| --- | --- |
| No new USB device appears in macOS System Information → USB, Windows Device Manager, or Linux `lsusb` | Try a known data cable and another USB port. The board's power LED can light even with a charge-only cable. |
| A USB device appears, but there is no new serial port | Identify the bridge from its marking, schematic, or USB hardware ID. The DevKitC V4 uses a Silicon Labs CP2102N. The NodeMCU V3 in the ESP-12F profile uses a WCH CH340. On Windows or macOS, use the matching vendor driver if automatic detection failed. On Linux, check that the distribution's `cp210x` or `ch341` kernel driver is available and bound. Reconnect and list ports again. |
| A serial port appears but the tool cannot open it | Close serial monitors and other processes using the port. On Linux, check your user's access to the device node. Then run the read-only `flash-id` probe below. |

The USB bridge driver runs on the desktop. It is needed for flashing, USB configuration and serial recovery. Wi-Fi login uses the network connection. [Espressif serial connection guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/get-started/establish-serial-connection.html) · [CP210x drivers](https://www.silabs.com/software-and-tools/usb-to-uart-bridge-vcp-drivers) · [CH340/CH341 for Windows](https://www.wch-ic.com/downloads/CH341SER_EXE.html) · [CH340/CH341 for macOS](https://github.com/WCHSoftGroup/ch34xser_macos)

## Install the firmware on a new board

1. Read the module marking and check the carrier pinout. Run the read-only probe below. Both profiles require 4 MiB of flash (`4MB` in esptool output).

   ```sh
   python -m esptool --chip auto --port PORT --no-stub flash-id
   ```

2. Choose the profile that matches the hardware and download its linked full USB image.

   | Module and carrier | Detected chip | MicroPython v1.29.0 image |
   | --- | --- | --- |
   | [ESP32-WROOM-32E on DevKitC V4](boards/esp32-devkitc-v4.md) | Classic ESP32 | `ESP32_GENERIC-OTA-20260824-v1.29.0.bin` |
   | [ESP-12F on NodeMCU V3](boards/esp8266-esp-12f.md) | ESP8266EX | `ESP8266_GENERIC-20260824-v1.29.0.bin` (standard build) |

   In the download directory, replace `IMAGE.bin` with the filename from your row and compare the complete digest with the SHA-256 in the linked profile.

   ```sh
   python -c "from pathlib import Path; import hashlib, sys; print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())" IMAGE.bin
   ```

3. If the matching firmware is already installed, go directly to [USB configuration](#configure-and-verify). For a full reinstall, back up any files you want to keep. The commands below erase the entire flash, including programs and credentials. Run **only the command for your board**, from the image directory.

   **ESP32-DevKitC V4**

   ```sh
   python -m esptool --chip esp32 --port PORT --baud 460800 write-flash --erase-all 0x1000 ESP32_GENERIC-OTA-20260824-v1.29.0.bin
   ```

   **ESP8266 NodeMCU V3 (ESP-12F)**

   ```sh
   python -m esptool --chip esp8266 --port PORT --baud 460800 write-flash --erase-all --flash-size detect 0x0 ESP8266_GENERIC-20260824-v1.29.0.bin
   ```

## Configure and verify

Return to the skill directory. Run this command in a local interactive terminal and enter the Wi-Fi network name, its password and a separate WebREPL password at the prompts. Choose 4 to 9 printable ASCII characters for the WebREPL password.

```sh
python scripts/provision.py --port PORT
```

The installer checks the firmware and flash size, then copies the bootstrap and new credentials over USB. After reset, the board starts a fresh Wi-Fi connection with those credentials, even for the same network name. Once it obtains an IP address during startup, it saves the new configuration. The installer reads that address over USB and verifies WebREPL login.

After successful login the installer prints a JSON record to standard output. Save that record with the device project. It includes the board name, firmware version and variant, device ID, address and login result. DHCP can assign a different address after a later restart. A serial terminal at 115200 baud shows `ESP_AGENTKIT_READY` and the IPv4 address during a normal connected boot.

## Retry a LAN login

If USB setup installed the files but the desktop login failed, close any other WebREPL client and check routing or client isolation. From the skill directory, run the following command with the board's current IPv4 address. It prompts for the existing WebREPL password, authenticates, and closes the connection without sending Python commands. Its JSON result records the address, port and login outcome. Keep the USB record for board identity.

```sh
python scripts/verify_webrepl.py --address 192.168.1.42
```

If the board reports `wifi_auth_failed`, check the network name and password and repeat USB setup. If it is still waiting for an IP after 20 seconds, it prints `ESP_AGENTKIT_PENDING` and its station MAC and continues to the application. USB setup exits with an unconfirmed login. When the router is available, look up that MAC in its DHCP leases and verify login at the assigned address. The board saves pending credentials on a boot that obtains an IP within the startup window.

## Wireless debugging

Use these commands between trials. All three send an interrupt to the board's foreground interpreter. A successful command leaves it at the interactive prompt. To resume the installed startup program, press the reset button (EN on the DevKitC, RST on the NodeMCU) with BOOT/FLASH released, or power-cycle the board. While measuring a pulse or observing the application's continuous behavior, let it run through the full trial.

| Command | Result |
| --- | --- |
| `python scripts/agentkit.py info --address 192.168.1.42` | Prints JSON with the address, interpreter, device ID and deployment status. Compare `device_id` with the USB record before running a probe that drives hardware. |
| `python scripts/agentkit.py run --address 192.168.1.42 probe.py` | Executes a local UTF-8 script once in the current interpreter. On completion, returns its output on stdout and any uncaught board exception on stderr. Use short text probes. |
| `python scripts/agentkit.py stop --address 192.168.1.42` | Confirms the interpreter is back at its interactive prompt and prints a JSON record with `repl_ready`. GPIO outputs may retain their last levels. |

Use text for probe output. Print binary readings in hex because raw REPL reserves byte `0x04` for its output delimiters. The CLI buffers up to 1 MiB each for output and traceback. A long-running probe should regularly yield with a call such as `time.sleep_ms(20)` so the board can service WebREPL. A tight compute loop or a blocking native operation can delay login or interruption. Use the USB serial interrupt if the wireless interpreter is unresponsive, or use the board's reset button. [MicroPython raw REPL protocol](https://docs.micropython.org/en/v1.29.0/reference/repl.html#raw-mode-and-raw-paste-mode) · [ESP32 event handling](https://github.com/micropython/micropython/blob/v1.29.0/ports/esp32/mphalport.c)

For interactive use the CLI prompts for the WebREPL password with echo disabled. For an Agent, pipe one password line directly from the developer's local credential store with `--password-stdin`. Both `agentkit.py` commands and the login-only `verify_webrepl.py` accept this option. For example, if a macOS Keychain generic password has been saved under service `esp-agentkit-lab-board`, the following command reads it for one login.

```sh
security find-generic-password -s esp-agentkit-lab-board -w | python scripts/agentkit.py info --address 192.168.1.42 --password-stdin
```

| Exit | What the Agent should do |
| --- | --- |
| 0 | Read stdout for the command result. Save the device ID and relevant probe observations in the developer's project. |
| 2 | Correct the arguments, source file, local Python environment or password input. |
| 3 | Check the board address, LAN reachability and WebREPL password. Close other WebREPL clients and retry the login. |
| 4 | Read the board traceback on stderr and any preceding script output on stdout. Correct the probe or its dependencies. |
| 5 | Execution or the final interpreter state could not be confirmed. If the CLI reports that execution finished, its output and traceback are available even if the return to the interactive prompt failed. A timed-out probe may still be driving hardware. Use `stop` to regain the prompt before deciding whether to repeat it. If the board has lost networking, recover over USB. |
| 130 | The local command was cancelled. If it had started a probe, use `stop` to check the board. |

## Deploy an application

Keep deployable files together in their own directory in the developer's project. The entry is `app.py`. Put its Python modules, packages, and data files inside that directory. It becomes the application's working directory at startup. The CLI skips dot-prefixed entries and `__pycache__` and rejects symbolic links in the bundle. Keep credentials, notes, and acceptance records outside it.

On the ESP8266, the launcher reads `app.py` into RAM to execute it. Keep that file short and import drivers and larger application modules as needed.

To install an application, use its directory in the developer's project, the board's current address and the device ID recorded at USB setup. This replaces the installed application and restarts the board.

```sh
python scripts/agentkit.py deploy --address 192.168.1.42 --device-id DEVICE_ID /path/to/device-project/app
```

To try a bundled example, substitute [`examples/startup`](examples/startup/app.py) to print the available heap, or [`examples/esp8266-led`](examples/esp8266-led/app.py) on the NodeMCU V3 to flash the onboard LED. For the LED example, look for three short flashes followed by a 1.4-second pause, then press RST and check that the pattern returns.

The CLI interrupts the foreground program, checks the device ID and estimates the space needed to stage files and create directories on littlefs2. It then marks the deployment in progress and installs changed files with SHA-256 verification. It reconciles the dedicated `/agentkit_app` directory to the source bundle, so store measurements that must survive updates elsewhere. The board commits the manifest, clears the marker and receives a hard reset request. The CLI then attempts a fresh login without interrupting the new application. The result includes the manifest ID, reset acknowledgment and login outcome.

Ask the developer to try the agreed action. If the behavior needs a change, edit the project's working copy and deploy again. On acceptance, record the device ID, manifest ID, board, firmware, wiring and the developer's observation in the device project. Link that record to a local commit or a snapshot of the deployable directory, including its dependencies. Keep the accepted version available when editing the next one.

### Try one module before deployment

```sh
python scripts/agentkit.py put --address 192.168.1.42 --device-id DEVICE_ID examples/startup/heap_report.py
```

`put` interrupts the foreground program and writes the file under its basename in `/agentkit_trials`. To try this version, run a local probe containing the following lines. Add the dependency to the local application directory when it belongs in the release.

```python
with open("/agentkit_trials/heap_report.py") as module:
    exec(module.read())
print(free_bytes())
```

### Recover an application

Use `info` between trials to inspect `deployment.state`, `manifest_id` and `boot_error`. Reading them interrupts the foreground application.

| What you see | Next action |
| --- | --- |
| `setup_required` or the CLI reports an incompatible launcher | On a board configured with the earlier Skill, run `python scripts/provision.py --port PORT --keep-config` once over USB. Enter the existing WebREPL password for the final login. |
| Board storage is full | Reduce the application bundle or free space on the board, then deploy the complete application again. |
| `empty`, `deploying` or `invalid` | Confirm the device ID. Deploy the complete local application again. The startup loader skips an incomplete release. |
| Manifest committed, reset or login unconfirmed | Check the board's current DHCP address and retry the login-only verifier. To inspect the installed version, use `info` and compare the device ID and manifest ID with the intended release. Press the reset button or rerun the same deploy to resume that application. If the manifest differs, deploy the intended source directory. |
| `boot_error` identifies a manifest and a traceback | Capture the error before resetting. Fix its cause in the local project and redeploy. |
| The application disables Wi-Fi on every boot | Use USB to suspend application startup as shown below. Correct the local source and redeploy over Wi-Fi when login returns. |

## Recover over USB

- **Suspend the application and keep the existing network configuration.** Use the following command on the provisioned board, then retry the LAN login. `resume` skips mpremote's automatic soft reset so it can interrupt the running application and write the same pending marker used during installation.

  ```sh
  python -m mpremote connect PORT resume exec "open('/.agentkit-deploying', 'wb').close()" reset
  ```

- **Port opens but esptool cannot synchronize.** Check the selected port and follow the manual download-mode sequence in the matching board profile. After flashing, release BOOT/FLASH and reset normally.
- **A Wi-Fi change failed.** Run `python scripts/provision.py --port PORT --keep-config` to use the previously saved configuration and its WebREPL password. If only a pending configuration exists, the installer retries it instead.
- **Existing customized startup file.** Setup interrupts the running interpreter and stops before writing any files if it finds an unfamiliar `boot.py` or `main.py`. Back up and move the custom files aside over USB before repeating setup. Press the reset button to resume the currently installed startup program if you leave setup.
- **`config_invalid` or `wifi_failed`.** Inspect the board configuration and 2.4 GHz network settings. Repeat USB setup if the saved configuration needs to change.
- **`webrepl_failed`.** Check that the firmware matches the board profile and the listener can start on port 8266.

## Verify on the selected board

1. With the router online, provision over USB. Expect the installer to report an address and a positive WebREPL login.
2. Boot the provisioned board with the router offline for at least 20 seconds. After the pending status appears, bring the router online, find the board's lease and verify the login over Wi-Fi while the board stays powered.
3. Hold a WebREPL connection open from another client and try the wireless verifier. Expect a nonzero exit. Release the first connection and repeat the same command. Expect an authenticated result using the existing configuration.
4. Run `info` twice. Confirm both device IDs match the USB setup record. Run a probe containing `print(6 * 7)` and expect stdout to contain `42`. Then add `raise ValueError("probe marker")` on the next line. Expect the same stdout, a board traceback ending in `ValueError: probe marker` on stderr, and exit code 4.
5. Run a probe containing the following loop with `run --timeout 2`. Expect exit code 5. Run `stop`, expect `repl_ready` to be true, then repeat the successful arithmetic probe. These checks use no GPIO pins.

   ```python
   import time

   while True:
       time.sleep_ms(20)
   ```
