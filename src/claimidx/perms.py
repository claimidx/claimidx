"""Private on-disk state: claimidx dirs are 0700 and every file in them 0600, from the moment it exists.

Files are created with ``os.open(O_CREAT | O_EXCL, 0o600)`` (a temp file swapped in with
``os.replace`` when the content is rewritten), so there is no window in which a config, key,
token, outbox or index is readable by other users. Existing installs are tightened on the
first run that touches them (``secure_home``).

POSIX only: on Windows the mode bits are not an access control, so every chmod here is a no-op.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600
POSIX = os.name != "nt"

_SECURED: set[str] = set()


def home_dir() -> Path:
    """The per-user claimidx dir (~/.claimidx)."""
    return Path.home() / ".claimidx"


def _chmod(path: str | os.PathLike[str], mode: int) -> None:
    if not POSIX:
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _loose(mode: int, want: int) -> bool:
    return stat.S_IMODE(mode) != want


def tighten_file(path: str | os.PathLike[str]) -> None:
    """chmod an existing regular file (never a symlink target) to 0600 when it is anything else."""
    if not POSIX:
        return
    try:
        st = os.lstat(path)
    except OSError:
        return
    if stat.S_ISREG(st.st_mode) and _loose(st.st_mode, FILE_MODE):
        _chmod(path, FILE_MODE)


def tighten_dir(path: str | os.PathLike[str]) -> None:
    """chmod an existing dir (never a symlink target) to 0700 when it is anything else."""
    if not POSIX:
        return
    try:
        st = os.lstat(path)
    except OSError:
        return
    if stat.S_ISDIR(st.st_mode) and _loose(st.st_mode, DIR_MODE):
        _chmod(path, DIR_MODE)


def secure_tree(root: str | os.PathLike[str]) -> None:
    """Tighten a claimidx-owned tree: the dir and every subdir 0700, every regular file 0600.

    Symlinks are left alone (neither followed nor chmod'ed). Safe to call on a missing dir.
    """
    if not POSIX:
        return
    root = Path(root)
    try:
        if not stat.S_ISDIR(os.lstat(root).st_mode):
            return
    except OSError:
        return
    tighten_dir(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames:
            tighten_dir(os.path.join(dirpath, name))
        for name in filenames:
            tighten_file(os.path.join(dirpath, name))


def secure_home(force: bool = False) -> None:
    """Once per process: tighten an existing ~/.claimidx (older installs wrote it 0755/0644). Never raises."""
    if not POSIX:
        return
    try:
        root = home_dir()
        key = str(root)
        if key in _SECURED and not force:
            return
        _SECURED.add(key)
        secure_tree(root)
    except Exception:
        pass


def _is_home(path: Path) -> bool:
    try:
        return os.path.abspath(path) == os.path.abspath(home_dir())
    except Exception:
        return False


def private_dir(path: str | os.PathLike[str]) -> Path:
    """``mkdir -p`` where every dir this call creates is 0700.

    An existing ~/.claimidx is tightened (once per process); any other existing dir is left as it is,
    because it may be a dir the user chose (``--db``, ``CLAIMIDX_CONFIG``) that claimidx does not own.
    """
    p = Path(path)
    if p.is_dir():
        if _is_home(p):
            secure_home()
        return p
    missing: list[Path] = []
    q = p
    while not q.exists():
        missing.append(q)
        if q.parent == q:
            break
        q = q.parent
    for d in reversed(missing):
        try:
            os.mkdir(d, DIR_MODE)
        except FileExistsError:
            continue
        _chmod(d, DIR_MODE)  # exact 0700 whatever the umask
    if _is_home(p):
        secure_home()
    return p


_BINARY = getattr(os, "O_BINARY", 0)


def private_file(path: str | os.PathLike[str]) -> Path:
    """Make sure ``path`` exists as a 0600 file without ever being wider (e.g. before sqlite opens it)."""
    p = Path(path)
    private_dir(p.parent)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, FILE_MODE)
    except FileExistsError:
        tighten_file(p)
        return p
    os.close(fd)
    _chmod(p, FILE_MODE)
    return p


def write_private(path: str | os.PathLike[str], text: str, *, newline: str | None = None) -> Path:
    """Atomically replace ``path`` with ``text``; the file is 0600 from creation (no wider window).

    Writes a sibling temp file opened ``O_CREAT | O_EXCL`` with mode 0600, then ``os.replace``s it over
    the target, so readers never see a half-written file either.
    """
    p = Path(path)
    private_dir(p.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY
    for _ in range(64):
        tmp = p.with_name(f".{p.name}.{secrets.token_hex(6)}.tmp")
        try:
            fd = os.open(tmp, flags, FILE_MODE)
            break
        except FileExistsError:
            continue
    else:  # pragma: no cover - 64 random collisions
        raise FileExistsError(str(p) + ".*.tmp")
    try:
        if POSIX:
            try:
                os.fchmod(fd, FILE_MODE)
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        try:
            os.replace(tmp, p)
        except PermissionError:
            if POSIX:
                raise
            # Windows: the target is open elsewhere (no delete-share); fall back to an in-place write.
            with open(p, "w", encoding="utf-8", newline=newline) as fh:
                fh.write(text)
            os.unlink(tmp)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def append_private(path: str | os.PathLike[str], text: str) -> Path:
    """Append ``text``; a new file is created 0600, an existing loose one is tightened to 0600."""
    p = Path(path)
    private_dir(p.parent)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _BINARY, FILE_MODE)
    try:
        if POSIX:
            try:
                os.fchmod(fd, FILE_MODE)
            except OSError:
                pass
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fd = -1
            fh.write(text)
    finally:
        if fd != -1:
            os.close(fd)
    return p


def sqlite_sidecars(db: str | os.PathLike[str]) -> None:
    """chmod the db and its -wal / -shm / -journal files to 0600 (sqlite copies the db mode; old ones may be wider)."""
    if not POSIX:
        return
    base = str(db)
    for suffix in ("", "-wal", "-shm", "-journal"):
        tighten_file(base + suffix)
