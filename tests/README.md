# Core workflow checks

Test the development flow and its important failure/recovery paths. Extend an
existing scenario when behavior changes; avoid tests for each helper, argument
variant or repeated assertion. Use temporary projects and synthetic credentials,
never real device-project configuration.

Run the affected file from the repository root:

```sh
uv run --project skill/esp-agentkit --locked python -m unittest discover -s tests -p test_project_cli.py -v
```

For changes across workflows or shared infrastructure, run the full suite once:

```sh
uv run --project skill/esp-agentkit --locked python -m unittest discover -s tests -v
```

| Workflow | Files | Essential outcomes |
| --- | --- | --- |
| Detect and select | `test_detection.py`, `test_detect_espressif.py` | List interfaces, select one device, retain partial results and release the port on failure. |
| Create and continue projects | `test_provision.py`, `test_project_cli.py`, `test_native_projects.py` | Save verified setup, discover/copy projects, refuse ambiguous or wrong targets; keep native projects independent of WebREPL and SDK installation. |
| Probe and deploy MicroPython | `test_cli.py`, `test_transport.py`, `test_deployment.py` | Preserve probe output/errors, respect ESP8266 flow control, refuse unsafe writes and recover from interrupted transfers. |
| Restart and accept | `test_restart.py`, `test_runtime.py` | Retain startup diagnostics, recover an installed release and require version-specific developer observations. |
| Build native firmware | `test_native_build.py` | Capture inputs and artifacts, hand off/rebuild, refuse changed inputs and altered archives. |
| Flash, observe and accept native firmware | `test_native_device.py` | Refuse wrong devices/layouts, connect flash and running evidence, invalidate stale evidence and retain cancellation records. |
| Coordinate operations | `test_operations.py` | Refuse concurrent ownership, clean up cancelled/timed-out process trees and reacquire resources. |

The desktop suite uses transport/device substitutes; native builds use a
substitute SDK. Process coordination uses real child processes and OS locks.
These checks do not establish real-board behavior or cross-platform support.
Windows native process execution is not implemented; those process checks are
skipped there. On hardware, follow the [Skill](../skill/esp-agentkit/SKILL.md)
and retain versioned observations in the device project. Public progress and
remaining scope belong in the [roadmap](../docs/roadmap.md#验证记录与待办).

## Optional MicroPython interpreter check

Run this only when changing interpreter-dependent startup or filesystem
behavior. It uses MicroPython v1.29.0 with a RAM-backed littlefs2 filesystem;
ordinary metadata or CLI changes only need the desktop workflows above.

```sh
git clone --depth 1 --branch v1.29.0 https://github.com/micropython/micropython.git /tmp/agentkit-micropython
uv run --project skill/esp-agentkit --locked make -C /tmp/agentkit-micropython/ports/unix -j2 \
  FROZEN_MANIFEST= MICROPY_PY_BTREE=0 MICROPY_PY_SSL=0 MICROPY_PY_FFI=0 \
  MICROPY_VFS_FAT=0 MICROPY_VFS_LFS1=0 MICROPY_VFS_LFS2=1
uv run --project skill/esp-agentkit --locked \
  python tests/micropython.py --micropython /tmp/agentkit-micropython/ports/unix/build-standard/micropython
```

An existing compatible interpreter can be supplied with `--micropython`.
Missing capabilities or failed assertions produce a nonzero exit.
