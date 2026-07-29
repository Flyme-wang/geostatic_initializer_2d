"""Geostatic solver methods: TIN, Ray-casting, and Pre-computation.

Three solving strategies, selectable by the user:

    "tin"         – TIN surface interpolation via Fortran SIGINI (Phase 1)
    "raycast"     – True 3D ray-casting via Fortran SIGINI (Phase 2)
    "precompute"  – Python pre-computation, direct keyword injection (Phase 3)
"""

from __future__ import division

import math

from geostatic_initializer_core import GeostaticModelError
from geostatic_initializer_core_3d import (
    interpolate_surface,
    total_vertical_stress_3d,
    pore_pressure_3d,
    _bulk_weight_3d,
)


# =========================================================================
#  Solver method constants
# =========================================================================

class Method(object):
    TIN = "tin"
    RAYCAST = "raycast"
    PRECOMPUTE = "precompute"

    ALL = (TIN, RAYCAST, PRECOMPUTE)
    LABELS = {
        TIN: "TIN Interpolation (fast, columnar mesh)",
        RAYCAST: "Ray Casting (robust, complex geometry)",
        PRECOMPUTE: "Pre-compute & Direct Assign (no Fortran)",
    }


# =========================================================================
#  Utility: ray–triangle intersection (Moller-Trumbore)
# =========================================================================

def _ray_triangle_intersection(ray_origin, ray_dir, tri):
    """Moller-Trumbore ray-triangle intersection.

    ray_origin : (ox, oy, oz)  – start of ray
    ray_dir    : (dx, dy, dz)  – direction (not necessarily normalized)
    tri        : ((x1,y1,z1), (x2,y2,z2), (x3,y3,z3))

    Returns (t, u, v) or None if no intersection.
    t = distance along ray; u,v = barycentric coords.
    """
    (ox, oy, oz) = ray_origin
    (dx, dy, dz) = ray_dir
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = tri

    e1x, e1y, e1z = x2 - x1, y2 - y1, z2 - z1
    e2x, e2y, e2z = x3 - x1, y3 - y1, z3 - z1

    # cross(ray_dir, e2)
    px = dy * e2z - dz * e2y
    py = dz * e2x - dx * e2z
    pz = dx * e2y - dy * e2x

    det = e1x * px + e1y * py + e1z * pz
    if abs(det) < 1e-30:
        return None

    inv_det = 1.0 / det

    # T = origin - v0
    tx, ty, tz = ox - x1, oy - y1, oz - z1

    # u = dot(T, P) * inv_det
    u = (tx * px + ty * py + tz * pz) * inv_det
    if u < -1e-12 or u > 1.0 + 1e-12:
        return None

    # cross(T, e1)
    qx = ty * e1z - tz * e1y
    qy = tz * e1x - tx * e1z
    qz = tx * e1y - ty * e1x

    # v = dot(ray_dir, Q) * inv_det
    v = (dx * qx + dy * qy + dz * qz) * inv_det
    if v < -1e-12 or u + v > 1.0 + 1e-12:
        return None

    # t = dot(e2, Q) * inv_det
    t = (e2x * qx + e2y * qy + e2z * qz) * inv_det
    if t < -1e-12:
        return None

    return (t, u, v)


# =========================================================================
#  TIN Method  (Phase 1 – same logic as core_3d, exposed cleanly)
# =========================================================================

def compute_stress_tin(plan, x, y, z):
    """Vertical stress at (x,y,z) using TIN surface interpolation."""
    return total_vertical_stress_3d(plan, x, y, z)


def compute_pore_pressure_tin(plan, x, y, z):
    """Pore pressure at (x,y,z) using TIN surface interpolation."""
    return pore_pressure_3d(plan, x, y, z)


# =========================================================================
#  Ray-cast Method  (Phase 2 – true 3D ray–triangle casting)
# =========================================================================

def _cast_ray_through_regions(ox, oy, oz, dz_sign, regions, ground_tris):
    """Cast a vertical ray from (ox,oy,oz) through all region surfaces.

    dz_sign = +1 for upward ray (overburden calculation)
    dz_sign = -1 for downward ray (used in upward-pore-pressure extension)

    Returns a list of (z_entry, z_exit, region_index, is_inside) segments.
    """
    # Collect all triangle-face intersection events along the ray
    events = []  # (z, delta, region_index)

    for ri, region in enumerate(regions):
        upper = region.get("upper_triangles", ())
        lower = region.get("lower_triangles", ())

        # Intersect ray with upper surface triangles
        for tri in upper:
            hit = _ray_triangle_intersection(
                (ox, oy, oz), (0.0, 0.0, float(dz_sign)), tri
            )
            if hit is not None:
                t = hit[0]
                z_hit = oz + dz_sign * t
                # Upper surface: exiting the region (delta = -1)
                events.append((z_hit, -1, ri))

        # Intersect ray with lower surface triangles
        for tri in lower:
            hit = _ray_triangle_intersection(
                (ox, oy, oz), (0.0, 0.0, float(dz_sign)), tri
            )
            if hit is not None:
                t = hit[0]
                z_hit = oz + dz_sign * t
                # Lower surface: entering the region (delta = +1)
                events.append((z_hit, +1, ri))

    if not events:
        return []

    # Sort events by Z
    events.sort(key=lambda e: e[0])

    # Build segments: track which regions are active
    active = set()
    segments = []
    prev_z = oz

    for z_hit, delta, ri in events:
        if z_hit <= prev_z + 1e-12:
            # Coincident or slightly earlier – merge
            pass
        if active:
            # There was something between prev_z and z_hit
            segments.append((prev_z, z_hit, frozenset(active)))
        # Update active set
        if delta > 0:
            active.add(ri)
        else:
            active.discard(ri)
        prev_z = z_hit

    return segments


def compute_stress_raycast(plan, x, y, z):
    """Vertical stress at (x,y,z) by ray-casting upward through regions.

    The ray goes from (x,y,z) upward (+Z) toward the ground surface.
    Each segment of the ray that passes through a region contributes
    thickness * bulk_weight to the overburden.
    """
    z = float(z)
    ground_tris = plan.get("ground_triangles", ())
    regions = plan.get("regions", ())
    gravity = plan.get("gravity", 10.0)

    segments = _cast_ray_through_regions(x, y, z, +1, regions, ground_tris)

    sigma_v = 0.0
    for z_entry, z_exit, active_regions in segments:
        thickness = z_exit - z_entry
        if thickness <= 0.0:
            continue
        for ri in active_regions:
            region = regions[ri]
            sigma_v -= thickness * _bulk_weight_3d(region, gravity)

    return sigma_v


def compute_pore_pressure_raycast(plan, x, y, z):
    """Pore pressure at (x,y,z)."""
    if "COUPLED" not in plan.get("mode", ""):
        return 0.0
    ground_tris = plan.get("ground_triangles", ())
    try:
        ground_z = interpolate_surface(ground_tris, x, y)
    except GeostaticModelError:
        return 0.0
    return plan.get("specific_weight", 0.0) * max(ground_z - float(z), 0.0)


# =========================================================================
#  Pre-computation Method  (Phase 3 – direct keyword injection)
# =========================================================================

def compute_element_stress(plan, centroid):
    """Compute the 6-component stress tensor at an element centroid.

    centroid = (x, y, z) in model coordinates.

    Returns (S11, S22, S33, S12, S13, S23) following Abaqus ordering.
    For geostatic: S33 = vertical stress (Z-axis), S11 = S22 = K0 * S33.
    """
    x, y, z = float(centroid[0]), float(centroid[1]), float(centroid[2])
    sv = total_vertical_stress_3d(plan, x, y, z)

    # Determine K0 for the region containing this point
    k0 = 1.0
    for region in plan.get("regions", ()):
        try:
            lo = interpolate_surface(region.get("lower_triangles", ()), x, y)
            up = interpolate_surface(region.get("upper_triangles", ()), x, y)
            if lo - 1e-9 <= z <= up + 1e-9:
                k0 = region.get("k0", 1.0)
                break
        except GeostaticModelError:
            continue

    sh = k0 * sv  # horizontal stress
    return (sh, sh, sv, 0.0, 0.0, 0.0)


def compute_node_pore_pressure(plan, x, y, z):
    """Compute pore pressure at a node position."""
    return pore_pressure_3d(plan, x, y, z)


def precompute_all_stresses(plan, elements_data, nodes_data):
    """Pre-compute stress and pore pressure for all elements and nodes.

    elements_data: iterable of (element_label, instance_name, cx, cy, cz)
    nodes_data: iterable of (node_label, instance_name, nx, ny, nz)

    Returns:
        element_stresses: dict of el_label -> (S11,S22,S33,S12,S13,S23)
        node_pore_pressures: dict of node_label -> pore_pressure_value
    """
    element_stresses = {}
    for el_label, inst_name, cx, cy, cz in elements_data:
        element_stresses[el_label] = compute_element_stress(plan, (cx, cy, cz))

    node_pore_pressures = {}
    if "COUPLED" in plan.get("mode", ""):
        for nd_label, inst_name, nx, ny, nz in nodes_data:
            node_pore_pressures[nd_label] = compute_node_pore_pressure(plan, nx, ny, nz)

    return element_stresses, node_pore_pressures


# =========================================================================
#  Dispatch
# =========================================================================

def solve_at_point(plan, x, y, z, method=Method.TIN):
    """Compute vertical stress at a single point using the chosen method."""
    if method == Method.TIN:
        return compute_stress_tin(plan, x, y, z)
    elif method == Method.RAYCAST:
        return compute_stress_raycast(plan, x, y, z)
    elif method == Method.PRECOMPUTE:
        return total_vertical_stress_3d(plan, x, y, z)  # same as TIN for single point
    else:
        raise GeostaticModelError("unknown solver method: %s" % method)


def pore_pressure_at_point(plan, x, y, z, method=Method.TIN):
    """Compute pore pressure at a single point."""
    if method == Method.RAYCAST:
        return compute_pore_pressure_raycast(plan, x, y, z)
    else:
        return pore_pressure_3d(plan, x, y, z)
