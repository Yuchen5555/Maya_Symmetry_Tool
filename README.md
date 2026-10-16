# Maya Symmetry Tool

**Version 2.1 | Autodesk Maya 2026 | Python 3**

A Maya Python tool designed to fix mesh symmetry while preserving the original UV layout, topology, and vertex order.

Developed for character modeling, rigging, and facial BlendShape workflows.

## Features

- **Symmetry Check** — Detect asymmetrical vertex positions.
- **Keep Side Selection** — Choose +X or -X as the symmetry reference.
- **Automatic Symmetry Fix** — Rebuild the opposite side using Delete, Mirror, and Merge.
- **Center Border Correction** — Align cutting-border vertices to X=0 before mirroring.
- **UV Preservation** — Transfer the original UV layout using topology-based matching.
- **UV Validation** — Validate UV correspondence before continuing.
- **Vertex Order Recovery** — Restore the original vertex order through UV-based mapping.
- **BlendShape Compatibility** — Preserve vertex correspondence for existing BlendShape targets when validation succeeds.

## Installation

1. Download `SymmetryTool.py`.
2. Place the file in your Maya scripts directory.
3. Open Maya and switch the Script Editor to Python.

## Usage

Run the following code in Maya's Python Script Editor:

```python
import SymmetryTool
SymmetryTool.SymmetryTool()
```

To reload the tool after updating the script:

```python
import importlib
import SymmetryTool

importlib.reload(SymmetryTool)
SymmetryTool.SymmetryTool()
```

## Workflow

1. **Geometry Rebuild** — Duplicate the original mesh, remove one side, detect cutting borders, flatten the center to X=0, mirror, and merge.
2. **UV Transfer** — Transfer all UV sets from the original mesh using topology-based matching.
3. **UV Validation** — Verify UV coordinates and face-vertex UV correspondence.
4. **Vertex Mapping** — Establish and validate a one-to-one vertex correspondence.
5. **Topology Validation** — Verify polygon connectivity.
6. **Vertex Order Recovery** — Reconstruct the corrected mesh using the original vertex order and UV layout.

## Safety

- The original mesh is never modified.
- All validation stages are mandatory.
- Failed validation prevents the creation of the final output.
- Intermediate meshes are retained for inspection.
- Only a successfully validated `_SymFinal` mesh should be considered a final result.

## Requirements

- Autodesk Maya 2026
- Python 3
- A clean, unbound polygon mesh
- Symmetry plane: Object Space X=0
- Clean transform values

**Note:** UV correspondence and topology validation must succeed for vertex order recovery. Results may vary with different mesh topology and UV layouts.

## Testing

Successfully tested in Maya 2026 with a MetaHuman-derived facial mesh.

Verified operations include geometry symmetry correction, UV preservation, vertex order recovery, and compatibility with existing facial BlendShape targets.

Testing on additional mesh types is recommended.

## License

This project is licensed under the MIT License.

Copyright © 2026 Yuchen Hu.

See the [LICENSE](LICENSE) file for more details.
