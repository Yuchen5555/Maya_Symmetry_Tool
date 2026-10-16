from collections import Counter, defaultdict
import math
import maya.cmds as cmds
import maya.api.OpenMaya as om

WINDOW = 'YCSymmetryTool'
UV_TOL = 1e-5


def log(message):
    line = '[Sym v2.1] ' + str(message)
    print(line)
    if cmds.scrollField('ycSymLog21', exists=True):
        cmds.scrollField('ycSymLog21', e=True, insertText=line + '\n')


def mesh_fn(transform):
    shapes = cmds.listRelatives(transform, shapes=True,
                                noIntermediate=True, fullPath=True) or []
    shapes = [s for s in shapes if cmds.nodeType(s) == 'mesh']
    if len(shapes) != 1:
        raise RuntimeError('Expected exactly one non-intermediate mesh: ' + transform)
    selection = om.MSelectionList()
    selection.add(shapes[0])
    return om.MFnMesh(selection.getDagPath(0))


def selected_mesh_transform():
    selections = cmds.ls(selection=True, objectsOnly=True, long=True) or []
    selections = list(dict.fromkeys(selections))
    if len(selections) != 1:
        raise RuntimeError('Select exactly one clean mesh TRANSFORM.')
    obj = selections[0]
    if cmds.nodeType(obj) == 'mesh':
        obj = (cmds.listRelatives(obj, parent=True, fullPath=True) or [None])[0]
    if not obj or cmds.nodeType(obj) != 'transform':
        raise RuntimeError('Please select a polygon mesh transform.')
    mesh_fn(obj)
    return obj


def check_clean_transform(obj):
    attrs = [('translateX', 0), ('translateY', 0), ('translateZ', 0),
             ('rotateX', 0), ('rotateY', 0), ('rotateZ', 0),
             ('scaleX', 1), ('scaleY', 1), ('scaleZ', 1)]
    for attr, expected in attrs:
        value = cmds.getAttr(obj + '.' + attr)
        if abs(value - expected) > 1e-6:
            raise RuntimeError('Transform not identity: {}.{} = {}. '
                               'Use a clean static copy with a local X=0 symmetry plane.'.format(
                                   obj, attr, value))
    # Reject source mesh with a live deformer. We never touch the original,
    # but copying its evaluated state may be unpredictable when rigged.
    history = cmds.listHistory(obj, pruneDagObjects=True) or []
    deformers = [n for n in history if cmds.nodeType(n) in
                 ('skinCluster', 'blendShape', 'cluster', 'wire', 'lattice',
                  'wrap', 'deltaMush')]
    if deformers:
        raise RuntimeError('Source has live deformers ({}). Use an unbound static copy.'.format(deformers))


def counts(fn):
    return fn.numVertices, fn.numEdges, fn.numPolygons


def boundary_edges(fn):
    edge_iter = om.MItMeshEdge(fn.dagPath())
    result = set()
    while not edge_iter.isDone():
        if edge_iter.onBoundary():
            result.add(frozenset(int(i) for i in fn.getEdgeVertices(edge_iter.index())))
        edge_iter.next()
    return result


def rounded_position(p, decimals=7):
    return (round(p.x, decimals), round(p.y, decimals), round(p.z, decimals))


def boundary_position_keys(fn):
    points = fn.getPoints(om.MSpace.kObject)
    return {frozenset((rounded_position(points[a]), rounded_position(points[b])))
            for edge in boundary_edges(fn)
            for a, b in [tuple(edge)]}


def faces_to_remove(tr, keep_side):
    fn = mesh_fn(tr)
    points = fn.getPoints(om.MSpace.kObject)
    removed = []
    for face_id in range(fn.numPolygons):
        ids = fn.getPolygonVertices(face_id)
        centroid = sum(points[int(i)].x for i in ids) / float(len(ids))
        if (keep_side == '+X' and centroid < 0.0) or (keep_side == '-X' and centroid > 0.0):
            removed.append('{}.f[{}]'.format(tr, face_id))
    if not removed or len(removed) >= fn.numPolygons:
        raise RuntimeError('Face-centroid cut is empty/invalid; confirm local X=0.')
    return removed


def find_new_cut_vertices(fn, old_boundary_keys, max_cut_x):
    points = fn.getPoints(om.MSpace.kObject)
    fresh = []
    for edge in boundary_edges(fn):
        a, b = tuple(edge)
        key = frozenset((rounded_position(points[a]), rounded_position(points[b])))
        if key not in old_boundary_keys:
            fresh.append((a, b))
    if not fresh:
        raise RuntimeError('No NEW cut border detected after deleting faces.')
    vertices = sorted({i for edge in fresh for i in edge})
    maximum = max(abs(points[i].x) for i in vertices)
    log('Cut border: {} edges, {} vertices, maximum |X|={:.6f}'.format(
        len(fresh), len(vertices), maximum))
    if maximum > max_cut_x:
        raise RuntimeError('CUT BORDER: maximum |X|={:.6f} exceeds limit {:.6f}. '
                           'Inspect cut preview rather than blindly increasing threshold.'.format(
                               maximum, max_cut_x))
    if len(vertices) > max(10, int(fn.numVertices * 0.15)):
        raise RuntimeError('CUT BORDER: {} vertices ({:.1f}%) is suspiciously large.'.format(
            len(vertices), 100.0 * len(vertices) / fn.numVertices))
    # A single connected new seam is expected for this type of head.
    graph = {v: set() for v in vertices}
    for a, b in fresh:
        graph[a].add(b)
        graph[b].add(a)
    visited = set()
    stack = [vertices[0]]
    while stack:
        v = stack.pop()
        if v not in visited:
            visited.add(v)
            stack.extend(graph[v] - visited)
    if len(visited) != len(vertices):
        raise RuntimeError('CUT BORDER: multiple disconnected new borders. Preview required.')
    return vertices


def duplicate_static(src, suffix):
    name = src.split('|')[-1] + suffix
    dest = cmds.duplicate(src, returnRootsOnly=True, name=name)[0]
    try:
        cmds.delete(dest, constructionHistory=True)
        mesh_fn(dest)
        return dest
    except Exception:
        if cmds.objExists(dest):
            cmds.delete(dest)
        raise


def cut_half(original, keep_side, max_cut_x, suffix):
    old_keys = boundary_position_keys(mesh_fn(original))
    half = duplicate_static(original, suffix)
    try:
        cut_faces = faces_to_remove(half, keep_side)
        cmds.delete(cut_faces)
        cut_vertices = find_new_cut_vertices(mesh_fn(half), old_keys, max_cut_x)
        return half, cut_vertices
    except Exception:
        # Keep the preview mesh for inspection if cut validation failed.
        log('CUT FAIL: temporary half mesh kept for inspection: ' + half)
        raise


def snap_cut_to_zero(tr, seam):
    fn = mesh_fn(tr)
    points = fn.getPoints(om.MSpace.kObject)
    for i in seam:
        p = points[i]
        points[i] = om.MPoint(0.0, p.y, p.z)
    fn.setPoints(points, om.MSpace.kObject)


def reflect_mesh_points(tr):
    # Do NOT set transform.scaleX = -1 (previous version could hit a live connection).
    fn = mesh_fn(tr)
    points = fn.getPoints(om.MSpace.kObject)
    for i, p in enumerate(points):
        points[i] = om.MPoint(-p.x, p.y, p.z)
    fn.setPoints(points, om.MSpace.kObject)
    # Reflecting points reverses handedness; reverse the polygon winding.
    cmds.polyNormal(tr, normalMode=0, constructionHistory=False)


def center_border_count(tr, tolerance):
    fn = mesh_fn(tr)
    points = fn.getPoints(om.MSpace.kObject)
    return sum(1 for edge in boundary_edges(fn)
               if all(abs(points[i].x) <= tolerance for i in edge))


def symmetric_positions(tr, tolerance=1e-4):
    """Verify a one-to-one point reflection (not just equal vertex counts)."""
    points = mesh_fn(tr).getPoints(om.MSpace.kObject)
    positive = []
    negative = []
    for i, p in enumerate(points):
        if p.x > tolerance:
            positive.append(i)
        elif p.x < -tolerance:
            negative.append(i)
    if len(positive) != len(negative):
        raise RuntimeError('GEOMETRY: +X/−X vertex count differs ({} / {}).'.format(
            len(positive), len(negative)))

    def cell(x, y, z):
        return (math.floor(x / tolerance), math.floor(y / tolerance), math.floor(z / tolerance))

    grid = defaultdict(list)
    for j in negative:
        p = points[j]
        grid[cell(p.x, p.y, p.z)].append(j)
    used = set()
    for i in positive:
        p = points[i]
        x, y, z = -p.x, p.y, p.z
        kx, ky, kz = cell(x, y, z)
        candidates = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((kx + dx, ky + dy, kz + dz), []):
                        q = points[j]
                        dist = math.sqrt((x - q.x)**2 + (y - q.y)**2 + (z - q.z)**2)
                        if dist <= tolerance and j not in used:
                            candidates.append((dist, j))
        if not candidates:
            raise RuntimeError('GEOMETRY: unmatched mirrored vertex {} at ({:.5f}, {:.5f}, {:.5f}).'.format(
                i, p.x, p.y, p.z))
        used.add(min(candidates)[1])
    return len(positive)


def build_symmetric_geometry(original, keep_side, max_cut_x, merge_tol):
    half, seam = cut_half(original, keep_side, max_cut_x, '_SymHalfTmp')
    snap_cut_to_zero(half, seam)
    log('PASS [1/6] Cut + flatten: {} seam vertices snapped to X=0'.format(len(seam)))
    mirror = duplicate_static(half, '_SymMirrorTmp')
    result = None
    try:
        reflect_mesh_points(mirror)
        result = cmds.polyUnite(half, mirror, constructionHistory=False,
                                mergeUVSets=True,
                                name=original.split('|')[-1] + '_SymRebuilt')[0]
        cmds.delete(half, mirror)
        fn = mesh_fn(result)
        points = fn.getPoints(om.MSpace.kObject)
        seam_vertices = sorted({v for edge in boundary_edges(fn)
                                for v in edge if abs(points[v].x) <= merge_tol})
        if not seam_vertices:
            raise RuntimeError('WELD: no centerline boundary vertices after combine.')
        cmds.polyMergeVertex(['{}.vtx[{}]'.format(result, i) for i in seam_vertices],
                             distance=merge_tol, alwaysMergeTwoVertices=False,
                             constructionHistory=False)
        cmds.delete(result, constructionHistory=True)
        remaining = center_border_count(result, max(merge_tol * 2, 1e-6))
        if remaining:
            raise RuntimeError('WELD: {} open centerline edges remain.'.format(remaining))
        pairs = symmetric_positions(result, tolerance=max(1e-5, merge_tol * 3))
        log('PASS [2/6] Mirror / merge / geometry symmetry: {} pairs'.format(pairs))
        return result
    except Exception:
        log('GEOMETRY FAIL. Rebuilt preview (if present) retained: {}'.format(result))
        raise
    finally:
        for obj in (half, mirror):
            if obj and cmds.objExists(obj):
                cmds.delete(obj)


def get_uv_data(mesh, uvset):
    u, v = mesh.getUVs(uvset)
    face_counts, uv_ids = mesh.getAssignedUVs(uvset)
    polygon_counts, verts = mesh.getVertices()
    if list(face_counts) != list(polygon_counts) or len(uv_ids) != len(verts):
        raise RuntimeError('UV {}: polygon corner assignments are missing/incomplete.'.format(uvset))
    if any(int(i) < 0 or int(i) >= len(u) for i in uv_ids):
        raise RuntimeError('UV {}: invalid UV ID in polygon assignments.'.format(uvset))
    return list(zip(u, v)), list(map(int, face_counts)), list(map(int, uv_ids)), list(map(int, verts))


def coordinate_uv_bijection(src_data, dst_data, name, tolerance=UV_TOL):
    """Source UV ID -> target UV ID by unique coordinates, not by ID or order."""
    if len(src_data) != len(dst_data):
        raise RuntimeError('UV {}: count differs ({} vs {}).'.format(
            name, len(src_data), len(dst_data)))
    aa = sorted(enumerate(src_data), key=lambda pair: (pair[1][0], pair[1][1]))
    bb = sorted(enumerate(dst_data), key=lambda pair: (pair[1][0], pair[1][1]))
    if not aa:
        raise RuntimeError('UV {}: no UV data.'.format(name))
    for label, data in [('original', aa), ('transferred', bb)]:
        for previous, current in zip(data, data[1:]):
            u0, v0 = previous[1]
            u1, v1 = current[1]
            if max(abs(u1 - u0), abs(v1 - v0)) <= tolerance:
                raise RuntimeError('UV {}: duplicate/near-duplicate coordinate in {}. '
                                   'Safe unique UV mapping unavailable.'.format(name, label))
    mapping = {}
    for (src_id, p), (dst_id, q) in zip(aa, bb):
        if max(abs(p[0] - q[0]), abs(p[1] - q[1])) > tolerance:
            raise RuntimeError('UV {}: coordinates differ; first mismatch: '
                               'original {} = {}, transferred {} = {}.'.format(
                                   name, src_id, p, dst_id, q))
        mapping[src_id] = dst_id
    return mapping


def polygon_vertex_signatures(fn, vertex_map=None):
    return Counter(tuple(sorted(vertex_map[int(v)] if vertex_map is not None else int(v)
                                for v in fn.getPolygonVertices(f)))
                   for f in range(fn.numPolygons))


def verify_uv_and_vertex_mapping(original, transferred, uv_tolerance=UV_TOL,
                                 center_band=0.15):
    """Mandatory check: UV geometry/corners + bijection + polygon connectivity.

    Multiple UV sets must each induce exactly the SAME vertex mapping.
    Mapping only accepted when every face corner's source UV is carried to
    its correct corresponding polygon corner on the reconstructed mesh.
    """
    src = mesh_fn(original)
    dst = mesh_fn(transferred)
    if counts(src) != counts(dst):
        raise RuntimeError('TOPOLOGY: V/E/F counts differ: {} vs {}.'.format(
            counts(src), counts(dst)))
    original_uvsets = src.getUVSetNames()
    target_uvsets = dst.getUVSetNames()
    if set(original_uvsets) != set(target_uvsets) or not original_uvsets:
        raise RuntimeError('UV: UV set names differ: {} vs {}.'.format(
            original_uvsets, target_uvsets))

    candidate_vertex_map = None
    uv_ids_by_set = {}
    uv_data_by_set = {}
    for uvset in original_uvsets:
        s_coords, s_face_counts, s_uv_ids, s_verts = get_uv_data(src, uvset)
        t_coords, t_face_counts, t_uv_ids, t_verts = get_uv_data(dst, uvset)
        uv_id_map = coordinate_uv_bijection(s_coords, t_coords, uvset, uv_tolerance)
        source_uv_vertex = {}
        target_uv_vertex = {}
        for uv_id, vertex_id in zip(s_uv_ids, s_verts):
            if uv_id in source_uv_vertex and source_uv_vertex[uv_id] != vertex_id:
                raise RuntimeError('UV {}: one original UV ID belongs to multiple vertices.'.format(uvset))
            source_uv_vertex[uv_id] = vertex_id
        for uv_id, vertex_id in zip(t_uv_ids, t_verts):
            if uv_id in target_uv_vertex and target_uv_vertex[uv_id] != vertex_id:
                raise RuntimeError('UV {}: one transferred UV ID belongs to multiple vertices.'.format(uvset))
            target_uv_vertex[uv_id] = vertex_id
        if set(source_uv_vertex) != set(uv_id_map):
            raise RuntimeError('UV {}: some source UVs are unassigned to polygons.'.format(uvset))
        if set(target_uv_vertex) != set(uv_id_map.values()):
            raise RuntimeError('UV {}: some target UVs are unassigned to polygons.'.format(uvset))
        vertex_map = {}
        for source_uv, source_vertex in source_uv_vertex.items():
            target_vertex = target_uv_vertex[uv_id_map[source_uv]]
            previous = vertex_map.setdefault(source_vertex, target_vertex)
            if previous != target_vertex:
                raise RuntimeError('UV {}: seam UVs give conflicting vertex IDs at {}.'.format(
                    uvset, source_vertex))
        if len(vertex_map) != src.numVertices or len(set(vertex_map.values())) != src.numVertices:
            raise RuntimeError('UV {}: incomplete/non-bijective vertex mapping ({} of {}).'.format(
                uvset, len(vertex_map), src.numVertices))
        if candidate_vertex_map is None:
            candidate_vertex_map = vertex_map
        elif candidate_vertex_map != vertex_map:
            raise RuntimeError('UV: different UV sets imply different vertex mappings.')
        uv_ids_by_set[uvset] = uv_id_map
        uv_data_by_set[uvset] = ((s_face_counts, s_uv_ids, s_verts),
                                 (t_face_counts, t_uv_ids, t_verts))
        log('PASS [4/6] UV {}: {} coordinate matches; unique vertex map.'.format(
            uvset, len(uv_id_map)))

    if polygon_vertex_signatures(src, candidate_vertex_map) != polygon_vertex_signatures(dst):
        raise RuntimeError('TOPOLOGY: face connectivity differs under UV vertex mapping.')
    log('PASS [5/6] Topology: {} mapped vertices, polygons correspond.'.format(src.numVertices))

    # For each UV set, confirm mapped (vertex ID, UV ID) pairs per FACE.
    # This is stronger than comparing UV count or UV coordinate collections.
    for uvset in original_uvsets:
        ((s_counts, s_uv, s_vert), (t_counts, t_uv, t_vert)) = uv_data_by_set[uvset]
        uvmap = uv_ids_by_set[uvset]
        def corner_signatures(counters, uv_ids, vertices, v_map=None, uv_map=None):
            result = Counter()
            offset = 0
            for n in counters:
                pairs = []
                for j in range(offset, offset + n):
                    v = vertices[j]
                    u = uv_ids[j]
                    pairs.append((v_map[v] if v_map is not None else v,
                                  uv_map[u] if uv_map is not None else u))
                result[tuple(sorted(pairs))] += 1
                offset += n
            return result
        if corner_signatures(s_counts, s_uv, s_vert, candidate_vertex_map, uvmap) != \
                corner_signatures(t_counts, t_uv, t_vert):
            raise RuntimeError('UV {}: face-vertex UV corner assignments differ.'.format(uvset))
        log('PASS [4/6] UV {}: all face-vertex UV corners correspond.'.format(uvset))

    # Guard against the symmetric topology's wrong L/R topological match.
    src_points = src.getPoints(om.MSpace.kObject)
    dst_points = dst.getPoints(om.MSpace.kObject)
    for original_id, target_id in candidate_vertex_map.items():
        a = src_points[original_id].x
        b = dst_points[target_id].x
        if a * b < 0 and abs(a) > center_band and abs(b) > center_band:
            raise RuntimeError('MAPPING: wrong hemisphere at original vertex {} -> target {} '
                               '(X {:.4f} -> {:.4f}).'.format(original_id, target_id, a, b))
    log('PASS [5/6] Vertex mapping: no large hemisphere reversal detected.')
    return candidate_vertex_map


def transfer_uv_topology(original, geometry):
    # Transfer onto disposable working copy; do not apply to original.
    target = duplicate_static(geometry, '_UVTopoTmp')
    try:
        cmds.transferAttributes(original, target,
                                transferPositions=0, transferNormals=0,
                                transferUVs=2, transferColors=0,
                                sampleSpace=5, searchMethod=3,
                                flipUVs=0, searchScaleX=1.0,
                                searchScaleY=1.0, searchScaleZ=1.0,
                                matchChoice=0)
        cmds.delete(target, constructionHistory=True)
        log('PASS [3/6] Topology UV transfer command completed (NOT YET validated).')
        return target
    except Exception:
        log('UV TRANSFER FAIL: temporary model retained: ' + target)
        raise


def verify_final_original_data(original, final, fixed, mapping):
    """Confirm the final copy kept source topology, UV IDs and corner data."""
    src, out = mesh_fn(original), mesh_fn(final)
    if counts(src) != counts(out):
        raise RuntimeError('FINAL: original V/E/F counts changed on output.')
    a_counts, a_verts = src.getVertices()
    b_counts, b_verts = out.getVertices()
    if list(a_counts) != list(b_counts) or list(a_verts) != list(b_verts):
        raise RuntimeError('FINAL: original face topology / vertex order was modified.')
    if list(src.getUVSetNames()) != list(out.getUVSetNames()):
        raise RuntimeError('FINAL: original UV set names were modified.')
    for uvset in src.getUVSetNames():
        a_coords, a_fc, a_uv, a_v = get_uv_data(src, uvset)
        b_coords, b_fc, b_uv, b_v = get_uv_data(out, uvset)
        if a_fc != b_fc or a_uv != b_uv or a_v != b_v:
            raise RuntimeError('FINAL: {} UV face-vertex assignments changed.'.format(uvset))
        if len(a_coords) != len(b_coords):
            raise RuntimeError('FINAL: {} UV coordinate count changed.'.format(uvset))
        if any(max(abs(u - x), abs(v - y)) > 1e-7
               for (u, v), (x, y) in zip(a_coords, b_coords)):
            raise RuntimeError('FINAL: {} UV coordinates changed.'.format(uvset))
    fixed_pts = mesh_fn(fixed).getPoints(om.MSpace.kObject)
    output_pts = out.getPoints(om.MSpace.kObject)
    for i in range(src.numVertices):
        a, b = output_pts[i], fixed_pts[mapping[i]]
        if math.sqrt((a.x - b.x)**2 + (a.y - b.y)**2 + (a.z - b.z)**2) > 1e-6:
            raise RuntimeError('FINAL: mapped point {} was not written correctly.'.format(i))


def make_final(original, fixed, mapping):
    final = duplicate_static(original, '_SymFinal')
    try:
        fn = mesh_fn(final)
        fixed_points = mesh_fn(fixed).getPoints(om.MSpace.kObject)
        target_points = om.MPointArray()
        for v in range(fn.numVertices):
            target_points.append(fixed_points[mapping[v]])
        fn.setPoints(target_points, om.MSpace.kObject)
        # Verify writing positions hasn't lost the intended shape.
        symmetric_positions(final, tolerance=1e-4)
        verify_final_original_data(original, final, fixed, mapping)
        log('PASS [6/6] Final: original UV IDs / face corners / vertex order EXACTLY preserved.')
        return final
    except Exception:
        if cmds.objExists(final):
            cmds.delete(final)
        raise


def auto_fix(keep_side='+X', max_cut_x=0.15, merge_tol=1e-5):
    if keep_side not in ('+X', '-X') or max_cut_x <= 0 or merge_tol <= 0:
        raise ValueError('Invalid side / max cut X / merge tolerance.')
    original = selected_mesh_transform()
    check_clean_transform(original)
    log('SOURCE: {} | Keep {} | maxCut={} | mergeTol={}'.format(
        original, keep_side, max_cut_x, merge_tol))
    geometry = build_symmetric_geometry(original, keep_side, max_cut_x, merge_tol)
    transferred = transfer_uv_topology(original, geometry)
    try:
        mapping = verify_uv_and_vertex_mapping(original, transferred,
                                               uv_tolerance=UV_TOL,
                                               center_band=max_cut_x)
        final = make_final(original, transferred, mapping)
        # Intermediates not needed after full validation; leave source alone.
        for obj in (geometry, transferred):
            if cmds.objExists(obj):
                cmds.delete(obj)
        cmds.select(final, replace=True)
        log('SUCCESS: {} | ALL mandatory stages PASS'.format(final))
        return final
    except Exception as exc:
        # Retain debug models, but never pretend they're production-ready.
        log('STOP: UV / topology / recovery failed. No SymFinal created.')
        log('Preview only: geometry={} ; UV={} (both UNVERIFIED)'.format(
            geometry, transferred))
        raise


def preview_cut_border(max_cut_x=0.15, keep_side='+X'):
    original = selected_mesh_transform()
    check_clean_transform(original)
    half, seam = cut_half(original, keep_side, max_cut_x, '_CutPreview')
    cmds.select(['{}.vtx[{}]'.format(half, v) for v in seam], replace=True)
    log('PREVIEW: {} cut vertices selected on {}. Inspect before running Auto Fix.'.format(
        len(seam), half))
    return half


def run_fix_callback(*unused):
    cmds.undoInfo(openChunk=True)
    try:
        side = cmds.optionMenu('ycSymSide21', query=True, value=True)
        maxcut = cmds.floatField('ycSymMax21', query=True, value=True)
        merge = cmds.floatField('ycSymMerge21', query=True, value=True)
        auto_fix(side, maxcut, merge)
    except Exception as exc:
        log('FAIL: ' + str(exc))
        cmds.warning('[Sym v2.1] ' + str(exc))
    finally:
        cmds.undoInfo(closeChunk=True)


def run_preview_callback(*unused):
    cmds.undoInfo(openChunk=True)
    try:
        side = cmds.optionMenu('ycSymSide21', query=True, value=True)
        maxcut = cmds.floatField('ycSymMax21', query=True, value=True)
        preview_cut_border(maxcut, side)
    except Exception as exc:
        log('PREVIEW FAIL: ' + str(exc))
        cmds.warning('[Sym v2.1] ' + str(exc))
    finally:
        cmds.undoInfo(closeChunk=True)


def SymmetryTool():
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    cmds.window(WINDOW, title='Symmetry Tool v2.1',
                widthHeight=(560, 450), sizeable=True)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)
    cmds.text(label='Select ONE clean, unbound mesh. Source stays unchanged.', align='left')
    cmds.text(label='Local symmetry plane X=0; clean identity transform required.', align='left')
    cmds.rowLayout(numberOfColumns=2, adjustableColumn=2)
    cmds.text(label='Keep side')
    cmds.optionMenu('ycSymSide21')
    cmds.menuItem(label='+X')
    cmds.menuItem(label='-X')
    cmds.setParent('..')
    cmds.rowLayout(numberOfColumns=2, adjustableColumn=2)
    cmds.text(label='Max cut-border |X|')
    cmds.floatField('ycSymMax21', value=0.15, precision=5, minValue=0.000001)
    cmds.setParent('..')
    cmds.rowLayout(numberOfColumns=2, adjustableColumn=2)
    cmds.text(label='Merge tolerance')
    cmds.floatField('ycSymMerge21', value=0.00001, precision=8, minValue=1e-9)
    cmds.setParent('..')
    cmds.button(label='Preview Cut Border (new edges, no original edits)',
                height=34, command=run_preview_callback)
    cmds.button(label='AUTO FIX + UV VALIDATION + VERTEX RECOVERY',
                height=42, command=run_fix_callback)
    cmds.text(label='No UV Validation bypass. Failed checks = NO SymFinal.', align='left')
    cmds.text(label='Strict UV check requires unique UV positions in every UV set.', align='left')
    cmds.scrollField('ycSymLog21', editable=False, wordWrap=True,
                     height=210, text='')
    cmds.showWindow(WINDOW)


if __name__ == '__main__':
    SymmetryTool()
