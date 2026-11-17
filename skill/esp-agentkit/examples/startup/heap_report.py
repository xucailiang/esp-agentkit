import gc


def free_bytes():
    gc.collect()
    return gc.mem_free()
