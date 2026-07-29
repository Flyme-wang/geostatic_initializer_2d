"""Abaqus-independent 3D geostatic model logic.

Coordinate convention:
    Z  = vertical direction (increasing upward, ground surface = max Z)
    XY = horizontal plane
    Gravity acts in -Z.
"""

from __future__ import division

import hashlib
import json
import math

from geostatic_initializer_core import (
    GeostaticModelError,
    _finite_positive,
    _finite_nonnegative,
)

# ---- 3D element type sets ------------------------------------------------
PORE_3D_ELEMENT_TYPES = frozenset((
    "C3D4P", "C3D6P", "C3D8P", "C3D8RP",
    "C3D10P", "C3D15P", "C3D20P", "C3D20RP",
))
SOLID_3D_ELEMENT_TYPES = frozenset((
    "C3D4", "C3D6", "C3D8", "C3D8R",
    "C3D10", "C3D15", "C3D20", "C3D20R",
))
SUPPORTED_3D_ELEMENT_TYPES = PORE_3D_ELEMENT_TYPES | SOLID_3D_ELEMENT_TYPES


def is_3d_pore_element(element_type):
    """Return True if *element_type* is a 3D pore-pressure element."""
    return str(element_type).strip().upper() in PORE_3D_ELEMENT_TYPES


# ---- 2-D interpolation on scattered (x,y) -> z --------------------------

def _barycentric_xyz(point, triangle):
    """Barycentric coords of *point*=(x,y) wrt *triangle*=((x1,y1,z1),...).

    Returns (l1, l2, l3, z_interp) or raises GeostaticModelError.
    """
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = triangle
    x, y = float(point[0]), float(point[1])
    denom = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    if abs(denom) < 1e-24:
        raise GeostaticModelError("degenerate triangle in 3D surface")
    l1 = ((y2 - y3) * (x - x3) + (x3 - x2) * (y - y3)) / denom
    l2 = ((y3 - y1) * (x - x3) + (x1 - x3) * (y - y3)) / denom
    l3 = 1.0 - l1 - l2
    tol = -1e-12
    if l1 < tol or l2 < tol or l3 < tol:
        raise GeostaticModelError("point (%r,%r) outside surface triangulation" % (x, y))
    return l1, l2, l3, l1 * z1 + l2 * z2 + l3 * z3


def interpolate_surface(triangles, x, y):
    """Return Z elevation of surface *triangles* at horizontal (x,y)."""
    x, y = float(x), float(y)
    for tri in triangles:
        try:
            _, _, _, z = _barycentric_xyz((x, y), tri)
            return z
        except GeostaticModelError:
            continue
    # Fallback: nearest-neighbour among all vertices
    points = []
    for tri in triangles:
        for p in tri:
            points.append(p)
    if not points:
        raise GeostaticModelError("3D surface has no triangles")
    dists = [((x - px) ** 2 + (y - py) ** 2, pz) for (px, py, pz) in points]
    dists.sort()
    return dists[0][1]


# ---- Surface extraction from free faces (3D) ----------------------------

def _element_face_definitions(node_count, el_type_upper):
    """Return tuple of face index-tuples for a 3D element."""
    # C3D8 / C3D8R: 6 quad faces
    if node_count == 8:
        return (
            (0, 1, 5, 4),  # face 1: -Z (bottom)
            (1, 2, 6, 5),  # face 2: +X
            (2, 3, 7, 6),  # face 3: +Z (top)
            (3, 0, 4, 7),  # face 4: -X
            (0, 3, 2, 1),  # face 5: -Y
            (4, 5, 6, 7),  # face 6: +Y
        )
    # C3D20 / C3D20R: 6 quad faces (only corner nodes for surface detection)
    if node_count == 20:
        return (
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
            (0, 3, 2, 1),
            (4, 5, 6, 7),
        )
    # C3D4: 4 triangular faces
    if node_count == 4:
        return (
            (0, 1, 3),
            (1, 2, 3),
            (2, 0, 3),
            (0, 2, 1),
        )
    # C3D10: 4 triangular faces (corner nodes only)
    if node_count == 10:
        return (
            (0, 1, 3),
            (1, 2, 3),
            (2, 0, 3),
            (0, 2, 1),
        )
    # C3D6: 2 triangular + 3 quad faces
    if node_count == 6:
        return (
            (0, 1, 4, 3),
            (1, 2, 5, 4),
            (2, 0, 3, 5),
            (0, 2, 1),
            (3, 4, 5),
        )
    # C3D15: same as C3D6 but with midside (corner nodes only)
    if node_count == 15:
        return (
            (0, 1, 4, 3),
            (1, 2, 5, 4),
            (2, 0, 3, 5),
            (0, 2, 1),
            (3, 4, 5),
        )
    return ()


def _triangulate_quad(p0, p1, p2, p3):
    """Split quad into two triangles."""
    return ((p0, p1, p2), (p0, p2, p3))


def _face_normal_z(tri):
    """Return Z-component of unit normal for triangle ((x1,y1,z1),(x2,y2,z2),(x3,y3,z3))."""
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = tri
    nx = (y2 - y1) * (z3 - z1) - (z2 - z1) * (y3 - y1)
    ny = (z2 - z1) * (x3 - x1) - (x2 - x1) * (z3 - z1)
    nz = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
    return nz


def surfaces_from_free_faces(elements, nodes):
    """Extract upper and lower triangulated surfaces from a 3D mesh.

    Returns (upper_triangles, lower_triangles).
    Each triangle is ((x1,y1,z1), (x2,y2,z2), (x3,y3,z3)).
    """
    node_map = {}
    for node in nodes:
        try:
            label = int(node.label)
        except Exception:
            continue
        node_map[label] = tuple(float(node.coordinates[i]) for i in range(3))

    # Build face-count dictionary
    face_occ = {}   # (sorted_node_labels) -> count
    face_tris = {}  # (sorted_node_labels) -> tuple of triangles

    for element in elements:
        el_type = str(getattr(element, "type", "")).upper()
        # Determine corner-node count
        if "8" in el_type and "8R" not in el_type:
            n_corner = 8
        elif "8R" in el_type:
            n_corner = 8
        elif "20" in el_type:
            n_corner = 20
        elif "4" in el_type and "4R" not in el_type:
            n_corner = 4
        elif "10" in el_type:
            n_corner = 10
        elif "6" in el_type:
            n_corner = 6
        elif "15" in el_type:
            n_corner = 15
        else:
            continue

        face_defs = _element_face_definitions(n_corner, el_type)
        if not face_defs:
            continue

        connectivity = tuple(int(v) for v in getattr(element, "connectivity", ()))
        if len(connectivity) < n_corner:
            # Try via getNodes
            if hasattr(element, "getNodes"):
                try:
                    raw_nodes = element.getNodes()
                except Exception:
                    continue
            else:
                continue
            connectivity = tuple(int(n.label) for n in raw_nodes[:n_corner])
            if len(connectivity) < n_corner:
                continue

        for face_indices in face_defs:
            face_labels = tuple(connectivity[i] for i in face_indices)
            key = tuple(sorted(face_labels))
            face_occ[key] = face_occ.get(key, 0) + 1

            if key in face_tris:
                continue  # already stored
            pts = [node_map[l] for l in face_labels if l in node_map]
            if len(pts) < 3:
                continue
            if len(pts) == 4:
                tris = _triangulate_quad(pts[0], pts[1], pts[2], pts[3])
            else:
                tris = (tuple(pts[:3]),)
            face_tris[key] = tris

    # Classify boundary faces by normal
    upper = []
    lower = []
    for key, count in face_occ.items():
        if count != 1:
            continue
        tris = face_tris.get(key, ())
        for tri in tris:
            nz = _face_normal_z(tri)
            if abs(nz) < 1e-10 * max(1.0, abs(nz)):
                continue  # near-vertical – skip
            if nz > 0:
                upper.append(tri)
            else:
                lower.append(tri)

    if not lower or not upper:
        raise GeostaticModelError(
            "3D mesh free-face extraction failed - no upper/lower surface found"
        )
    return tuple(upper), tuple(lower)


# ---- Region normalisation for 3D ----------------------------------------

def _normalize_region_3d(raw):
    """Validate and normalise one 3D region record."""
    instance = str(raw.get("instance", "")).strip()
    if not instance:
        raise GeostaticModelError("region instance name is empty")
    element_types = tuple(
        sorted(set(str(v).strip().upper() for v in raw.get("element_types", ())))
    )
    if not element_types:
        raise GeostaticModelError("%s has no element types" % instance)
    unsupported = tuple(v for v in element_types if v not in SUPPORTED_3D_ELEMENT_TYPES)
    if unsupported:
        raise GeostaticModelError(
            "%s uses unsupported 3D element type(s): %s" % (instance, ", ".join(unsupported))
        )
    pore_flags = tuple(is_3d_pore_element(v) for v in element_types)
    if any(pore_flags) and not all(pore_flags):
        raise GeostaticModelError("%s mixes pore and non-pore elements" % instance)
    porous = bool(pore_flags[0])

    density = _finite_positive(raw.get("density"), "%s density" % instance)
    k0 = _finite_nonnegative(raw.get("k0", 1.0), "%s K0" % instance)

    upper_tris = raw.get("upper_triangles")
    lower_tris = raw.get("lower_triangles")
    if upper_tris is None or lower_tris is None:
        raise GeostaticModelError("%s missing 3D surface data" % instance)

    def _normalize_triangles(tris):
        result = []
        for tri in tris:
            result.append(tuple(tuple(float(v) for v in p) for p in tri))
        return tuple(result)

    upper_tris = _normalize_triangles(upper_tris)
    lower_tris = _normalize_triangles(lower_tris)

    result = {
        "region_id": str(raw.get("region_id", instance)),
        "instance": instance,
        "element_types": element_types,
        "density": density,
        "k0": k0,
        "porous": porous,
        "upper_triangles": upper_tris,
        "lower_triangles": lower_tris,
        "element_labels": tuple(sorted(set(int(v) for v in raw.get("element_labels", ())))),
        "node_labels": tuple(sorted(set(int(v) for v in raw.get("node_labels", ())))),
    }
    if porous:
        void_ratio = _finite_nonnegative(raw.get("void_ratio"), "%s void ratio" % instance)
        saturation = _finite_nonnegative(raw.get("saturation"), "%s saturation" % instance)
        if saturation > 1.0:
            raise GeostaticModelError("%s saturation must not exceed 1" % instance)
        result.update(
            specific_weight=_finite_positive(
                raw.get("specific_weight"), "%s pore-fluid specific weight" % instance
            ),
            void_ratio=void_ratio,
            porosity=void_ratio / (1.0 + void_ratio),
            saturation=saturation,
        )
    else:
        result.update(specific_weight=0.0, void_ratio=0.0, porosity=0.0, saturation=0.0)
    return result


# ---- Plan builder -------------------------------------------------------

def build_plan_3d(model_name, regions, first_step_removed=(),
                   gravity=10.0, coupled_step=False):
    """Build a 3D geostatic plan (same interface as ``build_plan``)."""
    gravity = _finite_positive(gravity, "gravity")
    removed = set(str(v) for v in first_step_removed)
    active = [_normalize_region_3d(v) for v in regions if v["instance"] not in removed]
    if not active:
        raise GeostaticModelError("no regions are active in the first analysis step")

    any_pore = any(r["porous"] for r in active)
    if coupled_step and not any_pore:
        raise GeostaticModelError("coupled procedure has no pore-pressure elements")
    if any_pore and not coupled_step:
        raise GeostaticModelError("pore-pressure elements require a coupled procedure")

    fluid_weights = sorted(set(r["specific_weight"] for r in active if r["porous"]))
    if len(fluid_weights) > 1:
        raise GeostaticModelError("active porous regions use conflicting fluid weights")

    ground_tris = []
    for r in active:
        ground_tris.extend(r["upper_triangles"])

    base = {
        "schema_version": 2,
        "model_name": str(model_name),
        "mode": "COUPLED_3D" if any_pore else "TOTAL_3D",
        "gravity": gravity,
        "specific_weight": fluid_weights[0] if fluid_weights else 0.0,
        "regions": tuple(active),
        "porous_instances": tuple(
            sorted(set(r["instance"] for r in active if r["porous"]))
        ),
        "ground_triangles": tuple(ground_tris),
        "first_step_removed": tuple(sorted(removed)),
    }

    # fingerprint excluding triangle data (too large)
    # Use a deep copy to avoid mutating the original plan
    fp_doc = {}
    for key, value in base.items():
        if key == "regions":
            fp_doc[key] = tuple(
                dict((k, v) for k, v in r.items()
                     if k not in ("upper_triangles", "lower_triangles"))
                for r in value
            )
        elif key == "ground_triangles":
            continue
        else:
            fp_doc[key] = value
    base["fingerprint"] = hashlib.sha256(
        json.dumps(fp_doc, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return base


# ---- 3D stress / pore-pressure calculation ------------------------------

def _bulk_weight_3d(region, gravity):
    if not region["porous"]:
        return region["density"] * gravity
    return (region["density"] * gravity +
            region["saturation"] * region["porosity"] * region["specific_weight"])


def total_vertical_stress_3d(plan, x, y, z):
    """Vertical stress at (x,y,z) - Z axis is vertical upward, gravity = -Z."""
    z = float(z)
    overburden = 0.0
    for region in plan["regions"]:
        try:
            lower_z = interpolate_surface(region["lower_triangles"], x, y)
            upper_z = interpolate_surface(region["upper_triangles"], x, y)
        except GeostaticModelError:
            continue
        thickness = max(upper_z - max(z, lower_z), 0.0)
        overburden += thickness * _bulk_weight_3d(region, plan["gravity"])
    return -overburden


def pore_pressure_3d(plan, x, y, z):
    """Pore pressure at (x,y,z)."""
    if "COUPLED" not in plan["mode"]:
        return 0.0
    try:
        ground_z = interpolate_surface(plan["ground_triangles"], x, y)
    except GeostaticModelError:
        return 0.0
    return plan["specific_weight"] * max(ground_z - float(z), 0.0)


def returned_vertical_stress_3d(plan, instance_name, x, y, z):
    """Effective vertical stress returned by SIGINI for *instance_name*."""
    matches = [r for r in plan["regions"]
               if r["region_id"] == instance_name or r["instance"] == instance_name]
    selected = matches[0] if len(matches) == 1 else None
    if selected is None:
        raise GeostaticModelError("region is inactive or ambiguous: %s" % instance_name)
    stress = total_vertical_stress_3d(plan, x, y, z)
    if selected["porous"]:
        stress += selected["saturation"] * pore_pressure_3d(plan, x, y, z)
    return stress
