"""Stream registered audio as a ZIP without staging another copy on disk."""
from collections.abc import AsyncIterator, Iterator
import logging
import os
from pathlib import Path
import zipfile

from starlette.concurrency import iterate_in_threadpool

LOG = logging.getLogger(__name__)
CHUNK_BYTES = 1024 * 1024


class _ZipBuffer:
    """A non-seekable sink drained after each audio block."""

    def __init__(self):
        self.position = 0
        self.blocks = []

    def write(self, data: bytes) -> int:
        self.blocks.append(data)
        self.position += len(data)
        return len(data)

    def tell(self) -> int:
        return self.position

    def flush(self):
        pass

    def drain(self) -> bytes:
        data = b"".join(self.blocks)
        self.blocks.clear()
        return data


def _zip_chunks(records: list[dict]) -> Iterator[bytes]:
    sink = _ZipBuffer()
    try:
        with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED,
                             allowZip64=True, strict_timestamps=False) as archive:
            for record in records:
                path = Path(record["path"])
                info = zipfile.ZipInfo.from_file(path, record["archive_name"], strict_timestamps=False)
                with path.open("rb") as source:
                    before = os.fstat(source.fileno())
                    if (before.st_size, before.st_mtime_ns) != (record["size"], record["mtime_ns"]):
                        raise OSError("An audio export changed before downloading; select it again")
                    with archive.open(info, "w") as member:
                        yield sink.drain()
                        while block := source.read(CHUNK_BYTES):
                            member.write(block)
                            yield sink.drain()
                        after = os.fstat(source.fileno())
                        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                            raise OSError("An audio export changed during downloading; select it again")
                    yield sink.drain()
        yield sink.drain()
    except Exception:
        LOG.exception("Audio bundle transfer failed")
        raise
    finally:
        sink.blocks.clear()


async def stream_zip(records: list[dict]) -> AsyncIterator[bytes]:
    iterator = _zip_chunks(records)
    try:
        async for block in iterate_in_threadpool(iterator):
            if block:
                yield block
    finally:
        iterator.close()
