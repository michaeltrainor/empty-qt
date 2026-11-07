"""Find the files a model file refers to: buffers, materials, and images."""

from __future__ import annotations

import functools
import json
import struct
from pathlib import Path
from urllib.parse import unquote

from PIL import Image

#: Suffixes checked as images when looking for unreadable textures.
_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
)
#: OBJ material statements whose last token is a file.
_MTL_MAPS = frozenset({"bump", "disp", "decal", "norm", "refl"})
_GLB_MAGIC = b"glTF"
_GLB_JSON_CHUNK = 0x4E4F534A

#: ``(path, mtime_ns, size)`` for a file, ``(path, -1, -1)`` when missing.
FileStamp = tuple[str, int, int]


def referenced_files(path: Path) -> tuple[Path, ...]:
    """Return the external files ``path`` refers to.

    glTF (``.gltf`` and ``.glb``) buffer and image URIs and OBJ material
    libraries with their texture maps are listed. Embedded data and other
    formats have none.

    Args:
        path: Model file.

    Returns:
        Referenced paths, in file order and without duplicates. They may not
        exist.
    """
    try:
        stat = path.stat()
    except OSError:
        return ()
    return _referenced(path.resolve(), stat.st_mtime_ns, stat.st_size)


def source_signature(path: Path) -> tuple[FileStamp, ...]:
    """Stamp ``path`` and the files it refers to, to notice any edit.

    Args:
        path: Model file.

    Returns:
        One ``(path, mtime_ns, size)`` per file, the model file first.
    """
    return tuple(file_stamp(file) for file in (path, *referenced_files(path)))


def reference_problems(path: Path) -> list[str]:
    """Describe referenced files that are missing or unreadable images.

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


@functools.lru_cache(maxsize=64)
def _referenced(path: Path, _mtime_ns: int, _size: int) -> tuple[Path, ...]:
    """Scan ``path`` once per version; the stat values only key the cache."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".gltf":
            files = _gltf_files(path, json.loads(path.read_text(encoding="utf-8")))
        elif suffix == ".glb":
            files = _gltf_files(path, _glb_json(path))
        elif suffix == ".obj":
            files = _obj_files(path)
        else:
            files = []
    except (OSError, ValueError):
        # Unreadable or malformed: loading reports the real error.
        return ()
    return tuple(dict.fromkeys(files))


def _gltf_files(path: Path, document: object) -> list[Path]:
    if not isinstance(document, dict):
        return []
    files: list[Path] = []
    for section in ("buffers", "images"):
        entries = document.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            uri = entry.get("uri") if isinstance(entry, dict) else None
            if isinstance(uri, str) and uri and not uri.startswith("data:"):
                files.append(path.parent / unquote(uri))
    return files


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


def _obj_files(path: Path) -> list[Path]:
    files: list[Path] = []
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if line.startswith("mtllib"):
                name = line[len("mtllib") :].strip()
                if name:
                    files.append(path.parent / name)
    for library in list(files):
        if library.is_file():
            files.extend(_mtl_files(library))
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
                # Options such as ``-bm 1`` come first; the file is last.
                files.append(library.parent / words[-1])
    return files
