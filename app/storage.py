"""Writing the files in /data so a crash can't leave half of one behind."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to a temp file beside ``path``, then rename it into place.

    The rename is atomic on one filesystem, so a reader sees the old file or the
    new one, never a truncated mix. Raises ``OSError``; each store decides what
    a failed write means for the request.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.stem}-", suffix=path.suffix
    )
    try:
        with os.fdopen(handle, "w") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    except BaseException:
        Path(temp_path).unlink(missing_ok=True)
        raise
