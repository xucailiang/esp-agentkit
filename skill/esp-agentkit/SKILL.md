---
name: esp-agentkit
description: Find device projects by name, check hardware and wiring for the requested feature, detect Espressif chips over USB serial, provision supported MicroPython builds on classic ESP32 or ESP8266, run probes, deploy applications over Wi-Fi, and record deployment and acceptance results.
---

# ESP Agent Kit

Start with the requested behavior and [identify the hardware facts it depends on](#check-the-board-and-wiring). For a new board, use [USB detection](#detect-a-connected-device) to check the chip and flash. For an existing project, find it by [name or alias](#work-with-a-project), read its current source, wiring notes and last accepted observations, and check any changes to the physical setup.

Use a USB data cable for setup and a trusted 2.4 GHz Wi-Fi network that allows the desktop to reach the board. WebREPL uses an unencrypted connection. The project stores its WebREPL management password in plaintext in `project.json`; no operating-system credential store or separate machine configuration is required. Keep that password out of command arguments, output, deployment receipts and the public repository. Copying the complete device project carries its connection configuration with it.

## Environment and workspace

Use the cloned `esp-agentkit` repository as the Agent workspace. Read its
root [AGENTS.md](../../AGENTS.md) first, then follow this guide for device operations; no
separate Skill installation or registration is needed. Start with the relevant
sections for a new board, an existing project, or a failed operation. Before
configuring hardware, [check the actual board and wiring](#check-the-board-and-wiring).

Run all desktop commands in this guide from the repository root. The Python
3.12 environment and dependency files remain in `skill/esp-agentkit/`:

```sh
uv sync --project skill/esp-agentkit --locked
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py --help
```

uv's `--project skill/esp-agentkit` selects the Python environment without
changing the working directory. The CLI's own `--project` selects a device
project by name, alias or path. Keep the repository root as the working
directory so project discovery and repository-relative file paths stay
consistent. Use Windows PowerShell or a macOS shell. Current desktop validation
targets macOS and Windows, with Windows hardware validation pending.

## Work with a project

Create new device projects under `device-projects/<project-name>/`, for example
`device-projects/esp12-screen/`. Keep application code, assets, wiring and
operation records in that project. Reuse an existing project's location rather
than moving it automatically. The repository's `.gitignore` excludes
`device-projects/`; preserve and back up the complete project directory when
moving computers or handing it to another Agent.

For a new board, use `provision.py --project-dir device-projects/<project-name>`
as described in [Configure and verify](#configure-and-verify). It creates
`project.json`, a starter `app/app.py` and a README from the verified setup.
For an existing project, keep its source and configuration. If creating a
configuration manually, use the [example](examples/project.example.json),
replacing its dummy device ID, address and password with verified details.
Save it as UTF-8; files with or without a UTF-8 BOM are accepted. `app` is a
relative child directory containing `app.py` and all deployable dependencies;
create that directory and entry before using the project. Keep the configuration
outside the application directory so it is not uploaded as application data.

```json
{
  "schema_version": 1,
  "name": "esp12-screen",
  "aliases": ["ESP12 screen", "display project"],
  "description": "NodeMCU V3 with a 128x128 SPI display",
  "app": "app",
  "device": {
    "device_id": "01020304",
    "address": "192.168.1.42",
    "webrepl_password": "change-me"
  }
}
```

For an existing workspace, list projects before asking the developer for a path
or connection details:

```sh
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py projects
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py info --project "ESP12 screen"
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py deploy --project "ESP12 screen"
```

`projects` returns summaries without passwords. CLI JSON output escapes non-ASCII
characters for compatibility with redirected streams; parse the JSON to recover
the original Unicode names and paths. Names and aliases match exactly,
ignoring case. Discovery searches the current workspace: the nearest enclosing
Git worktree, or the current directory when there is no enclosing worktree. It
does not use a global project registry. An explicit path such as `device-projects/esp12-screen`
or a `project.json` path can also be supplied to `--project`. Bare names always
participate in name, alias and directory-name matching, even when a same-named
directory exists. Multiple matches require a specific choice;
do not guess. For a natural-language request, use the summaries and project
descriptions to identify the intended name or alias.
Invalid configurations are omitted from discovery; pass their path directly
to obtain a validation error without exposing the saved password.

`info`, `run`, `stop`, `put`, `deploy` and `restart` accept `--project`. They use the address,
expected device ID and plaintext password from that project. The CLI checks the
device ID before executing a project probe or installing files. Identity queries
still interrupt the foreground application. A historical README or an open TCP
port does not establish the current installed application. Inspect the actual
device between trials when that distinction matters.

Relative `run` and `put` file arguments resolve from the selected project root.
An explicit `--address` can override a stale DHCP address for an operation; the
device ID must still match. A conflicting explicit `--device-id` is rejected.
`--password-stdin` explicitly overrides the saved password for that invocation.
These overrides do not rewrite `project.json`; update a changed address after
confirming the intended device. Normal project operations do not open USB or
fall back to USB. The existing explicit address/device arguments remain
available for scripts that do not use a project.

Keep `project.json`, the application, source assets and preparation instructions
in the project. The README holds observations and wiring notes or links to them.
Project deployments add `releases/<manifest>/` snapshots and `records/` receipts.
These are ordinary files that another Agent can use after copying the directory.
Before handoff, update the README with the board and firmware, wiring,
dependencies, latest attempted and accepted manifests, observations and open
issues. Copy the complete directory, including releases and records, into the
receiving workspace. Discover it there with `projects`; check the current board
identity and manifest before changing behavior. Historical acceptance applies
only to its recorded version. An independent-power check requires disconnecting
the computer's USB cable and cold-starting from a separate power source.

## Detect a connected device

Connect the board with a USB data cable and run:

```sh
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py detect
```

The command lists local serial interfaces and selects the sole USB serial candidate, if there is one. It then uses esptool to identify the chip through its ROM loader; MicroPython does not need to be installed. With multiple USB candidates it returns their details without opening a port. Select the intended port explicitly:

```sh
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py detect --port PORT
```

Use a local port such as `/dev/cu.SLAB_USBtoUART`, `/dev/ttyUSB0` or `COM4`. Explicit selection also works when USB metadata is unavailable. USB bridge information such as CH340 or CP2102 identifies an interface, not the target chip or carrier board.

| Option | Behavior |
| --- | --- |
| `--list` | Enumerate interfaces without opening any port or probing a chip. |
| `--port PORT` | Probe this local serial port. |
| `--backend auto` | Select an available detection backend; currently only esptool is implemented. |
| `--backend esptool` | Select the Espressif ROM detection backend explicitly. |
| `--timeout SECONDS` | Limit the active probe to a finite positive duration; default: 20 seconds. |

Active detection can reset the device and interrupt its running program. It does not erase or write flash and does not upload a flasher stub. The backend requests a normal reset before closing a connected device. If the reset is unconfirmed, or detection times out or is cancelled, release BOOT/FLASH and reset manually to resume normal startup. If automatic entry into the ROM loader fails, use the download-mode sequence for the actual board and retry.

Stdout contains JSON with `schema_version: 1`, discovered `interfaces`, the detected `device`, and `runtime` and `capabilities` results. Esptool diagnostics go to stderr. A successful probe reports the chip family, description, available features, MAC and flash information where available; inspect warnings for incomplete fields or reset failures. The carrier board remains unknown. Firmware, application filesystem, networking and deployment compatibility are `not_checked`: ROM detection does not inspect the installed application or prove that the board can use the current provisioning and WebREPL deployment workflow. A chip MAC is not a substitute for the `device_id` recorded by USB provisioning.

| Exit | Meaning and next action |
| --- | --- |
| 0 | Interface listing completed, or chip identity was confirmed. Check warnings and unchecked capabilities before setup. |
| 2 | Correct local arguments or install the Skill's dependencies. |
| 3 | No target was found or communication failed. Check the cable, port, drivers and download mode. A failed probe does not establish that the chip is non-Espressif. |
| 5 | Select a port from multiple candidates, or inspect an unconfirmed or timed-out probe before retrying. |
| 130 | The local command was cancelled. A device touched by the probe may need a manual reset. |

Detection currently discovers serial interfaces and probes Espressif devices supported by the installed esptool version. Non-serial USB interfaces, debug probes, STM32, Pico and Linux device backends are future extensions. New detection backends will share this entry point and identify hardware through their protocols. Provisioning and deployment require their documented firmware and runtime conditions.

## Check the board and wiring

Identify what the requested feature needs. For example, to blink an onboard
LED, establish which GPIO drives it and which level turns it on. Use the
materials already supplied for this board. If a detail is missing, explain
what you need to establish and ask for the relevant part of the pinout,
schematic or vendor page for that revision. If the developer has no diagram,
ask for the board markings or a photo that shows them and use those details
to find the matching documentation.

Check external wiring separately. For example, to use an SPI display, ask
which board pin the display's `CS` wire actually reaches. A wiring photo or
the labels at both ends of the wire can answer that question. Before guiding
a connection or driving a pin, confirm the relevant GPIO mapping, voltage and
polarity. If a detail is still unclear, ask about that connection and continue
with work that does not depend on it, such as USB identification.

Keep the board model and revision (when known), source links or supplied
diagrams, actual connections and unresolved questions in the device project's
wiring notes under `device-projects/<project-name>/` (or its existing location).
For a new project, save notes collected before `provision.py --project-dir`
in `wiring.md` and supplied diagrams in a `hardware/` subdirectory. The installer
creates `README.md`, `project.json` and `app/`. After setup, link the saved
notes and diagrams from the generated README.
On a later change, start from those notes and check them against the current
physical setup.

## Find the board's serial port

Use `uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py detect --list` before and after connecting the board to find the newly appearing port. Unplug and reconnect the board if the choice is ambiguous. Bluetooth and debug-console ports may already be present with the board unplugged; detection does not automatically probe ports without USB metadata.

| What changes when you plug it in? | Next step |
| --- | --- |
| No new USB device appears in macOS System Information → USB, Windows Device Manager, or Linux `lsusb` | Try a known data cable and another USB port. The board's power LED can light even with a charge-only cable. |
| A USB device appears, but there is no new serial port | Identify the bridge from its marking, schematic, or USB hardware ID. On Windows or macOS, use the matching vendor driver if automatic detection failed. On Linux, check that the corresponding kernel driver is available and bound, such as `cp210x` for CP210x or `ch341` for CH340. Reconnect and list ports again. |
| A serial port appears but the tool cannot open it | Close serial monitors and other processes using the port. On Linux, check your user's access to the device node. Then retry `detect --port PORT`. |

The USB bridge driver runs on the desktop. It is needed for flashing, USB configuration and serial recovery. Wi-Fi login uses the network connection. [Espressif serial connection guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/get-started/establish-serial-connection.html) · [CP210x drivers](https://www.silabs.com/software-and-tools/usb-to-uart-bridge-vcp-drivers) · [CH340/CH341 for Windows](https://www.wch-ic.com/downloads/CH341SER_EXE.html) · [CH340/CH341 for macOS](https://github.com/WCHSoftGroup/ch34xser_macos)

## Install the firmware on a new board

1. Check the module marking, the manufacturer's firmware requirements and the [detected chip and flash size](#detect-a-connected-device). The current USB installer requires 4 MiB of flash (4,194,304 bytes) and one of the MicroPython v1.29.0 builds below. On classic ESP32 it also checks the OTA partition layout. Confirm the image suits the actual hardware before flashing.

2. If the required firmware is already installed, go directly to [USB configuration](#configure-and-verify). Otherwise download the matching full USB image.

   | Chip | MicroPython v1.29.0 full image | SHA-256 |
   | --- | --- | --- |
   | Classic ESP32 | [ESP32_GENERIC OTA](https://micropython.org/resources/firmware/ESP32_GENERIC-OTA-20260824-v1.29.0.bin) | `e904946b33e2fd21790ee42f2191939119492bf8d962e1fd6013656b450bc414` |
   | ESP8266EX | [ESP8266_GENERIC](https://micropython.org/resources/firmware/ESP8266_GENERIC-20260824-v1.29.0.bin) | `94bfdf94a6c48ac5dfe79203577baad61a58c2525cc39a010b934a6666d4c551` |

   The ESP32 link supplies the full `.bin` with bootloader, partition table and initial firmware. The installer checks these partition offsets and sizes on a 4 MiB device. MicroPython registers `vfs` from the remaining flash during startup. [Release partition table](https://github.com/micropython/micropython/blob/v1.29.0/ports/esp32/partitions-4MiB-ota.csv) · [Filesystem registration](https://github.com/micropython/micropython/blob/v1.29.0/ports/esp32/main.c#L240-L258)

   | Partition | Flash offset | Size |
   | --- | --- | --- |
   | `ota_0` | `0x10000` | `0x180000` |
   | `ota_1` | `0x190000` | `0x180000` |
   | `vfs` | `0x310000` | `0xF0000` |

   The standard ESP8266 build creates a littlefs filesystem on first boot on a blank 4 MiB device. Official installation instructions cover each image's flash layout and write address. [ESP32 installation](https://micropython.org/download/ESP32_GENERIC/#firmware-support-for-ota) · [ESP8266 installation](https://docs.micropython.org/en/v1.29.0/esp8266/tutorial/intro.html#deploying-the-firmware)

   Keep the downloaded image with the device project. From the repository root, replace `IMAGE.bin` below with its repository-relative or absolute path and compare the complete digest with the SHA-256 above.

   ```sh
   uv run --project skill/esp-agentkit --locked python -c "from pathlib import Path; import hashlib, sys; print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())" IMAGE.bin
   ```

3. For a full reinstall, back up any files you want to keep. The commands below erase the entire flash, including programs and credentials. Confirm the chip, image and write address for your hardware before running the corresponding command from the repository root. Replace the image filename with the path to your saved download.

   **Classic ESP32 with the full ESP32_GENERIC OTA image**

   ```sh
   uv run --project skill/esp-agentkit --locked python -m esptool --chip esp32 --port PORT --baud 460800 write-flash --erase-all 0x1000 ESP32_GENERIC-OTA-20260824-v1.29.0.bin
   ```

   **ESP8266 with the standard ESP8266_GENERIC image**

   ```sh
   uv run --project skill/esp-agentkit --locked python -m esptool --chip esp8266 --port PORT --baud 460800 write-flash --erase-all --flash-size detect 0x0 ESP8266_GENERIC-20260824-v1.29.0.bin
   ```

## Configure and verify

From the repository root, run this command in a local interactive terminal and enter the Wi-Fi network name, its password and a separate WebREPL password at the prompts. Choose 4 to 9 printable ASCII characters for the WebREPL password.

```sh
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/provision.py --port PORT --project-dir device-projects/esp12-screen
```

The installer checks the firmware and flash size, then copies the bootstrap and new credentials over USB. After reset, the board starts a fresh Wi-Fi connection with those credentials, even for the same network name. Once it obtains an IP address during startup, it saves the new configuration. The installer reads that address over USB and verifies WebREPL login.

After successful login the installer prints a JSON record to standard output.
It includes the firmware profile, device ID, address and login result. The
profile name does not identify the carrier board; confirm its markings and
wiring separately. DHCP can assign a different address after a later restart.
A serial terminal at 115200 baud shows `ESP_AGENTKIT_READY` and the IPv4 address
during a normal connected boot.

With `--project-dir`, the installer checks the destination before touching the
board. It refuses an existing `project.json`, `app` or `README.md`; unrelated
files such as `wiring.md`, supplied diagrams or a downloaded firmware image can
remain in the directory. It
saves the verified device ID, address and management password directly into
`project.json`. The Wi-Fi password is not saved locally. The starter application
does not drive GPIO and is not deployed automatically. Complete the generated
README's wiring, dependencies and observations, then edit and deploy the app.
Omit `--project-dir` to retain the original setup-only operation for an existing
project; update its configuration using the verified result.

If setup succeeds but local project saving fails, keep the reported device ID
and address, resolve the destination issue and retry only project creation:

```sh
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/provision.py --save-project-only --address 192.168.1.42 --device-id DEVICE_ID --project-dir device-projects/esp12-screen
```

Enter the existing management password at the hidden prompt. For `--keep-config`
or `--save-project-only`, a caller can use `--password-stdin` to pipe the password
already held in a private project configuration without putting it in command
arguments or output. New Wi-Fi configuration still uses hidden prompts.
This path verifies
the device over Wi-Fi without repeating USB setup, changing network settings
or deploying files. Its identity query interrupts the application; use the
explicit restart command below when ready to resume an installed application.
Keep the management password in `project.json`, outside the application,
README and operation receipts.

## Retry a LAN login

If USB setup installed the files but the desktop login failed, close any other WebREPL client and check routing or client isolation. From the repository root, run the following command with the board's current IPv4 address. It prompts for the existing WebREPL password, authenticates, and closes the connection without sending Python commands. Its JSON result records the address, port and login outcome. Keep the USB record for board identity.

```sh
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/verify_webrepl.py --address 192.168.1.42
```

If the board reports `wifi_auth_failed`, check the network name and password and repeat USB setup. If it is still waiting for an IP after 20 seconds, it prints `ESP_AGENTKIT_PENDING` and its station MAC and continues to the application. USB setup exits with an unconfirmed login. When the router is available, look up that MAC in its DHCP leases and verify login at the assigned address. The board saves pending credentials on a boot that obtains an IP within the startup window.

## Wireless debugging

Use these commands between trials. `info`, `run` and `stop` send an interrupt to
the board's foreground interpreter and leave it at the interactive prompt on
success. They do not restart the application automatically. Use
[Restart the installed application](#restart-the-installed-application) when
the probe is finished. While measuring a pulse or observing continuous behavior,
let the application run through the full trial.

| Command | Result |
| --- | --- |
| `uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py info --address 192.168.1.42` | Prints JSON with the address, interpreter, device ID and deployment status. Compare `device_id` with the USB record before running a probe that drives hardware. |
| `uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py run --address 192.168.1.42 device-projects/esp12-screen/probe.py` | Executes a local UTF-8 script once in the current interpreter. On completion, returns its output on stdout and any uncaught board exception on stderr. Use short text probes. |
| `uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py stop --address 192.168.1.42` | Confirms the interpreter is back at its interactive prompt and prints a JSON record with `repl_ready`. GPIO outputs may retain their last levels. |

Use text for probe output. Print binary readings in hex because raw REPL reserves byte `0x04` for its output delimiters. The CLI buffers up to 1 MiB each for output and traceback. A long-running probe should regularly yield with a call such as `time.sleep_ms(20)` so the board can service WebREPL. A tight compute loop or a blocking native operation can delay login or interruption. Use the USB serial interrupt if the wireless interpreter is unresponsive, or use the board's reset button. [MicroPython raw REPL protocol](https://docs.micropython.org/en/v1.29.0/reference/repl.html#raw-mode-and-raw-paste-mode) · [ESP32 event handling](https://github.com/micropython/micropython/blob/v1.29.0/ports/esp32/mphalport.c)

For project operations, use the password already saved in `project.json`:

```sh
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py run --project "ESP12 screen" probe.py
```

For explicit-address interactive use the CLI prompts with echo disabled. A
caller can instead supply one password line through `--password-stdin`; the
login-only `verify_webrepl.py` also accepts this option. Do not require the
developer to repeat a password already available in the selected project.

Operate on one device sequentially. During deployment, do not open another
serial monitor, REPL, probe or reset operation on that board. Let the application
run uninterrupted while the developer observes the agreed behavior.

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
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py deploy --project "ESP12 screen"
```

Without a project configuration, the existing explicit form remains available:

```sh
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py deploy --address 192.168.1.42 --device-id DEVICE_ID device-projects/esp12-screen/app
```

With `--project`, the application directory comes from `project.json`. An
explicit directory must match that configured directory. Before connecting,
the CLI freezes the application under `releases/<manifest>/` and deploys that
snapshot. It records the deployment result under `records/`, including failed
or unconfirmed operations. The configuration and its password are not included
in application snapshots or receipts. Project deployment progress goes to
stderr; stdout retains the final JSON result.

To try a bundled example, copy the contents of [`skill/esp-agentkit/examples/startup`](examples/startup/app.py) into the device project's configured application directory to print the available heap, or use [`skill/esp-agentkit/examples/esp8266-led`](examples/esp8266-led/app.py) on the NodeMCU V3 to flash the onboard LED. Keep later application changes in the device project. For the LED example, look for three short flashes followed by a 1.4-second pause, then press RST and check that the pattern returns.

The CLI interrupts the foreground program, checks the device ID and estimates the space needed to stage files and create directories on littlefs2. It then marks the deployment in progress and installs changed files with SHA-256 verification. It reconciles the dedicated `/agentkit_app` directory to the source bundle, so store measurements that must survive updates elsewhere. The board commits the manifest, clears the marker and receives a hard reset request. The CLI then attempts a fresh login without interrupting the new application. The result includes the manifest ID, reset acknowledgment and login outcome.

Ask the developer to try the agreed action. If the behavior needs a change, edit the project's working copy and deploy again. On acceptance, record the device ID, manifest ID, board, firmware, wiring and the developer's observation in the device project. Link that record to a local commit or a snapshot of the deployable directory, including its dependencies. Keep the accepted version available when editing the next one.

After the developer confirms the actual behavior, add a local acceptance record
for the exact deployed manifest:

```sh
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py accept --project "ESP12 screen" --manifest MANIFEST_ID --observation "Developer confirmed the new image and correct colors."
```

`accept` does not contact the board or perform a test. It records the supplied
observation against a matching confirmed deployment and verified source
snapshot. A committed deployment whose reset or login was unconfirmed can also
qualify after a successful `restart` for the same device and manifest. A restart
alone is not a deployment record; an incomplete or failed recovery does not
qualify. Never infer visual acceptance from successful upload or WebREPL
login, and do not transfer an old version's acceptance to a new manifest.
In the project README, distinguish the latest attempted deployment, confirmed
installation and developer-accepted version; include dates and manifest IDs.

### Try one module before deployment

```sh
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py put --address 192.168.1.42 --device-id DEVICE_ID skill/esp-agentkit/examples/startup/heap_report.py
```

`put` interrupts the foreground program and writes the file under its basename in `/agentkit_trials`. To try this version, run a local probe containing the following lines. Add the dependency to the local application directory when it belongs in the release.

```python
with open("/agentkit_trials/heap_report.py") as module:
    exec(module.read())
print(free_bytes())
```

### Restart the installed application

After a probe or `info`, restart the application already installed on the board:

```sh
uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/agentkit.py restart --project "ESP12 screen"
```

Use `--manifest MANIFEST_ID` when the restart must match a particular release.
Without a project, supply `--address` and `--device-id` and enter the management
password. The command checks the device identity, launcher and installed
manifest and files. It refuses incomplete, invalid or mismatched deployments.
Extra files inside `/agentkit_app` also invalidate the release; keep persistent
measurements outside that managed directory and redeploy the intended complete
application to reconcile its contents.
It does not deploy the working copy or choose a version from local history.

Before reset, the command preserves the current deployment status and boot
error in a project restart record. If this record cannot be saved, it refuses
the reset so the diagnostic remains available. Without a project, preserve the
pre-reset diagnostic printed on stderr yourself. A final result separately
records reset acknowledgment and a login-only reconnect; successful login does
not establish application behavior. Unknown outcomes remain unknown. Restart
records do not create source snapshots or acceptance records.

A restart begins the application from its startup entry; it does not resume a
paused probe. If restart is unconfirmed, inspect its result and retry the
login-only verifier before interrupting the board again. Physical reset remains
available with BOOT/FLASH released. Capture any boot error before resetting.

### Recover an application

Use `info` between trials to inspect `deployment.state`, `manifest_id` and `boot_error`. Reading them interrupts the foreground application.

| What you see | Next action |
| --- | --- |
| `setup_required` or the CLI reports an incompatible launcher | On a board configured with the earlier Skill, run `uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/provision.py --port PORT --keep-config` once over USB. Enter the existing WebREPL password for the final login. |
| Board storage is full | Reduce the application bundle or free space on the board, then deploy the complete application again. |
| `empty`, `deploying` or `invalid` | Confirm the device ID. Deploy the complete local application again. The startup loader skips an incomplete release. |
| Manifest committed, reset or login unconfirmed | Check the board's current DHCP address and retry the login-only verifier. To inspect the installed version, use `info` and compare the device ID and manifest ID with the intended release. Use `restart --project PROJECT --manifest MANIFEST_ID` to restart that installed version. If the manifest differs, decide whether to keep it or deploy the intended source directory. |
| `boot_error` identifies a manifest and a traceback | Capture the error before resetting. Fix its cause in the local project and redeploy. |
| The application disables Wi-Fi on every boot | Use USB to suspend application startup as shown below. Correct the local source and redeploy over Wi-Fi when login returns. |

## Recover over USB

- **Suspend the application and keep the existing network configuration.** Use the following command on the provisioned board, then retry the LAN login. `resume` skips mpremote's automatic soft reset so it can interrupt the running application and write the same pending marker used during installation.

  ```sh
  uv run --project skill/esp-agentkit --locked python -m mpremote connect PORT resume exec "open('/.agentkit-deploying', 'wb').close()" reset
  ```

- **Port opens but esptool cannot synchronize.** Check the selected port and follow the manufacturer's instructions for entering download mode on the actual board. Follow its reset sequence to return to normal startup.
- **A Wi-Fi change failed.** Run `uv run --project skill/esp-agentkit --locked python skill/esp-agentkit/scripts/provision.py --port PORT --keep-config` to use the previously saved configuration and its WebREPL password. If only a pending configuration exists, the installer retries it instead. This option preserves the existing management password and still requires it for verification; it is not a forgotten-password reset command.
- **Existing customized startup file.** Setup interrupts the running interpreter and stops before writing any files if it finds an unfamiliar `boot.py` or `main.py`. Back up and move the custom files aside over USB before repeating setup. Press the reset button to resume the currently installed startup program if you leave setup.
- **`config_invalid` or `wifi_failed`.** Inspect the board configuration and 2.4 GHz network settings. Repeat USB setup if the saved configuration needs to change.
- **`webrepl_failed`.** Check the installed firmware build against the USB installer's requirements and verify that the listener can start on port 8266.

## Verify on the selected board

For USB detection, first run `detect --list` and confirm that the running application is not interrupted. Run `detect` with one USB candidate and compare the reported chip and flash capacity with the actual hardware. Confirm normal startup after the probe. With multiple candidates, expect exit code 5 and no active probe until `--port` is supplied. Record manual download-mode requirements and any missing fields. These checks do not establish firmware or deployment compatibility; complete the checks below for the selected setup.

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

6. After a probe, run `restart --project PROJECT --manifest MANIFEST_ID` for the
   installed release. Expect the same manifest, a reset acknowledgment and a
   login-only reconnect. Check the pre-reset diagnostic and final restart
   records, then ask the developer whether the application actually resumed.
   A different expected manifest must refuse reset. Keep incomplete deployment
   and storage-failure tests in a backed-up test project with a recovery path.
7. Test `--project-dir` using a new destination. On an already configured board,
   use `--keep-config` to preserve networking. Verify the generated project's
   identity, firmware notes and starter, then test `--save-project-only` with a
   second destination. Neither starter is deployed by project creation. An
   existing destination must fail before opening the device.
8. On a recoverable test application, induce a known startup exception. Read it
   through `info`, confirm `restart` retains it before reset, then restore the
   retained working release. Check that the old exception clears and obtain a
   fresh behavioral observation. Test independent power and a full project
   handoff separately; login or USB reset is not evidence of either.
