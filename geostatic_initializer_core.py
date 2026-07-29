"""Abaqus-independent geostatic model validation and calculations."""

from __future__ import division

import hashlib
import json
import math


class GeostaticModelError(ValueError):
    """Raised when a model cannot be initialized without guessing."""


PORE_ELEMENT_TYPES = frozenset(
    (
        "CPE3P",
        "CPE4P",
        "CPE4RP",
        "CPE6MP",
        "CPE8P",
        "CPE8RP",
        "CAX3P",
        "CAX4P",
        "CAX4RP",
        "CAX6MP",
        "CAX8P",
        "CAX8RP",
    )
)
SUPPORTED_TOTAL_ELEMENT_TYPES = frozenset(
    (
        "CPE3",
        "CPE4",
        "CPE4R",
        "CPE6",
        "CPE6M",
        "CPE8",
        "CPE8R",
        "CAX3",
        "CAX4",
        "CAX4R",
        "CAX6",
        "CAX6M",
        "CAX8",
        "CAX8R",
    )
)
SUPPORTED_2D_ELEMENT_TYPES = PORE_ELEMENT_TYPES | SUPPORTED_TOTAL_ELEMENT_TYPES


def _finite_positive(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise GeostaticModelError("%s must be a finite positive value" % label)
    if not math.isfinite(number) or number <= 0.0:
        raise GeostaticModelError("%s must be a finite positive value" % label)
    return number


def _finite_nonnegative(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise GeostaticModelError("%s must be finite and nonnegative" % label)
    if not math.isfinite(number) or number < 0.0:
        raise GeostaticModelError("%s must be finite and nonnegative" % label)
    return number


def is_pore_element(element_type):
    return str(element_type).strip().upper() in PORE_ELEMENT_TYPES


def interpolate_profile(profile, x_value):
    if not profile:
        raise GeostaticModelError("profile is empty")
    x_value = float(x_value)
    if x_value <= profile[0][0]:
        return float(profile[0][1])
    if x_value >= profile[-1][0]:
        return float(profile[-1][1])
    for first, second in zip(profile[:-1], profile[1:]):
        if first[0] <= x_value <= second[0]:
            width = second[0] - first[0]
            if width <= 0.0:
                raise GeostaticModelError("profile x coordinates must increase")
            ratio = (x_value - first[0]) / width
            return first[1] + ratio * (second[1] - first[1])
    raise GeostaticModelError("profile interpolation failed")


def profiles_from_nodes(nodes, tolerance=1.0e-9):
    if not nodes:
        raise GeostaticModelError("meshed region has no nodes")
    points = sorted((float(item[1][0]), float(item[1][1])) for item in nodes)
    span = max(point[0] for point in points) - min(point[0] for point in points)
    scale = max(abs(span), 1.0)
    threshold = max(float(tolerance) * scale, 1.0e-12)
    groups = []
    for x_value, y_value in points:
        if not groups or abs(x_value - groups[-1][0]) > threshold:
            groups.append([x_value, [y_value]])
        else:
            groups[-1][1].append(y_value)
    if len(groups) < 2:
        raise GeostaticModelError("region profile needs at least two x stations")
    y_values = [value for _, values in groups for value in values]
    y_scale = max(max(y_values) - min(y_values), 1.0)
    for _, values in groups:
        if len(values) < 2 or max(values) - min(values) <= 1.0e-10 * y_scale:
            raise GeostaticModelError(
                "region requires a columnar mesh: every x station needs distinct lower and upper nodes"
            )
    lower = tuple((group[0], min(group[1])) for group in groups)
    upper = tuple((group[0], max(group[1])) for group in groups)
    return lower, upper


def _normalize_region(raw):
    instance = str(raw.get("instance", "")).strip()
    if not instance:
        raise GeostaticModelError("region instance name is empty")
    element_types = tuple(
        sorted(set(str(value).strip().upper() for value in raw.get("element_types", ())))
    )
    if not element_types:
        raise GeostaticModelError("%s has no element types" % instance)
    unsupported = tuple(value for value in element_types if value not in SUPPORTED_2D_ELEMENT_TYPES)
    if unsupported:
        raise GeostaticModelError(
            "%s uses unsupported 2D element type(s): %s"
            % (instance, ", ".join(unsupported))
        )
    pore_flags = tuple(is_pore_element(value) for value in element_types)
    if any(pore_flags) and not all(pore_flags):
        raise GeostaticModelError("%s mixes pore and non-pore elements" % instance)
    porous = bool(pore_flags[0])
    density = _finite_positive(raw.get("density"), "%s density" % instance)
    k0 = _finite_nonnegative(raw.get("k0", 1.0), "%s K0" % instance)
    lower = raw.get("lower_profile")
    upper = raw.get("upper_profile")
    if lower is None or upper is None:
        lower, upper = profiles_from_nodes(raw.get("nodes", ()))
    elif raw.get("nodes") and not raw.get("profile_from_free_boundary", False):
        # Explicit envelopes must not bypass the same mesh-safety check used
        # when profiles are inferred directly from nodes.
        profiles_from_nodes(raw["nodes"])
    lower = tuple((float(x), float(y)) for x, y in lower)
    upper = tuple((float(x), float(y)) for x, y in upper)
    if len(lower) < 2 or len(upper) < 2:
        raise GeostaticModelError("%s lower/upper profiles are incompatible" % instance)
    for profile in (lower, upper):
        if any(second[0] <= first[0] for first, second in zip(profile[:-1], profile[1:])):
            raise GeostaticModelError(
                "%s boundary profiles must be single-valued with increasing x" % instance
            )
    if lower[0][0] != upper[0][0] or lower[-1][0] != upper[-1][0]:
        raise GeostaticModelError("%s lower/upper profile extents differ" % instance)
    profile_x = sorted(set(point[0] for point in lower + upper))
    y_values = [point[1] for point in lower + upper]
    thickness_tolerance = 1.0e-10 * max(max(y_values) - min(y_values), 1.0)
    if any(
        interpolate_profile(upper, x) - interpolate_profile(lower, x)
        <= thickness_tolerance
        for x in profile_x
    ):
        raise GeostaticModelError("%s lower/upper boundaries cross" % instance)
    result = {
        "region_id": str(raw.get("region_id", instance)),
        "instance": instance,
        "element_types": element_types,
        "density": density,
        "k0": k0,
        "porous": porous,
        "lower": lower,
        "upper": upper,
        "element_labels": tuple(sorted(set(int(v) for v in raw.get("element_labels", ())))),
        "node_labels": tuple(sorted(set(int(v) for v in raw.get("node_labels", ())))),
    }
    if porous:
        void_ratio = _finite_nonnegative(
            raw.get("void_ratio"), "%s void ratio" % instance
        )
        saturation = _finite_nonnegative(
            raw.get("saturation"), "%s saturation" % instance
        )
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
        result.update(
            specific_weight=0.0,
            void_ratio=0.0,
            porosity=0.0,
            saturation=0.0,
        )
    return result


def _profile_max(regions):
    x_values = sorted(
        set(point[0] for region in regions for point in region["upper"])
    )
    ground = []
    for x_value in x_values:
        candidates = []
        for region in regions:
            if region["upper"][0][0] <= x_value <= region["upper"][-1][0]:
                candidates.append(interpolate_profile(region["upper"], x_value))
        if candidates:
            ground.append((x_value, max(candidates)))
    if len(ground) < 2:
        raise GeostaticModelError("cannot construct active ground surface")
    return tuple(ground)


def _fingerprint(document):
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_plan(
    model_name,
    regions,
    first_step_removed=(),
    gravity=10.0,
    coupled_step=False,
):
    gravity = _finite_positive(gravity, "gravity")
    removed = set(str(value) for value in first_step_removed)
    active = [_normalize_region(value) for value in regions if value["instance"] not in removed]
    if not active:
        raise GeostaticModelError("no regions are active in the first analysis step")
    any_pore = any(region["porous"] for region in active)
    if coupled_step and not any_pore:
        raise GeostaticModelError("coupled procedure has no pore-pressure elements")
    if any_pore and not coupled_step:
        raise GeostaticModelError("pore-pressure elements require a coupled procedure")
    fluid_weights = sorted(
        set(region["specific_weight"] for region in active if region["porous"])
    )
    if len(fluid_weights) > 1:
        raise GeostaticModelError("active porous regions use conflicting fluid weights")
    base = {
        "schema_version": 1,
        "model_name": str(model_name),
        "mode": "COUPLED" if any_pore else "TOTAL",
        "gravity": gravity,
        "specific_weight": fluid_weights[0] if fluid_weights else 0.0,
        "regions": tuple(active),
        "porous_instances": tuple(
            sorted(set(region["instance"] for region in active if region["porous"]))
        ),
        "ground": _profile_max(active),
        "first_step_removed": tuple(sorted(removed)),
    }
    base["fingerprint"] = _fingerprint(base)
    return base


def pore_pressure(plan, x_value, y_value):
    if plan["mode"] != "COUPLED":
        return 0.0
    ground = interpolate_profile(plan["ground"], x_value)
    return plan["specific_weight"] * max(ground - float(y_value), 0.0)


def _bulk_weight(region, gravity):
    if not region["porous"]:
        return region["density"] * gravity
    return (
        region["density"] * gravity
        + region["saturation"] * region["porosity"] * region["specific_weight"]
    )


def total_vertical_stress(plan, x_value, y_value):
    y_value = float(y_value)
    overburden = 0.0
    for region in plan["regions"]:
        if not (region["lower"][0][0] <= x_value <= region["lower"][-1][0]):
            continue
        lower = interpolate_profile(region["lower"], x_value)
        upper = interpolate_profile(region["upper"], x_value)
        thickness = max(upper - max(y_value, lower), 0.0)
        overburden += thickness * _bulk_weight(region, plan["gravity"])
    return -overburden


def returned_vertical_stress(plan, instance_name, x_value, y_value):
    matches = [
        region
        for region in plan["regions"]
        if region["region_id"] == instance_name or region["instance"] == instance_name
    ]
    selected = matches[0] if len(matches) == 1 else None
    if selected is None:
        raise GeostaticModelError("region is inactive or ambiguous: %s" % instance_name)
    stress = total_vertical_stress(plan, x_value, y_value)
    if selected["porous"]:
        stress += selected["saturation"] * pore_pressure(plan, x_value, y_value)
    return stress
