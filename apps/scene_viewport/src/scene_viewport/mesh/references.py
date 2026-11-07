"""Find the files a model file refers to: buffers, materials, and images."""

from __future__ import annotations

import functools
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

#: Suffixes checked as images when looking for unreadable textures.
_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
)
#: OBJ material statements whose argument is a file.
_MTL_MAPS = frozenset({"bump", "disp", "decal", "norm", "refl"})
#: MTL map options and how many values each takes. ``-o``, ``-s``, and ``-t``
#: take one to three numbers, so they are handled by reading numbers.
_MTL_OPTION_ARITY = {
    "-blendu": 1,
    "-blendv": 1,
    "-boost": 1,
    "-mm": 2,
    "-texres": 1,
    "-clamp": 1,
    "-bm": 1,
    "-imfchan": 1,
    "-type": 1,
    "-cc": 1,
}
_MTL_VECTOR_OPTIONS = frozenset({"-o", "-s", "-t"})
_GLB_MAGIC = b"glTF"
_GLB_JSON_CHUNK = 0x4E4F534A

#: ``(path, mtime_ns, size)`` for a file, ``(path, -1, -1)`` when missing.
FileStamp = tuple[str, int, int]


@dataclass(frozen=True, slots=True)
class _Scan:
    """What one pass over a model file found."""

    files: tuple[Path, ...]
    remote: tuple[str, ...]


def referenced_files(path: Path) -> tuple[Path, ...]:
    """Return the external files ``path`` refers to.

    glTF (``.gltf`` and ``.glb``) buffer and image URIs and OBJ material
    libraries with their texture maps are listed. Embedded data, remote
    URIs (see :func:`remote_references`), and other formats have none.

    Each material library is scanned on its own timestamp, so a texture
    added to an edited ``.mtl`` shows up without touching the ``.obj``.

    Args:
        path: Model file.

    Returns:
        Referenced paths, in file order and without duplicates. They may not
        exist.
    """
    scan = _scan_file(path)
    if scan is None:
        return ()
    files = list(scan.files)
    for file in scan.files:
        if file.suffix.lower() == ".mtl":
            files.extend(_scan_library(file))
    return tuple(dict.fromkeys(files))


def remote_references(path: Path) -> tuple[str, ...]:
    """Return glTF buffer and image URIs with a scheme, such as ``https:``.

    These are not fetched, so a texture behind one is never shown.

    Args:
        path: Model file.

    Returns:
        The URIs in file order, without duplicates.
    """
    scan = _scan_file(path)
    return () if scan is None else scan.remote


def source_signature(path: Path) -> tuple[FileStamp, ...]:
    """Stamp ``path`` and the files it refers to, to notice any edit.

    Args:
        path: Model file.

    Returns:
        One ``(path, mtime_ns, size)`` per file, the model file first.
    """
    return tuple(file_stamp(file) for file in (path, *referenced_files(path)))


def reference_problems(path: Path) -> list[str]:
    """Describe referenced files that are missing, unreadable, or remote.

    Args:
        path: Model file.

    Returns:
        One message per problem, such as ``"car.obj: missing paint.png"``.
    """
    problems: list[str] = []
    for file in referenced_files(path):
        if not file.is_file():
            problems.append(f"{path.name}: missing {file.name}")
            continue
        if file.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        try:
            with Image.open(file) as image:
                image.verify()
        except Exception:
            # Pillow raises many types for truncated or foreign data.
            problems.append(f"{path.name}: cannot read image {file.name}")
    for uri in remote_references(path):
        problems.append(f"{path.name}: remote file not loaded {uri}")
    return problems


def file_stamp(file: Path) -> FileStamp:
    """Return ``(path, mtime_ns, size)`` for ``file``, or ``-1`` values if missing.

    Args:
        file: Any path.

    Returns:
        The stamp, which changes whenever the file is saved again.
    """
    try:
        stat = file.stat()
    except OSError:
        return (str(file), -1, -1)
    return (str(file), stat.st_mtime_ns, stat.st_size)


def _scan_file(path: Path) -> _Scan | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return _scan(path.resolve(), stat.st_mtime_ns, stat.st_size)


def _scan_library(library: Path) -> tuple[Path, ...]:
    try:
        stat = library.stat()
    except OSError:
        return ()
    return _mtl_scan(library, stat.st_mtime_ns, stat.st_size)


@functools.lru_cache(maxsize=64)
def _scan(path: Path, _mtime_ns: int, _size: int) -> _Scan:
    """Scan ``path`` once per version; the stat values only key the cache."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".gltf":
            return _gltf_scan(path, json.loads(path.read_text(encoding="utf-8")))
        if suffix == ".glb":
            return _gltf_scan(path, _glb_json(path))
        if suffix == ".obj":
            return _Scan(files=tuple(dict.fromkeys(_obj_libraries(path))), remote=())
    except (OSError, ValueError):
        # Unreadable or malformed: loading reports the real error.
        pass
    return _Scan(files=(), remote=())


@functools.lru_cache(maxsize=64)
def _mtl_scan(library: Path, _mtime_ns: int, _size: int) -> tuple[Path, ...]:
    """Scan one material library per version; the stat values key the cache."""
    try:
        return tuple(dict.fromkeys(_mtl_files(library)))
    except OSError:
        return ()


def _gltf_scan(path: Path, document: object) -> _Scan:
    if not isinstance(document, dict):
        return _Scan(files=(), remote=())
    files: list[Path] = []
    remote: list[str] = []
    for section in ("buffers", "images"):
        entries = document.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            uri = entry.get("uri") if isinstance(entry, dict) else None
            if not isinstance(uri, str) or not uri:
                continue
            scheme = urlsplit(uri).scheme.lower()
            if scheme == "data":
                continue
            if len(scheme) > 1:
                # A one-letter scheme is a Windows drive, not a URL.
                remote.append(uri)
                continue
            files.append(path.parent / unquote(uri))
    return _Scan(files=tuple(dict.fromkeys(files)), remote=tuple(dict.fromkeys(remote)))


def _glb_json(path: Path) -> object:
    """Return the JSON chunk of a binary glTF."""
    with path.open("rb") as stream:
        header = stream.read(20)
        if len(header) < 20 or header[:4] != _GLB_MAGIC:
            msg = "not a GLB file"
            raise ValueError(msg)
        length, kind = struct.unpack("<II", header[12:20])
        if kind != _GLB_JSON_CHUNK:
            msg = "GLB has no JSON chunk"
            raise ValueError(msg)
        return json.loads(stream.read(length).decode("utf-8"))


def _obj_libraries(path: Path) -> list[Path]:
    """Return the material libraries an OBJ names on its ``mtllib`` lines.

    The OBJ format allows several libraries on one line, separated by
    spaces, but file names may contain spaces too. The whole remainder of
    the line wins when a file by that name exists; otherwise each word is a
    library.
    """
    files: list[Path] = []
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            words = line.split()
            if len(words) < 2 or words[0] != "mtllib":
                continue
            whole = path.parent / line.split(None, 1)[1].strip()
            if whole.is_file() or len(words) == 2:
                files.append(whole)
            else:
                files.extend(path.parent / word for word in words[1:])
    return files


def _mtl_files(library: Path) -> list[Path]:
    files: list[Path] = []
    with library.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            words = line.split()
            if len(words) < 2:
                continue
            statement = words[0].lower()
            if statement.startswith("map_") or statement in _MTL_MAPS:
                name = _mtl_map_file(words[1:])
                if name:
                    files.append(library.parent / name)
    return files


def _mtl_map_file(words: list[str]) -> str:
    """Return the file name of a map statement, after its ``-option`` values.

    Options come first (``-bm 1``, ``-o 0 0.5``); whatever is left is the
    file name, joined with single spaces so names with spaces survive.
    """
    index = 0
    while index < len(words) and words[index].startswith("-"):
        option = words[index].lower()
        index += 1
        if option in _MTL_VECTOR_OPTIONS:
            while index < len(words) and _is_number(words[index]):
                index += 1
        else:
            index += _MTL_OPTION_ARITY.get(option, 1)
    return " ".join(words[index:])


def _is_number(word: str) -> bool:
    try:
        float(word)
    except ValueError:
        return False
    return True
