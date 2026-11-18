# Local regression checks

From the repository root, run the desktop suite with Python 3.12 and the Skill's pinned dependencies.

```sh
uv run --python 3.12 --with-requirements skill/esp-agentkit/requirements.txt python -m unittest discover -s tests -v
```

The suite uses `unittest`, temporary files and synthetic credentials. It executes the CLI entry in-process and calls the production deployment and startup code with local I/O adapters.

| File | Regression |
| --- | --- |
| `test_detection.py` | Serial candidate selection, explicit ports, probe timeouts and structured CLI results |
| `test_detect_espressif.py` | Esptool ROM identification, partial results and connection cleanup without hardware access |
| `test_cli.py` | Exit codes, stdout and traceback preservation, cancellation and input errors |
| `test_transport.py` | ESP8266 flow control, UTF-8 transfer and storage error translation |
| `test_deployment.py` | Device identity checks, corrupted uploads followed by retry, capacity refusal before writes |
| `test_runtime.py` | Startup exceptions, bounded diagnostics and the `info` response |

The ESP8266 peer models a 128-byte flow-control window and a 255-byte receive capacity. A negative control bypasses the initial credit limit and must reproduce data loss. Deployment recovery corrupts actual staged bytes, retains the partially updated files, and retries the complete application.

Detection tests substitute serial inventory and ROM communication. They do not open a real device or establish hardware compatibility. On hardware, separately verify interface-only discovery, single-device identification, explicit selection with multiple candidates, and normal startup after the detection reset. Record the chip, flash capacity and any manual download-mode steps; keep runtime and deployment checks separate. See [USB detection](../skill/esp-agentkit/SKILL.md#detect-a-connected-device).

## Unix MicroPython integration

This explicit check runs the production startup code and deployment capacity check on MicroPython v1.29.0 with a RAM-backed littlefs2 filesystem. The desktop suite adapts MicroPython-only APIs for CPython. This group checks their actual interpreter behavior.

On macOS or Linux, prepare the interpreter with Git, a C compiler and Make available. Run these commands from the repository root.

```sh
git clone --depth 1 --branch v1.29.0 https://github.com/micropython/micropython.git /tmp/agentkit-micropython
uv run --python 3.12 make -C /tmp/agentkit-micropython/ports/unix -j2 \
  FROZEN_MANIFEST= MICROPY_PY_BTREE=0 MICROPY_PY_SSL=0 MICROPY_PY_FFI=0 \
  MICROPY_VFS_FAT=0 MICROPY_VFS_LFS1=0 MICROPY_VFS_LFS2=1
uv run --python 3.12 --with-requirements skill/esp-agentkit/requirements.txt \
  python tests/micropython.py --micropython /tmp/agentkit-micropython/ports/unix/build-standard/micropython
```

An existing compatible interpreter can be supplied with `--micropython`. Missing capabilities or a failed assertion produce a nonzero exit. Wireless connectivity, power-loss recovery and peripheral behavior still require hardware validation. See the [hardware checks](../skill/esp-agentkit/SKILL.md#verify-on-the-selected-board) and [acceptance plan (Chinese)](../docs/architecture-and-development.md#分阶段开发与验收).
