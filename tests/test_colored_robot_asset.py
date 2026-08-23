from __future__ import annotations

import json
import struct
from pathlib import Path

ASSET = (
    Path(__file__).parents[1]
    / "pql-a00_description"
    / "meshes"
    / "PQL01_002_assy_colored.glb"
)


def _read_glb_json(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        magic, version, declared_length = struct.unpack("<4sII", stream.read(12))
        json_length, chunk_type = struct.unpack("<I4s", stream.read(8))
        document = json.loads(stream.read(json_length))

    assert magic == b"glTF"
    assert version == 2
    assert declared_length == path.stat().st_size
    assert chunk_type == b"JSON"
    return document


def test_colored_robot_asset_preserves_assembly_links_and_materials() -> None:
    document = _read_glb_json(ASSET)
    names = [node.get("name", "") for node in document["nodes"]]

    assert "PQL01 assy" in names
    assert len([name for name in names if name.startswith("PQL-LG00")]) == 4
    assert len([name for name in names if name.startswith("PQL01-LU00-A1")]) == 4
    assert len([name for name in names if name.startswith("PQL01-LD00-A1")]) == 4
    assert len([name for name in names if name.startswith("Leg r shaft")]) == 4
    assert len([name for name in names if name.startswith("Leg under shaft")]) == 4
    assert len([name for name in names if name.startswith("leg_cap_gom3")]) == 4
    assert len([name for name in names if name.startswith("s-23-44 rod assy")]) == 8
    assert len([name for name in names if name.startswith("s2-23-45 tube")]) == 8
    assert len([name for name in names if name.startswith("M5rodend_body")]) == 12

    # The AP214 source has component-level display styles. Requiring several
    # materials prevents a future conversion from silently degrading to a
    # single uncoloured mesh.
    materials = document["materials"]
    colors = {
        tuple(material["pbrMetallicRoughness"]["baseColorFactor"])
        for material in materials
    }
    assert len(materials) >= 10
    assert len(colors) >= 10
