"""One embedded-index application writer per database, including across processes."""

import os
from contextlib import contextmanager
from pathlib import Path

# Platform lock modules are mutually exclusive and must remain conditional.
if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextmanager
def embedded_writer_lock(database):
    path = Path(database).with_suffix(".server.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            if path.stat().st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError(
                    ("An application already owns this embedded database. Use one worker per data directory.")
                ) from None
        else:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(
                    ("An application already owns this embedded database. Use one worker per data directory.")
                ) from None
        yield
    finally:
        stream.close()
