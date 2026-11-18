# Core workflow regression checks

Keep regression coverage focused on device development workflows and their
important failure/recovery paths. Extend an existing scenario when a change
affects that workflow; do not add another test for each helper, argument type,
error string or equivalent encoding. Each scenario should verify an observable
outcome, with representative failures rather than exhaustive permutations.

For a project-selection or handoff change, run the relevant file from the
repository root with Python 3.12 and the Skill's pinned dependencies:

```sh
uv run --python 3.12 --with-requirements skill/esp-agentkit/requirements.txt python -m unittest discover -s tests -p test_project_cli.py -v
```

Select the affected files from the table below. When a change spans several
workflows or shared infrastructure, run the complete core suite once:

```sh
uv run --python 3.12 --with-requirements skill/esp-agentkit/requirements.txt python -m unittest discover -s tests -v
```

The suite uses `unittest`, temporary files and synthetic credentials. It executes
the public CLI with local transport substitutes and calls the production
deployment and startup code. A copied project's discovery and acceptance also
run as subprocesses to exercise redirected output.

| File | Regression |
| --- | --- |
| `test_detection.py` | List interfaces → select one target → report identity or an unconfirmed probe |
| `test_detect_espressif.py` | ROM probe → report full/partial results → reset and release the serial port |
| `test_cli.py` | Stop → run a probe → preserve output, Python errors and lost-connection status |
| `test_project_cli.py` | Copy and discover a project → verify identity → deploy and retain a release → record explicit acceptance; refuse ambiguous, unconfirmed or altered releases |
| `test_transport.py` | Transfer a long Unicode probe within ESP8266 flow-control limits and execute it in the requested namespace |
| `test_deployment.py` | Refuse a wrong device; recover from a corrupt upload or insufficient space while retaining management configuration |
| `test_runtime.py` | Report a boot failure through `info` → correct the application → clear the old diagnostic |

The ESP8266 peer models a 128-byte flow-control window and a 255-byte receive capacity. A negative control bypasses the initial credit limit and must reproduce data loss. Deployment recovery corrupts actual staged bytes, retains the partially updated files, and retries the complete application.

Detection tests substitute serial inventory and ROM communication. They do not open a real device or establish hardware compatibility. On hardware, separately verify interface-only discovery, single-device identification, explicit selection with multiple candidates, and normal startup after the detection reset. Record the chip, flash capacity and any manual download-mode steps; keep runtime and deployment checks separate. See [USB detection](../skill/esp-agentkit/SKILL.md#detect-a-connected-device).

Project tests use temporary `project.json` files with synthetic plaintext
passwords. They must not read a real device project or an operating-system
credential store. Project discovery and acceptance records are local operations;
wireless authentication and actual peripheral behavior still require hardware
validation. The copied-project workflow includes a UTF-8 BOM configuration and
CP1252 output streams for discovery, deployment and acceptance; the other workflows use ordinary
UTF-8 configurations. This retains coverage of the observed encoding failures
within the handoff chain instead of repeating separate encoding matrices.
These checks do not constitute native Windows validation.
For handoff testing, copy a complete project directory, select it by
name or alias, deploy through Wi-Fi, and associate the developer's observation
with the exact deployed manifest. Test independent power separately from a
reset while the USB cable remains attached.

## Unix MicroPython integration

This optional check runs the production startup code and deployment capacity
check on MicroPython v1.29.0 with a RAM-backed littlefs2 filesystem. Run it when
changing interpreter-dependent startup or filesystem behavior, not for ordinary
project metadata, documentation or CLI formatting changes. The desktop suite
adapts MicroPython-only APIs for CPython; this check exercises their actual
interpreter behavior.

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
