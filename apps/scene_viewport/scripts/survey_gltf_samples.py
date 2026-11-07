"""Try to load every glTF sample asset and report what the importer misses.

Point it at a checkout of KhronosGroup/glTF-Sample-Assets (the ``Models``
folder). For each model it picks one variant (plain ``glTF`` first, then
``glTF-Binary``, then ``glTF-Embedded``), reads the glTF JSON to list the
features the file uses, runs :func:`load_model` and :func:`import_objects`
headless (no GPU), and writes a Markdown report: a status table per model
and a feature table that says how often each feature appears and how many
of those models fail to load. Use the feature table to pick what to support
next.

Usage::

    uv run --package scene-viewport python \\
        apps/scene_viewport/scripts/survey_gltf_samples.py \\
        C:/path/to/glTF-Sample-Assets/Models report.md [--variants glTF,glTF-Binary]
"""

from __future__ import annotations

import argparse
import json
import logging
import struct
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from scene_viewport.mesh.importer import ModelImportError, load_model
from scene_viewport.model_import import import_conversion, import_objects

_DEFAULT_VARIANTS = ("glTF", "glTF-Binary", "glTF-Embedded")
_PRIMITIVE_MODES = {
    0: "points",
    1: "lines",
    2: "line loop",
    3: "line strip",
    5: "triangle strip",
    6: "triangle fan",
}


@dataclass
class Survey:
    """Result for one sample model."""

    name: str
    folder: str
    tags: list[str]
    variant: str
    file: Path | None
    features: list[str] = field(default_factory=list)
    status: str = "skipped"
    detail: str = ""
    nodes: int = 0
    meshes: int = 0
    textured: int = 0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _glb_json(path: Path) -> object:
    with path.open("rb") as stream:
        header = stream.read(20)
        if len(header) < 20 or header[:4] != b"glTF":
            msg = "not a GLB file"
            raise ValueError(msg)
        length, kind = struct.unpack("<II", header[12:20])
        if kind != 0x4E4F534A:
            msg = "GLB has no JSON chunk"
            raise ValueError(msg)
        return json.loads(stream.read(length).decode("utf-8"))


def _document(path: Path) -> dict[str, object]:
    document = (
        _glb_json(path)
        if path.suffix.lower() == ".glb"
        else json.loads(path.read_text(encoding="utf-8"))
    )
    return document if isinstance(document, dict) else {}


def _list(document: dict[str, object], key: str) -> list[dict[str, object]]:
    value = document.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def gltf_features(document: dict[str, object]) -> list[str]:
    """Name the glTF features a document uses that an importer must handle.

    Args:
        document: Parsed glTF JSON.

    Returns:
        Sorted feature names, such as ``"animations"`` or
        ``"ext: KHR_materials_transmission"``.
    """
    features: set[str] = set()
    for key in ("extensionsUsed", "extensionsRequired"):
        value = document.get(key)
        if isinstance(value, list):
            prefix = "ext required: " if key == "extensionsRequired" else "ext: "
            features.update(
                f"{prefix}{name}" for name in value if isinstance(name, str)
            )
    if len(_list(document, "scenes")) > 1:
        features.add("multiple scenes")
    for key in ("animations", "skins", "cameras"):
        if _list(document, key):
            features.add(key)
    for mesh in _list(document, "meshes"):
        for primitive in _list(mesh, "primitives"):
            mode = primitive.get("mode", 4)
            if isinstance(mode, int) and mode != 4:
                features.add(f"primitive mode: {_PRIMITIVE_MODES.get(mode, mode)}")
            if primitive.get("targets"):
                features.add("morph targets")
            attributes = primitive.get("attributes")
            if isinstance(attributes, dict):
                if "indices" not in primitive:
                    features.add("no indices")
                if "NORMAL" not in attributes:
                    features.add("no normals")
                if "TANGENT" in attributes:
                    features.add("tangents")
                if "TEXCOORD_1" in attributes:
                    features.add("second UV set")
                if "COLOR_0" in attributes:
                    features.add("vertex colors")
                if "JOINTS_0" in attributes:
                    features.add("skinning attributes")
    for accessor in _list(document, "accessors"):
        if "sparse" in accessor:
            features.add("sparse accessors")
            break
    for material in _list(document, "materials"):
        alpha = material.get("alphaMode")
        if isinstance(alpha, str) and alpha != "OPAQUE":
            features.add(f"alpha mode: {alpha}")
        if material.get("doubleSided"):
            features.add("double sided")
        if "occlusionTexture" in material:
            features.add("occlusion map")
        if "emissiveTexture" in material or material.get("emissiveFactor"):
            features.add("emissive")
        if "normalTexture" in material:
            features.add("normal map")
        pbr = material.get("pbrMetallicRoughness")
        if isinstance(pbr, dict) and "metallicRoughnessTexture" in pbr:
            features.add("metal/rough map")
    for image in _list(document, "images"):
        mime = image.get("mimeType")
        uri = image.get("uri")
        kind = ""
        if isinstance(mime, str):
            kind = mime.split("/")[-1]
        elif isinstance(uri, str) and not uri.startswith("data:"):
            kind = Path(uri).suffix.lstrip(".").lower()
        if kind and kind not in ("png", "jpeg", "jpg"):
            features.add(f"image: {kind}")
    for sampler in _list(document, "samplers"):
        if sampler.get("wrapS", 10497) != 10497 or sampler.get("wrapT", 10497) != 10497:
            features.add("texture wrap: clamp/mirror")
    for texture in _list(document, "textures"):
        if "source" not in texture:
            features.add("texture without core source")
    return sorted(features)


def _pick_variant(folder: Path, variants: tuple[str, ...]) -> tuple[str, Path | None]:
    for variant in variants:
        candidate = folder / variant
        if not candidate.is_dir():
            continue
        files = sorted(candidate.glob("*.gltf")) + sorted(candidate.glob("*.glb"))
        if files:
            return variant, files[0]
    present = [
        d.name for d in folder.iterdir() if d.is_dir() and d.name.startswith("glTF")
    ]
    return ", ".join(present) or "none", None


def survey_model(folder: Path, variants: tuple[str, ...]) -> Survey:
    """Load one sample model folder and describe the outcome."""
    name = folder.name
    tags: list[str] = []
    metadata = folder / "metadata.json"
    if metadata.is_file():
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            name = str(payload.get("name") or name)
            tags = [str(tag) for tag in payload.get("tags", [])]
        except (OSError, json.JSONDecodeError):
            pass
    variant, file = _pick_variant(folder, variants)
    result = Survey(
        name=name, folder=folder.name, tags=tags, variant=variant, file=file
    )
    if file is None:
        result.status = "no variant"
        result.detail = f"only {variant}"
        return result
    try:
        result.features = gltf_features(_document(file))
    except (OSError, ValueError) as exc:
        result.features = [f"unreadable JSON: {exc}"]
    started = time.perf_counter()
    try:
        model = load_model(file)
        imported = import_objects(
            model, set(), conversion=import_conversion(file, "auto", "auto")
        )
    except ModelImportError as exc:
        result.status = "fail"
        result.detail = str(exc)
    except Exception as exc:  # The report must list any crash, whatever its type.
        result.status = "crash"
        result.detail = f"{type(exc).__name__}: {exc}"
    else:
        result.status = "ok"
        result.nodes = len(imported.objects.transforms)
        result.meshes = len(imported.objects.meshes)
        result.textured = len(model.maps)
        result.warnings = list(model.warnings)
    result.seconds = time.perf_counter() - started
    return result


def write_report(results: list[Survey], out: Path, models_dir: Path) -> None:
    """Write the Markdown report."""
    counts = Counter(result.status for result in results)
    feature_total: Counter[str] = Counter()
    feature_failing: Counter[str] = Counter()
    for result in results:
        for feature in result.features:
            feature_total[feature] += 1
            if result.status in ("fail", "crash"):
                feature_failing[feature] += 1
    lines = [
        "# glTF sample asset survey",
        "",
        f"Source: `{models_dir}`. {len(results)} models.",
        "",
        "| Status | Models |",
        "|---|---|",
        *(f"| {status} | {count} |" for status, count in sorted(counts.items())),
        "",
        "## Features by frequency",
        "",
        "Models that use the feature, and how many of those fail to load. "
        "Features that load are still only partly supported when the viewer "
        "ignores them (animation, alpha, occlusion, cameras, lights).",
        "",
        "| Feature | Models | Failing |",
        "|---|---|---|",
    ]
    for feature, total in sorted(feature_total.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {feature} | {total} | {feature_failing[feature]} |")
    lines += [
        "",
        "## Models",
        "",
        "| Model | Tags | Variant | Status | Nodes/meshes/textured | Time | Features "
        "| Detail |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for result in sorted(results, key=lambda r: (r.status != "ok", r.name.lower())):
        features = ", ".join(result.features)
        detail = result.detail or "; ".join(result.warnings)
        detail = detail.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {result.name} | {', '.join(result.tags)} | {result.variant} | "
            f"{result.status} | {result.nodes}/{result.meshes}/{result.textured} | "
            f"{result.seconds:.1f}s | {features} | {detail} |"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run the survey from the command line."""
    parser = argparse.ArgumentParser(
        description="Load every glTF sample asset and report what the importer misses."
    )
    parser.add_argument("models", type=Path, help="glTF-Sample-Assets/Models folder")
    parser.add_argument("report", type=Path, help="Markdown report to write")
    parser.add_argument(
        "--variants",
        default=",".join(_DEFAULT_VARIANTS),
        help="variant folders to try, in order (comma separated)",
    )
    parser.add_argument("--only", default="", help="substring filter on folder names")
    args = parser.parse_args(argv)
    logging.getLogger("trimesh").setLevel(logging.CRITICAL)
    variants = tuple(part.strip() for part in args.variants.split(",") if part.strip())
    folders = sorted(
        folder
        for folder in args.models.iterdir()
        if folder.is_dir()
        and (folder / "metadata.json").is_file()
        and args.only.lower() in folder.name.lower()
    )
    results: list[Survey] = []
    for index, folder in enumerate(folders, start=1):
        result = survey_model(folder, variants)
        results.append(result)
        sys.stderr.write(
            f"[{index}/{len(folders)}] {result.folder}: {result.status} "
            f"({result.seconds:.1f}s) {result.detail}\n"
        )
    write_report(results, args.report, args.models)
    sys.stderr.write(f"wrote {args.report}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
