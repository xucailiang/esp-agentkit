---
name: esp-agentkit
description: Help a hardware developer provision an ESP32-DevKitC V4, run MicroPython probes, deploy applications over Wi-Fi, and recover an interrupted installation.
---

# ESP Agent Kit

For a new board, start with USB setup below. For a configured board, use its current DHCP address and the device ID from the developer's project. [Run a probe](#wireless-debugging) to inspect a specific signal or [deploy the application](#deploy-an-application) for the developer's next trial. Use [Retry a LAN login](#retry-a-lan-login) to check connectivity during a trial.

Use a USB data cable for setup and a trusted 2.4 GHz Wi-Fi network that allows the desktop to reach the board. The developer identifies the board and enters its credentials in a local terminal. WebREPL uses an unencrypted connection. Keep passwords out of chat, command arguments and project records.

Run the commands from the directory containing this file. Use Python 3.12 in an activated virtual environment. You can install the host tools and list the existing ports before you have a board.

```sh
python -m pip install -r requirements.txt
python -m serial.tools.list_ports -v
```

## Find the board's serial port

Once the board is available, connect it with a data cable and list the ports again. Use the newly appearing port as `PORT` below (for example, `/dev/cu.SLAB_USBtoUART`, `/dev/ttyUSB0` or `COM4`). Unplug and reconnect the board if the choice is ambiguous. Bluetooth and debug-console ports may already be present with the board unplugged.

| What changes when you plug it in? | Next step |
| --- | --- |
| No new USB device appears in macOS System Information → USB, Windows Device Manager, or Linux `lsusb` | Try a known data cable and another USB port. The board's power LED can light even with a charge-only cable. |
| A USB device appears, but there is no new serial port | Identify the bridge from its marking, the schematic, or the USB hardware ID. The official reference board uses Silicon Labs CP2102N. A board fitted with WCH CH340/CH341 uses the WCH driver. On Windows or macOS, use the matching vendor driver if automatic detection failed. On Linux, check that the distribution's `cp210x` or `ch341` kernel driver is available and bound. Reconnect and list ports again. |
| A serial port appears but the tool cannot open it | Close serial monitors and other processes using the port. On Linux, check your user's access to the device node. Then run the read-only `flash-id` probe below. |

The USB bridge driver runs on the desktop. It is needed for flashing, USB configuration and serial recovery. Wi-Fi login uses the network connection. [Espressif serial connection guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/get-started/establish-serial-connection.html) · [CP210x drivers](https://www.silabs.com/software-and-tools/usb-to-uart-bridge-vcp-drivers) · [CH340/CH341 for Windows](https://www.wch-ic.com/downloads/CH341SER_EXE.html) · [CH340/CH341 for macOS](https://github.com/WCHSoftGroup/ch34xser_macos)

## Install the firmware on a new board

1. Read the shield marking and match the carrier to the [reference board profile](boards/esp32-devkitc-v4.md). Run the probe below and check that it reports a classic ESP32 and 4 MB flash.

   ```sh
   python -m esptool --chip esp32 --port PORT flash-id
   ```

2. Download the full USB image linked in the profile. Run the command below in the image directory and compare the complete SHA-256 value with the profile before writing it.

   ```sh
   python -m pip hash ESP32_GENERIC-OTA-20260824-v1.29.0.bin
   ```

3. The following command erases the entire board, including previous programs and credentials. Use it for a new board or an intended full reinstall. On an already provisioned board with the matching firmware, go directly to USB configuration. Run this command in the image directory.

   ```sh
   python -m esptool --chip esp32 --port PORT --baud 460800 write-flash --erase-all 0x1000 ESP32_GENERIC-OTA-20260824-v1.29.0.bin
   ```

## Configure and verify

Return to the skill directory. Run this command in a local interactive terminal and enter the Wi-Fi network name, its password and a separate WebREPL password at the prompts. Choose 4 to 9 printable ASCII characters for the WebREPL password.

```sh
python scripts/provision.py --port PORT
```

The installer checks the running firmware, copies the bootstrap and private configuration over USB, resets the board and reads its DHCP address from the serial boot message. It then opens a new WebREPL connection from the desktop and waits for the board to confirm authentication. A serial address alone confirms only that the board joined Wi-Fi.

After successful login the installer prints a JSON record to standard output. Save that record with the device project. It includes the board name, firmware version and variant, device ID, address and login result. DHCP can assign a different address after a later restart. A serial terminal at 115200 baud shows `ESP_AGENTKIT_READY` and the IPv4 address during a normal connected boot.

## Retry a LAN login

If USB setup installed the files but the desktop login failed, close any other WebREPL client and check routing or client isolation. From the skill directory, run the following command with the board's current IPv4 address. It prompts for the existing WebREPL password, authenticates, and closes the connection without sending Python commands. Its JSON result records the address, port and login outcome. Keep the USB record for board identity.

```sh
python scripts/verify_webrepl.py --address 192.168.1.42
```

If the board has no IP after 20 seconds, it prints `ESP_AGENTKIT_PENDING` and its station MAC, and continues normal startup. The ESP32 keeps trying to join Wi-Fi with the saved credentials. Once the router is available, find that MAC in its DHCP leases and use the assigned address with the command above. A first USB setup that ends in this state reports an unconfirmed login and exits nonzero. Save the board, firmware and device ID from its USB diagnostic alongside the later wireless result. If the password or SSID actually changed, repeat USB configuration to install the new values.

## Wireless debugging

Use these commands between trials. All three send an interrupt to the board's foreground interpreter. A successful command leaves it at the interactive prompt. To resume the installed startup program, press EN with BOOT released, or power-cycle the board. While measuring a pulse or observing the application's continuous behavior, let it run through the full trial.

| Command | Result |
| --- | --- |
| `python scripts/agentkit.py info --address 192.168.1.42` | Prints JSON with the address, interpreter, device ID and deployment status. Compare `device_id` with the USB record before running a probe that drives hardware. |
| `python scripts/agentkit.py run --address 192.168.1.42 probe.py` | Executes a local UTF-8 script once in the current interpreter. On completion, returns its output on stdout and any uncaught board exception on stderr. Use short text probes. |
| `python scripts/agentkit.py stop --address 192.168.1.42` | Confirms the interpreter is back at its interactive prompt and prints a JSON record with `repl_ready`. GPIO outputs may retain their last levels. |

Use text for probe output. Print binary readings in hex because raw REPL reserves byte `0x04` for its output delimiters. The CLI buffers up to 1 MiB each for output and traceback. A long-running probe should regularly yield with a call such as `time.sleep_ms(20)` so the ESP32 can service WebREPL. A tight compute loop or a blocking native operation can delay login or interruption. Use the USB serial interrupt if the wireless interpreter is unresponsive, or press EN to restart the board. [MicroPython raw REPL protocol](https://docs.micropython.org/en/v1.29.0/reference/repl.html#raw-mode-and-raw-paste-mode) · [ESP32 event handling](https://github.com/micropython/micropython/blob/v1.29.0/ports/esp32/mphalport.c)

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

The bundled [startup example](examples/startup/app.py) imports [heap_report.py](examples/startup/heap_report.py) and prints a startup message with the available heap. It runs without external peripherals. From the Skill directory, substitute the address and the device ID recorded at USB setup.

```sh
python scripts/agentkit.py deploy --address 192.168.1.42 --device-id DEVICE_ID examples/startup
```

The CLI interrupts the foreground program, checks the device ID and available space, marks the deployment in progress, and installs changed files with SHA-256 verification. It reconciles the dedicated `/agentkit_app` directory to the source bundle, so store measurements that must survive updates elsewhere. The board commits the manifest, clears the marker and receives a hard reset request. The CLI then attempts a fresh login without interrupting the new application. The result includes the manifest ID, reset acknowledgment and login outcome.

Give the developer the agreed physical action to try. Record what they observed against this manifest ID, alongside the matching source, dependencies and wiring in the device project. A successful login only confirms that management is reachable. If the software needs a change, edit the local source and repeat the same deploy command.

### Try one module before deployment

```sh
python scripts/agentkit.py put --address 192.168.1.42 --device-id DEVICE_ID examples/startup/heap_report.py
```

`put` interrupts the foreground program and writes the file under its basename in `/agentkit_trials`. To try this version, run a local probe containing the following lines. Add the dependency to the local application directory when it belongs in the release.

```python
execfile("/agentkit_trials/heap_report.py")
print(free_bytes())
```

### Recover an application

Use `info` between trials to inspect `deployment.state`, `manifest_id` and `boot_error`. Reading them interrupts the foreground application.

| What you see | Next action |
| --- | --- |
| `setup_required` or the CLI reports an incompatible launcher | On a board configured with the earlier Skill, run `python scripts/provision.py --port PORT --keep-config` once over USB. Enter the existing WebREPL password for the final login. |
| `empty`, `deploying` or `invalid` | Confirm the device ID. Deploy the complete local application again. The startup loader skips an incomplete release. |
| Manifest committed, reset or login unconfirmed | Check the board's current DHCP address and retry the login-only verifier. To inspect the installed version, use `info` and compare the device ID and manifest ID with the intended release. Restart with EN or rerun the same deploy to resume that application. If the manifest differs, deploy the intended source directory. |
| `boot_error` identifies a manifest and a traceback | Capture the error before resetting. Fix its cause in the local project and redeploy. |
| The application disables Wi-Fi on every boot | Use USB to suspend application startup as shown below. Correct the local source and redeploy over Wi-Fi when login returns. |

## Recover over USB

- **Suspend the application and keep the existing network configuration.** Use the following command on the provisioned board, then retry the LAN login. `resume` skips mpremote's automatic soft reset so it can interrupt the running application and write the same pending marker used during installation.

  ```sh
  python -m mpremote connect PORT resume exec "open('/.agentkit-deploying', 'wb').close()" reset
  ```

- **Port opens but esptool cannot synchronize.** Check the selected port and follow the BOOT/EN sequence in the board profile to enter download mode. Release BOOT and reset normally after flashing.
- **Existing customized startup file.** Setup interrupts the running interpreter and stops before writing any files if it finds an unfamiliar `boot.py` or `main.py`. Back up and move the custom files aside over USB before repeating setup. Press EN to resume the currently installed startup program if you leave setup.
- **`config_invalid` or `wifi_failed`.** Inspect the board configuration and 2.4 GHz network settings. Repeat USB setup if the saved configuration needs to change.
- **`webrepl_failed`.** Check that the reference firmware is installed and the listener can start on port 8266.

## Verify on the reference board

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
