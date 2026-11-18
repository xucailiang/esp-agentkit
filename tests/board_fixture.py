"""Temporary board storage with controllable capacity and upload corruption."""

import ast
from contextlib import contextmanager, nullcontext
import hashlib
from pathlib import PurePosixPath
from types import SimpleNamespace

from support import load_runtime


class FileBoard:
    def __init__(self, root):
        self.root = root
        self.root.mkdir()
        self.device_id = "01020304"
        self.runtime = load_runtime(root)
        self.block_size = 4096
        self.total_blocks = 128
        self.free_blocks = 64
        self.uploads_until_corruption = 0
        self.mutations = []
        self.restarts = 0
        self.connections = 0

    def path(self, path):
        relative = PurePosixPath(path).relative_to("/")
        if ".." in relative.parts:
            raise AssertionError("Board path escaped the temporary filesystem.")
        return self.root.joinpath(*relative.parts)

    def snapshot(self):
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes() if path.is_file() else None
            for path in self.root.rglob("*")
        }

    def fs_stat(self, path):
        return self.path(path).stat()

    def fs_listdir(self, path):
        return [
            SimpleNamespace(name=entry.name, st_mode=entry.stat().st_mode, st_size=entry.stat().st_size)
            for entry in self.path(path).iterdir()
        ]

    def fs_hashfile(self, path, algorithm):
        return hashlib.new(algorithm, self.path(path).read_bytes()).digest()

    def fs_writefile(self, path, data):
        self.mutations.append(("write", path))
        self.path(path).write_bytes(data)
        if self.uploads_until_corruption:
            self.uploads_until_corruption -= 1
            if self.uploads_until_corruption == 0:
                self.path(path).write_bytes(data + b"\x00")

    def fs_touchfile(self, path):
        self.mutations.append(("touch", path))
        self.path(path).touch()

    def fs_rmfile(self, path):
        self.mutations.append(("remove", path))
        self.path(path).unlink()

    def fs_mkdir(self, path):
        self.mutations.append(("mkdir", path))
        self.path(path).mkdir()

    def exec(self, source):
        if source in (
            "import os, machine, binascii, vfs",
            "try:\n import agentkit_runtime as _ak_runtime\nexcept ImportError:\n _ak_runtime = None",
        ):
            return
        call = ast.parse(source, mode="eval").body
        if not isinstance(call, ast.Call) or ast.unparse(call.func) != "os.rename":
            raise AssertionError(f"Unexpected board command: {source}")
        original, target = [ast.literal_eval(arg) for arg in call.args]
        self.mutations.append(("rename", original, target))
        self.path(original).replace(self.path(target))

    def eval(self, source):
        if source == "binascii.hexlify(machine.unique_id()).decode()":
            return self.device_id
        if source == "getattr(_ak_runtime, 'VERSION', None)":
            return self.runtime.VERSION
        if source == "_ak_runtime.status()":
            return self.runtime.status()
        if source == (
            "(tuple(os.statvfs('/')), any(type(fs) is getattr(vfs, 'VfsLfs2', None) "
            "for fs, mount in vfs.mount() if mount == '/'))"
        ):
            usage = (self.block_size, self.block_size, self.total_blocks, self.free_blocks, self.free_blocks)
            return usage, True
        raise AssertionError(f"Unexpected board expression: {source}")

    def restart(self):
        self.restarts += 1

    @contextmanager
    def connect(self, address, password):
        assert address == "127.0.0.1" and password == "testpass"
        self.connections += 1
        yield SimpleNamespace(filesystem=lambda: nullcontext(self))
