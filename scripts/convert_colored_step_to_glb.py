"""Convert a colored AP214 STEP assembly to a browser-ready binary glTF.

The converter uses Open Cascade XCAF rather than a plain STEP reader so that
assembly names and per-face colors survive the conversion.  It is intentionally
kept out of the runtime server dependency set; install ``cadquery-ocp`` only on
the workstation used to regenerate the web asset.

Example::

    python scripts/convert_colored_step_to_glb.py \
      "sankou/PQL01_002 assy.step" \
      pql-a00_description/meshes/PQL01_002_assy_colored.glb
"""

from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter


def convert(source: Path, output: Path, *, linear_deflection_mm: float) -> None:
    try:
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.Message import Message_ProgressRange
        from OCP.RWGltf import RWGltf_CafWriter
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
        from OCP.TColStd import TColStd_IndexedDataMapOfStringString
        from OCP.TDF import TDF_LabelSequence
        from OCP.TDocStd import TDocStd_Document
        from OCP.XCAFApp import XCAFApp_Application
        from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    except ImportError as error:
        raise SystemExit(
            "Open Cascade bindings are required. Install `cadquery-ocp` on the "
            "conversion workstation."
        ) from error

    source = source.resolve()
    output = output.resolve()
    if not source.is_file():
        raise SystemExit(f"STEP file not found: {source}")
    if linear_deflection_mm <= 0:
        raise SystemExit("--linear-deflection-mm must be greater than zero")
    output.parent.mkdir(parents=True, exist_ok=True)

    started = perf_counter()
    application = XCAFApp_Application.GetApplication_s()
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    application.NewDocument(TCollection_ExtendedString("BinXCAF"), document)

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(True)
    if not reader.Perform(str(source), document):
        raise SystemExit(f"STEP/XCAF transfer failed: {source}")
    print(f"STEP loaded in {perf_counter() - started:.1f}s", flush=True)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    if roots.Length() != 1:
        raise SystemExit(f"Expected one free assembly root, found {roots.Length()}")
    root_shape = XCAFDoc_ShapeTool.GetShape_s(roots.Value(1))

    mesh_started = perf_counter()
    mesher = BRepMesh_IncrementalMesh(
        root_shape,
        linear_deflection_mm,
        False,
        0.45,
        True,
    )
    mesher.Perform()
    if not mesher.IsDone():
        raise SystemExit("Open Cascade triangulation failed")
    print(f"Triangulated in {perf_counter() - mesh_started:.1f}s", flush=True)

    metadata = TColStd_IndexedDataMapOfStringString()
    metadata.Add(
        TCollection_AsciiString("Generator"),
        TCollection_AsciiString("PQL colored STEP converter (Open Cascade XCAF)"),
    )
    writer = RWGltf_CafWriter(TCollection_AsciiString(str(output)), True)
    writer.SetParallel(True)
    # Merge coplanar face primitives where possible. Material boundaries remain
    # separate, so the STEP colors are retained while JSON/accessor overhead is
    # reduced for browser loading.
    writer.SetMergeFaces(True)
    writer.SetSplitIndices16(False)
    writer.SetToEmbedTexturesInGlb(True)
    if not writer.Perform(document, metadata, Message_ProgressRange()):
        raise SystemExit(f"GLB export failed: {output}")

    size_mib = output.stat().st_size / (1024 * 1024)
    print(
        f"Wrote {output} ({size_mib:.1f} MiB) in {perf_counter() - started:.1f}s",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert an AP214 colored STEP assembly to a binary glTF asset."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--linear-deflection-mm",
        type=float,
        default=0.75,
        help="Tessellation chord tolerance in STEP millimetres (default: 0.75)",
    )
    args = parser.parse_args()
    convert(args.source, args.output, linear_deflection_mm=args.linear_deflection_mm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
