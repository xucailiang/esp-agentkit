"""Boot a Unix MicroPython interpreter with a five-block RAM filesystem."""

import sys
import vfs

sys.path.insert(0, sys.argv[1])
import agentkit_runtime


class Device:
    @staticmethod
    def unique_id():
        return b"desktop"


class MemoryBlocks:
    def __init__(self):
        self.data = bytearray(5 * 4096)

    def readblocks(self, block, buffer, offset=0):
        start = block * 4096 + offset
        buffer[:] = self.data[start:start + len(buffer)]

    def writeblocks(self, block, buffer, offset=0):
        start = block * 4096 + offset
        self.data[start:start + len(buffer)] = buffer

    def ioctl(self, operation, argument):
        if operation == 4:
            return 5
        if operation == 5:
            return 4096
        if operation == 6:
            start = argument * 4096
            self.data[start:start + 4096] = b"\xff" * 4096
        return 0


sys.modules["machine"] = Device
blocks = MemoryBlocks()
vfs.VfsLfs2.mkfs(blocks)
vfs.umount("/")
vfs.mount(vfs.VfsLfs2(blocks), "/")
print("ESP_AGENTKIT_FIXTURE_READY")
