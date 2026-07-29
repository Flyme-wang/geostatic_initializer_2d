"""Abaqus/CAE adapter for the 2D geostatic initializer."""

from __future__ import division, print_function

import os
import re
import shutil
import tempfile

try:
    from .geostatic_initializer_core import (
        GeostaticModelError,
        build_plan,
        interpolate_profile,
        is_pore_element,
        profiles_from_nodes,
    )
    from .geostatic_initializer_generator import write_project_pair
    from .geostatic_initializer_core_3d import (
        SUPPORTED_3D_ELEMENT_TYPES,
        is_3d_pore_element,
        surfaces_from_free_faces,
        interpolate_surface,
        build_plan_3d,
    )
    from .geostatic_initializer_generator_3d import (
        write_project_pair_3d,
    )
except (ImportError, ValueError):
    from geostatic_initializer_core import (
        GeostaticModelError,
        build_plan,
        interpolate_profile,
        is_pore_element,
        profiles_from_nodes,
    )
    from geostatic_initializer_generator import write_project_pair
    from geostatic_initializer_core_3d import (
        SUPPORTED_3D_ELEMENT_TYPES,
        is_3d_pore_element,
        surfaces_from_free_faces,
        interpolate_surface,
        build_plan_3d,
    )
    from geostatic_initializer_generator_3d import (
        write_project_pair_3d,
    )
    from geostatic_initializer_solver import (
        Method,
        precompute_all_stresses,
    )
    from geostatic_initializer_generator_precompute import (
        write_project_pair_precompute,
        render_keyword_blocks,
    )


BEGIN_MARKER = "** GI2D BEGIN"
END_MARKER = "** GI2D END"
ACTIVE_ELEMENT_SET = "GI2D_ACTIVE_ELEMENTS"
ACTIVE_POROUS_NODE_SET = "GI2D_ACTIVE_POROUS_NODES"


def _repository_values(repository):
    if repository is None:
        return ()
    try:
        return tuple(repository.values())
    except AttributeError:
        return tuple(repository)


def region_instance_names(region, known_names=()):
    names = getattr(region, "instanceNames", None)
    if names:
        return tuple(str(value) for value in names)
    instances = getattr(region, "instances", None)
    if instances:
        return tuple(str(getattr(value, "name", value)) for value in instances)
    instance = getattr(region, "instance", None)
    if instance is not None:
        return (str(getattr(instance, "name", instance)),)
    if isinstance(region, (tuple, list)):
        strings = tuple(str(value) for value in region if isinstance(value, str))
        matched = tuple(name for name in known_names if name in strings)
        if matched:
            return matched
        if len(strings) >= 3:
            return (strings[2],)
    return ()


def _is_inactive_flag(value):
    if value is False or value == 0:
        return True
    return str(value).strip().upper() in ("OFF", "FALSE", "NO", "0")


def _keyword_removed_instances(model, first_step_name, keyword_blocks, known_names):
    text = "\n".join(str(block) for block in keyword_blocks)
    step_pattern = re.compile(
        r"(?im)^\*STEP\b[^\n]*\bNAME\s*=\s*([^,\n]+)"
    )
    match = next(
        (
            item
            for item in step_pattern.finditer(text)
            if item.group(1).strip().upper() == str(first_step_name).strip().upper()
        ),
        None,
    )
    if match is None:
        return ()
    end = re.search(r"(?im)^\*END STEP\b", text[match.end() :])
    step_text = text[match.end() : match.end() + end.start()] if end else text[match.end() :]
    interactions = getattr(model, "interactions", {})
    by_upper = dict((str(name).upper(), value) for name, value in interactions.items())
    lines = step_text.splitlines()
    removed = set()
    current_interaction = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        comment = re.match(r"(?i)^\*\*\s*INTERACTION\s*:\s*(.+?)\s*$", stripped)
        if comment:
            current_interaction = comment.group(1).strip().strip("'\"")
            continue
        if not re.match(r"(?i)^\*MODEL CHANGE\b", stripped):
            continue
        if "REMOVE" not in stripped.upper():
            continue
        interaction = by_upper.get(current_interaction.upper())
        if interaction is not None:
            removed.update(
                region_instance_names(getattr(interaction, "region", None), known_names)
            )
        for data_line in lines[index + 1 :]:
            data = data_line.strip()
            if not data or data.startswith("**"):
                continue
            if data.startswith("*"):
                break
            set_reference = data.split(",", 1)[0].strip()
            instance_reference = set_reference.split(".", 1)[0].strip().upper()
            by_exact_name = dict((name.upper(), name) for name in known_names)
            if instance_reference in by_exact_name:
                removed.add(by_exact_name[instance_reference])
            break
    return tuple(sorted(removed))


def collect_first_step_removed_instances(model, first_step_name, keyword_blocks=None):
    assembly = getattr(model, "rootAssembly", None)
    instances = getattr(assembly, "instances", {}) if assembly is not None else {}
    known_names = tuple(str(name) for name in instances.keys())
    if keyword_blocks is None:
        keyword_block = getattr(model, "keywordBlock", None)
        if keyword_block is not None:
            keyword_block.synchVersions(storeNodesAndElements=False)
            keyword_blocks = tuple(keyword_block.sieBlocks)
    if keyword_blocks is not None:
        parsed = _keyword_removed_instances(
            model, first_step_name, keyword_blocks, known_names
        )
        if parsed:
            return parsed
    containers = [getattr(model, "interactions", None)]
    containers.append(getattr(model, "modelChanges", None))
    removed = set()
    seen = set()
    for container in containers:
        for item in _repository_values(container):
            if id(item) in seen:
                continue
            seen.add(id(item))
            if str(getattr(item, "createStepName", "")) != str(first_step_name):
                continue
            if not _is_inactive_flag(getattr(item, "activeInStep", True)):
                continue
            removed.update(
                region_instance_names(getattr(item, "region", None), known_names)
            )
    return tuple(sorted(removed))


def _element_corner_nodes(element, instance_nodes):
    nodes = None
    if hasattr(element, "getNodes"):
        try:
            nodes = tuple(element.getNodes())
        except Exception:
            nodes = None
    if nodes is None:
        connectivity = tuple(int(value) for value in getattr(element, "connectivity", ()))
        by_label = dict((int(node.label), node) for node in instance_nodes)
        if connectivity and all(value in by_label for value in connectivity):
            nodes = tuple(by_label[value] for value in connectivity)
        elif connectivity and all(0 <= value < len(instance_nodes) for value in connectivity):
            nodes = tuple(instance_nodes[value] for value in connectivity)
    element_type = str(getattr(element, "type", "")).upper()
    corner_count = 3 if "3" in element_type else 4 if "4" in element_type else 0
    if nodes is None or corner_count not in (3, 4) or len(nodes) < corner_count:
        raise GeostaticModelError(
            "columnar mesh boundary extraction supports linear CPE3/CPE4 or CAX3/CAX4 elements"
        )
    return tuple(nodes[:corner_count])


def _boundary_profiles_from_elements(elements, instance_nodes, tolerance=1.0e-9):
    edge_counts = {}
    edge_nodes = {}
    for element in elements:
        corners = _element_corner_nodes(element, instance_nodes)
        for first, second in zip(corners, corners[1:] + corners[:1]):
            first_label = int(first.label)
            second_label = int(second.label)
            key = tuple(sorted((first_label, second_label)))
            edge_counts[key] = edge_counts.get(key, 0) + 1
            edge_nodes[key] = (first, second)
    boundary = [edge_nodes[key] for key, count in edge_counts.items() if count == 1]
    if not boundary:
        raise GeostaticModelError("columnar mesh boundary has no free edges")
    boundary_labels = set(int(node.label) for edge in boundary for node in edge)
    by_label = dict((int(node.label), node) for node in instance_nodes)
    x_values = [float(by_label[label].coordinates[0]) for label in boundary_labels]
    x_min, x_max = min(x_values), max(x_values)
    scale = max(abs(x_max - x_min), 1.0)
    threshold = max(tolerance * scale, 1.0e-12)

    horizontal_edges = []
    for first, second in boundary:
        x_first = float(first.coordinates[0])
        x_second = float(second.coordinates[0])
        on_left = abs(x_first - x_min) <= threshold and abs(x_second - x_min) <= threshold
        on_right = abs(x_first - x_max) <= threshold and abs(x_second - x_max) <= threshold
        if not (on_left or on_right):
            horizontal_edges.append((int(first.label), int(second.label)))

    adjacency = {}
    for first, second in horizontal_edges:
        adjacency.setdefault(first, set()).add(second)
        adjacency.setdefault(second, set()).add(first)
    if any(len(values) > 2 for values in adjacency.values()):
        raise GeostaticModelError("columnar mesh boundary contains a branch")
    components = []
    unseen = set(adjacency)
    while unseen:
        pending = [unseen.pop()]
        component = set(pending)
        while pending:
            current = pending.pop()
            for neighbor in adjacency[current]:
                if neighbor not in component:
                    component.add(neighbor)
                    unseen.discard(neighbor)
                    pending.append(neighbor)
        components.append(component)
    if len(components) != 2:
        raise GeostaticModelError(
            "columnar mesh boundary must contain exactly two continuous upper/lower chains (no holes)"
        )

    profiles = []
    for component in components:
        endpoints = [label for label in component if len(adjacency[label]) == 1]
        edge_count = sum(len(adjacency[label]) for label in component) // 2
        if len(endpoints) != 2 or edge_count != len(component) - 1:
            raise GeostaticModelError("columnar mesh boundary chain is not continuous")
        ordered = []
        previous = None
        current = min(endpoints, key=lambda label: float(by_label[label].coordinates[0]))
        while True:
            ordered.append(current)
            candidates = [value for value in adjacency[current] if value != previous]
            if not candidates:
                break
            if len(candidates) != 1:
                raise GeostaticModelError("columnar mesh boundary contains a branch")
            previous, current = current, candidates[0]
        profile = tuple(
            (float(by_label[label].coordinates[0]), float(by_label[label].coordinates[1]))
            for label in ordered
        )
        for first, second in zip(profile[:-1], profile[1:]):
            if second[0] - first[0] <= threshold:
                raise GeostaticModelError(
                    "columnar mesh boundary is not single-valued and x-monotone"
                )
        if abs(profile[0][0] - x_min) > threshold or abs(profile[-1][0] - x_max) > threshold:
            raise GeostaticModelError("columnar mesh boundary does not span the region")
        profiles.append(profile)

    profiles.sort(key=lambda profile: sum(point[1] for point in profile) / len(profile))
    lower, upper = profiles
    sample_x = sorted(set(point[0] for profile in profiles for point in profile))
    if any(interpolate_profile(upper, x) <= interpolate_profile(lower, x) for x in sample_x):
        raise GeostaticModelError("columnar mesh upper/lower boundaries cross")
    return lower, upper


def extract_instance_profile(instance):
    nodes = tuple(
        (
            int(node.label),
            (float(node.coordinates[0]), float(node.coordinates[1])),
        )
        for node in instance.nodes
    )
    if not nodes:
        raise GeostaticModelError("instance has no mesh nodes")
    elements = tuple(getattr(instance, "elements", ()))
    if elements:
        lower, upper = _boundary_profiles_from_elements(elements, tuple(instance.nodes))
    else:
        lower, upper = profiles_from_nodes(nodes)
    return lower, upper, nodes

def _extract_3d_instance_profile(instance):
    """Extract upper/lower triangulated surfaces for a 3D instance."""
    nodes_tuple = tuple(instance.nodes)
    if not nodes_tuple:
        raise GeostaticModelError("instance %s has no mesh nodes" % instance.name)
    elements_tuple = tuple(getattr(instance, "elements", ()))
    upper_tris, lower_tris = surfaces_from_free_faces(elements_tuple, nodes_tuple)
    return lower_tris, upper_tris


def _element_type_set(elements):
    """Return the set of unique element type strings for a collection."""
    types = set()
    for el in elements:
        try:
            types.add(str(el.type).upper())
        except Exception:
            pass
    return types


def _model_has_3d_elements(model):
    """Quick check: does the model contain any 3D continuum elements?"""
    for instance in getattr(model.rootAssembly, "instances", {}).values():
        for el in getattr(instance, "elements", ()):
            et = str(getattr(el, "type", "")).upper()
            if et in SUPPORTED_3D_ELEMENT_TYPES:
                return True
    return False


def numeric_field_value(field, names):
    for name in names:
        if not hasattr(field, name):
            continue
        value = getattr(field, name)
        if isinstance(value, (tuple, list)):
            if not value:
                continue
            value = value[0]
            if isinstance(value, (tuple, list)):
                if not value:
                    continue
                value = value[0]
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    raise GeostaticModelError("predefined field has no readable numeric value")


def _generated_keyword_text(stress_set, pore_set, coupled):
    lines = [
        BEGIN_MARKER,
        "** Generated by Geostatic Initializer 2D",
        "*INITIAL CONDITIONS, TYPE=STRESS, USER",
        str(stress_set),
    ]
    if coupled:
        if not pore_set:
            raise ValueError("coupled keyword generation needs a pore node set")
        lines.extend(
            (
                "*INITIAL CONDITIONS, TYPE=PORE PRESSURE, USER",
                str(pore_set),
            )
        )
    lines.extend((END_MARKER, ""))
    return "\n".join(lines)


def replace_generated_keyword_blocks(blocks, stress_set, pore_set, coupled):
    generated = _generated_keyword_text(stress_set, pore_set, coupled)
    cleaned = []
    skipping = False
    for block in blocks:
        if BEGIN_MARKER in block:
            skipping = True
            if END_MARKER in block:
                skipping = False
            continue
        if skipping:
            if END_MARKER in block:
                skipping = False
            continue
        cleaned.append(block)
    step_index = next(
        (index for index, block in enumerate(cleaned) if block.lstrip().upper().startswith("*STEP")),
        len(cleaned),
    )
    cleaned.insert(step_index, generated)
    return tuple(cleaned)


def validate_job_model(job, model_name):
    actual = str(getattr(job, "model", ""))
    if actual != str(model_name):
        raise ValueError("job belongs to model %s, not %s" % (actual, model_name))
    return job


def _source_fingerprint(source_text):
    first = re.search(r"(?im)^C MODEL FINGERPRINT:\s*(\S+)\s*$", source_text)
    second = re.search(
        r"(?im)^C MODEL FINGERPRINT CONT:\s*(\S+)\s*$", source_text
    )
    if first is None:
        return ""
    return first.group(1) + (second.group(1) if second is not None else "")


def audit_generated_state(
    job_subroutine,
    source_text,
    plan,
    element_members,
    pore_members,
):
    errors = []
    if not os.path.isabs(str(job_subroutine)):
        errors.append("job userSubroutine path is not absolute")
    if _source_fingerprint(source_text) != str(plan["fingerprint"]):
        errors.append("generated Fortran fingerprint is stale")
    expected_elements = set()
    expected_pore_nodes = set()
    for region in plan["regions"]:
        instance = str(region["instance"]).upper()
        expected_elements.update(
            (instance, int(label)) for label in region.get("element_labels", ())
        )
        if region.get("porous"):
            expected_pore_nodes.update(
                (instance, int(label)) for label in region.get("node_labels", ())
            )
    actual_elements = set((str(name).upper(), int(label)) for name, label in element_members)
    actual_pore_nodes = set((str(name).upper(), int(label)) for name, label in pore_members)
    if actual_elements != expected_elements:
        errors.append("GI2D active element set does not exactly match current plan")
    if plan["mode"] == "COUPLED" and actual_pore_nodes != expected_pore_nodes:
        errors.append("GI2D pore node set does not exactly match current plan")
    if plan["mode"] != "COUPLED" and actual_pore_nodes:
        errors.append("total-stress plan unexpectedly has a GI2D pore node set")
    return {"ok": not errors, "errors": tuple(errors)}


def classify_analysis_mode(step_types, element_types):
    step_names = tuple(str(value).upper() for value in step_types)
    element_names = tuple(str(value).upper() for value in element_types)
    has_pore = any(is_pore_element(value) for value in element_names)
    has_nonpore = any(not is_pore_element(value) for value in element_names)
    coupled_procedure = any(
        "SOILS" in value
        or "POREFLUID" in value
        or "CONSOLIDATION" in value
        for value in step_names
    )
    total_procedure = any("STATICSTEP" in value for value in step_names)
    geostatic = any("GEOSTATIC" in value for value in step_names)
    if coupled_procedure and not has_pore:
        raise GeostaticModelError(
            "coupled procedure has no pore-pressure elements"
        )
    if has_pore and not (coupled_procedure or geostatic):
        raise GeostaticModelError(
            "pore-pressure elements are inconsistent with the analysis procedure"
        )
    if has_pore:
        return "COUPLED"
    if total_procedure or geostatic:
        return "TOTAL"
    if has_nonpore:
        raise GeostaticModelError("analysis procedure is not supported for total stress")
    raise GeostaticModelError("model has no supported continuum elements")


def _first_analysis_step(model):
    names = [str(name) for name in model.steps.keys() if str(name) != "Initial"]
    if not names:
        raise GeostaticModelError("model has no analysis step")
    return names[0]


def _single_material_for_instance(model, instance):
    part_name = str(getattr(instance, "partName", ""))
    if not part_name:
        part_name = str(getattr(getattr(instance, "part", None), "name", ""))
    part = model.parts[part_name]
    assignments = tuple(part.sectionAssignments)
    if len(assignments) != 1:
        raise GeostaticModelError(
            "%s must have exactly one section assignment in the MVP" % instance.name
        )
    section = model.sections[assignments[0].sectionName]
    material_name = str(section.material)
    return material_name, model.materials[material_name]


def _section_assignment_element_labels(assignment):
    elements = getattr(getattr(assignment, "region", None), "elements", ())
    return tuple(sorted(set(int(element.label) for element in elements)))


def _profile_for_labels(instance, element_labels):
    if not element_labels:
        return extract_instance_profile(instance)
    selected = set(int(value) for value in element_labels)
    node_labels = set()
    instance_nodes = tuple(instance.nodes)
    valid_labels = set(int(node.label) for node in instance_nodes)
    for element in instance.elements:
        if int(element.label) in selected:
            element_nodes = None
            if hasattr(element, "getNodes"):
                try:
                    element_nodes = tuple(element.getNodes())
                except Exception:
                    element_nodes = None
            if element_nodes is not None:
                node_labels.update(int(node.label) for node in element_nodes)
                continue
            connectivity = tuple(int(value) for value in element.connectivity)
            if all(value in valid_labels for value in connectivity):
                node_labels.update(connectivity)
            elif all(0 <= value < len(instance_nodes) for value in connectivity):
                node_labels.update(int(instance_nodes[value].label) for value in connectivity)
            else:
                raise GeostaticModelError(
                    "element connectivity cannot be mapped to instance node labels"
                )
    nodes = tuple(node for node in instance.nodes if int(node.label) in node_labels)
    selected_elements = tuple(
        element for element in instance.elements if int(element.label) in selected
    )
    lower, upper = _boundary_profiles_from_elements(selected_elements, tuple(instance.nodes))
    records = tuple(
        (int(node.label), (float(node.coordinates[0]), float(node.coordinates[1])))
        for node in nodes
    )
    return lower, upper, records


def _all_instance_spans(assembly):
    spans = {}
    for instance_name, instance in getattr(assembly, "instances", {}).items():
        if not len(getattr(instance, "elements", ())) or not len(
            getattr(instance, "nodes", ())
        ):
            continue
        x_values = tuple(float(node.coordinates[0]) for node in instance.nodes)
        spans[str(instance_name)] = (min(x_values), max(x_values))
    return spans


def _collect_surface_evidence(assembly, instance_spans):
    evidence = {}
    known_names = tuple(instance_spans)

    def add_surface(evidence_name, surface, forced_names=()):
        entities = []
        for attr in (
            "edges",
            "side1Edges",
            "side2Edges",
            "end1Edges",
            "end2Edges",
            "faces",
            "side1Faces",
            "side2Faces",
            "elements",
        ):
            value = getattr(surface, attr, None)
            if value:
                entities.extend(tuple(value))
        coordinates = []
        raw_intervals = []
        for entity in entities:
            nodes = getattr(entity, "nodes", None)
            if nodes is None and hasattr(entity, "getNodes"):
                try:
                    nodes = entity.getNodes()
                except Exception:
                    nodes = ()
            entity_x = []
            for node in nodes or ():
                try:
                    x_value = float(node.coordinates[0])
                    coordinates.append(x_value)
                    entity_x.append(x_value)
                except Exception:
                    pass
            if entity_x:
                raw_intervals.append((min(entity_x), max(entity_x)))
        names = tuple(forced_names) or region_instance_names(surface, known_names)
        if not names:
            derived = []
            for entity in entities:
                candidate = getattr(entity, "instanceName", None)
                if candidate is None:
                    candidate = getattr(getattr(entity, "instance", None), "name", None)
                if candidate in instance_spans:
                    derived.append(str(candidate))
            names = tuple(sorted(set(derived)))
        expected = None
        spans = [instance_spans[name] for name in names if name in instance_spans]
        if spans:
            expected = (min(value[0] for value in spans), max(value[1] for value in spans))
        scale_values = [abs(value) for interval in raw_intervals for value in interval]
        if expected:
            scale_values.extend((abs(expected[0]), abs(expected[1]), abs(expected[1] - expected[0])))
        tolerance = 1.0e-8 * max(scale_values + [1.0])
        merged = []
        for start, end in sorted(raw_intervals):
            if not merged or start > merged[-1][1] + tolerance:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)
        merged = tuple((value[0], value[1]) for value in merged)
        gaps = []
        if expected:
            cursor = expected[0]
            for start, end in merged:
                if end < expected[0] - tolerance or start > expected[1] + tolerance:
                    continue
                clipped_start = max(start, expected[0])
                clipped_end = min(end, expected[1])
                if clipped_start > cursor + tolerance:
                    gaps.append((cursor, clipped_start))
                cursor = max(cursor, clipped_end)
            if cursor < expected[1] - tolerance:
                gaps.append((cursor, expected[1]))
        else:
            for first, second in zip(merged[:-1], merged[1:]):
                if second[0] > first[1] + tolerance:
                    gaps.append((first[1], second[0]))
        evidence[str(evidence_name).upper()] = {
            "entity_count": len(entities),
            "span": (min(coordinates), max(coordinates)) if coordinates else None,
            "expected_span": expected,
            "intervals": merged,
            "coverage_gaps": tuple(gaps),
            "instances": names,
        }

    for surface_name, surface in getattr(assembly, "surfaces", {}).items():
        add_surface(surface_name, surface)
    for instance_name, instance in getattr(assembly, "instances", {}).items():
        for surface_name, surface in getattr(instance, "surfaces", {}).items():
            qualified = "%s.%s" % (instance_name, surface_name)
            add_surface(qualified, surface, (str(instance_name),))
    return evidence


def _parse_input_surfaces_and_ties(text):
    lines = text.splitlines()
    surfaces = {}
    ties = []
    index = 0
    current_part = None
    while index < len(lines):
        stripped = lines[index].strip()
        upper = stripped.upper()
        if upper.startswith("*PART"):
            name_match = re.search(r"(?i)\bNAME\s*=\s*([^,]+)", stripped)
            current_part = name_match.group(1).strip().upper() if name_match else None
            index += 1
            continue
        if upper.startswith("*END PART"):
            current_part = None
            index += 1
            continue
        if upper.startswith("*SURFACE"):
            name_match = re.search(r"(?i)\bNAME\s*=\s*([^,]+)", stripped)
            name = name_match.group(1).strip().upper() if name_match else ""
            data = []
            index += 1
            while index < len(lines) and not lines[index].lstrip().startswith("*"):
                value = lines[index].strip()
                if value and not value.startswith("**"):
                    data.append(value)
                index += 1
            if name:
                value = tuple(data)
                if current_part:
                    surfaces["%s.%s" % (current_part, name)] = value
                if name not in surfaces:
                    surfaces[name] = value
            continue
        if upper.startswith("*TIE"):
            name_match = re.search(r"(?i)\bNAME\s*=\s*([^,]+)", stripped)
            name = name_match.group(1).strip() if name_match else "UNNAMED-TIE"
            data = None
            index += 1
            while index < len(lines) and not lines[index].lstrip().startswith("*"):
                value = lines[index].strip()
                if value and not value.startswith("**"):
                    data = value
                    break
                index += 1
            refs = tuple(
                item.strip().upper() for item in (data or "").split(",") if item.strip()
            )
            ties.append(
                {
                    "name": name,
                    "secondary": refs[0] if len(refs) > 0 else "",
                    "main": refs[1] if len(refs) > 1 else "",
                }
            )
            continue
        index += 1
    return surfaces, tuple(ties)


def _resolve_input_surface(surface_name, surfaces):
    surface_name = str(surface_name).upper()
    if surface_name in surfaces:
        return surfaces[surface_name]
    if "." in surface_name:
        instance_name, suffix = surface_name.rsplit(".", 1)
        part_name = re.sub(r"-\d+$", "", instance_name)
        qualified_part = "%s.%s" % (part_name, suffix)
        if qualified_part in surfaces:
            return surfaces[qualified_part]
        if suffix in surfaces:
            return surfaces[suffix]
    return None


def _constant_density(material, instance_name):
    density = getattr(material, "density", None)
    table = getattr(density, "table", ()) if density is not None else ()
    if not table or not table[0]:
        raise GeostaticModelError("%s material has no density" % instance_name)
    if len(table) != 1 or len(table[0]) != 1:
        raise GeostaticModelError("%s density must be constant" % instance_name)
    return float(table[0][0])


def _field_for_instance(model, instance_name, key_fragment):
    fragment = key_fragment.lower()
    matches = []
    for name, field in model.predefinedFields.items():
        if getattr(field, "suppressed", False):
            continue
        label = (str(name) + " " + field.__class__.__name__).lower()
        if fragment not in label:
            continue
        names = region_instance_names(getattr(field, "region", None))
        if instance_name in names or instance_name.upper() in str(
            getattr(field, "region", "")
        ).upper():
            matches.append(field)
    if len(matches) > 1:
        raise GeostaticModelError(
            "%s has ambiguous %s field coverage" % (instance_name, key_fragment)
        )
    return matches[0] if matches else None


def _porous_properties(model, instance_name, material):
    permeability = getattr(material, "permeability", None)
    if permeability is None:
        raise GeostaticModelError("%s pore elements lack permeability" % instance_name)
    specific_weight = float(getattr(permeability, "specificWeight", 0.0))
    void_field = _field_for_instance(model, instance_name, "void")
    sat_field = _field_for_instance(model, instance_name, "sat")
    if void_field is not None:
        void_ratio = numeric_field_value(
            void_field,
            (
                "voidsRatio1",
                "voidsRatio",
                "voidRatio1",
                "voidRatio",
                "value",
                "magnitude",
                "magnitudes",
            ),
        )
    else:
        raise GeostaticModelError("%s lacks a unique initial void ratio" % instance_name)
    if sat_field is None:
        raise GeostaticModelError("%s lacks a unique initial saturation" % instance_name)
    saturation = numeric_field_value(
        sat_field, ("value", "saturation", "magnitude", "magnitudes")
    )
    return specific_weight, void_ratio, saturation


def inspect_model(model, gravity=10.0, k0=1.0):
    first_step = _first_analysis_step(model)
    removed = collect_first_step_removed_instances(model, first_step)
    removed_set = set(removed)
    regions = []
    all_element_types = []
    instance_spans = _all_instance_spans(model.rootAssembly)
    for instance_name, instance in model.rootAssembly.instances.items():
        if instance_name in removed_set or not len(instance.elements):
            continue
        # Fail before material/section processing if the real mesh cannot
        # supply an unambiguous lower and upper node at every x station.
        extract_instance_profile(instance)
        part_name = str(getattr(instance, "partName", ""))
        if not part_name:
            part_name = str(getattr(getattr(instance, "part", None), "name", ""))
        part = model.parts[part_name]
        assignments = tuple(part.sectionAssignments)
        if not assignments:
            raise GeostaticModelError("%s has no section assignment" % instance_name)
        element_by_label = dict((int(e.label), e) for e in instance.elements)
        covered = set()
        for assignment_index, assignment in enumerate(assignments):
            labels = _section_assignment_element_labels(assignment)
            if not labels and len(assignments) == 1:
                labels = tuple(sorted(element_by_label))
            if not labels:
                raise GeostaticModelError(
                    "%s section assignment has no mesh elements" % instance_name
                )
            overlap = covered.intersection(labels)
            if overlap:
                raise GeostaticModelError(
                    "%s section assignments overlap element labels" % instance_name
                )
            covered.update(labels)
            try:
                selected_elements = tuple(element_by_label[label] for label in labels)
            except KeyError:
                raise GeostaticModelError(
                    "%s section assignment references unknown elements" % instance_name
                )
            element_types = tuple(
                sorted(set(str(element.type).upper() for element in selected_elements))
            )
            all_element_types.extend(element_types)
            porous = bool(
                element_types and all(is_pore_element(value) for value in element_types)
            )
            section = model.sections[assignment.sectionName]
            material_name = str(section.material)
            material = model.materials[material_name]
            lower, upper, nodes = _profile_for_labels(instance, labels)
            raw = {
                "region_id": "%s::%s" % (instance_name, assignment.sectionName),
                "instance": str(instance_name),
                "material": material_name,
                "element_types": element_types,
                "element_labels": labels,
                "node_labels": tuple(record[0] for record in nodes),
                "density": _constant_density(material, instance_name),
                "k0": float(k0),
                "lower_profile": lower,
                "upper_profile": upper,
                "profile_from_free_boundary": True,
                "nodes": nodes,
            }
            if porous:
                specific_weight, void_ratio, saturation = _porous_properties(
                    model, instance_name, material
                )
                raw.update(
                    specific_weight=specific_weight,
                    void_ratio=void_ratio,
                    saturation=saturation,
                )
            regions.append(raw)
        if covered != set(element_by_label):
            raise GeostaticModelError(
                "%s section assignments do not cover every element" % instance_name
            )
    step_types = tuple(
        step.__class__.__name__
        for name, step in model.steps.items()
        if str(name) != "Initial"
    )
    mode = classify_analysis_mode(step_types, tuple(all_element_types))
    plan = build_plan(
        getattr(model, "name", "active-model"),
        tuple(regions),
        first_step_removed=removed,
        gravity=gravity,
        coupled_step=mode == "COUPLED",
    )
    plan["analysis_step_types"] = step_types
    plan["tie_surface_evidence"] = _collect_surface_evidence(
        model.rootAssembly, instance_spans
    )
    return plan



def inspect_model_3d(model, gravity=10.0, k0=1.0):
    """Inspect a 3D model and build a geostatic plan (Z = vertical upward)."""
    first_step = _first_analysis_step(model)
    removed = collect_first_step_removed_instances(model, first_step)
    removed_set = set(removed)
    regions = []
    all_element_types = []

    for instance_name, instance in model.rootAssembly.instances.items():
        if instance_name in removed_set or not len(instance.elements):
            continue

        element_types = _element_type_set(instance.elements)
        element_types_3d = tuple(sorted(
            et for et in element_types if et in SUPPORTED_3D_ELEMENT_TYPES
        ))
        if not element_types_3d:
            continue
        all_element_types.extend(element_types_3d)

        try:
            lower_tris, upper_tris = _extract_3d_instance_profile(instance)
        except GeostaticModelError:
            raise

        part_name = str(getattr(instance, "partName", ""))
        if not part_name:
            part_name = str(getattr(getattr(instance, "part", None), "name", ""))
        part = model.parts[part_name]
        assignments = tuple(part.sectionAssignments)
        if not assignments:
            raise GeostaticModelError("%s has no section assignment" % instance_name)

        element_by_label = dict((int(e.label), e) for e in instance.elements)
        covered = set()

        for assign_index, assignment in enumerate(assignments):
            labels = _section_assignment_element_labels(assignment)
            if not labels and len(assignments) == 1:
                labels = tuple(sorted(element_by_label))
            if not labels:
                raise GeostaticModelError(
                    "%s section assignment has no mesh elements" % instance_name
                )
            overlap = covered.intersection(labels)
            if overlap:
                raise GeostaticModelError(
                    "%s section assignments overlap element labels" % instance_name
                )
            covered.update(labels)

            try:
                selected_elements = tuple(element_by_label[label] for label in labels)
            except KeyError:
                raise GeostaticModelError(
                    "%s section assignment references unknown elements" % instance_name
                )

            sec_elem_types = tuple(sorted(set(
                str(el.type).upper() for el in selected_elements
            )))
            porous = bool(
                sec_elem_types and all(is_3d_pore_element(v) for v in sec_elem_types)
            )

            section = model.sections[assignment.sectionName]
            material_name = str(section.material)
            material = model.materials[material_name]

            node_labels_set = set()
            for el in selected_elements:
                try:
                    nds = el.getNodes()
                except Exception:
                    connectivity = tuple(int(v) for v in el.connectivity)
                    inst_nodes = tuple(instance.nodes)
                    for ci in connectivity:
                        if 0 <= ci < len(inst_nodes):
                            node_labels_set.add(int(inst_nodes[ci].label))
                    continue
                for n in nds:
                    node_labels_set.add(int(n.label))

            raw = {
                "region_id": "%s::%s" % (instance_name, assignment.sectionName),
                "instance": str(instance_name),
                "material": material_name,
                "element_types": sec_elem_types,
                "element_labels": labels,
                "node_labels": tuple(sorted(node_labels_set)),
                "density": _constant_density(material, instance_name),
                "k0": float(k0),
                "upper_triangles": upper_tris,
                "lower_triangles": lower_tris,
            }
            if porous:
                specific_weight, void_ratio, saturation = _porous_properties(
                    model, instance_name, material
                )
                raw.update(
                    specific_weight=specific_weight,
                    void_ratio=void_ratio,
                    saturation=saturation,
                )
            regions.append(raw)

        if covered != set(element_by_label):
            raise GeostaticModelError(
                "%s section assignments do not cover every element" % instance_name
            )

    step_types = tuple(
        step.__class__.__name__
        for name, step in model.steps.items()
        if str(name) != "Initial"
    )
    coupled_step = any(
        "SOILS" in v.upper() or "POREFLUID" in v.upper() or "CONSOLIDATION" in v.upper()
        for v in step_types
    )
    plan = build_plan_3d(
        getattr(model, "name", "active-model"),
        tuple(regions),
        first_step_removed=removed,
        gravity=gravity,
        coupled_step=coupled_step,
    )
    plan["analysis_step_types"] = step_types
    return plan




def _replace_assembly_set(assembly, name, entity_groups, entity_name):
    from abaqusConstants import UNION

    try:
        if name in assembly.sets:
            del assembly.sets[name]
    except Exception:
        pass
    child_sets = []
    for index, entities in enumerate(entity_groups):
        child_name = "%s_%d" % (name, index + 1)
        try:
            if child_name in assembly.sets:
                del assembly.sets[child_name]
        except Exception:
            pass
        kwargs = {entity_name: entities}
        child_sets.append(assembly.Set(name=child_name, **kwargs))
    return assembly.SetByBoolean(name=name, sets=tuple(child_sets), operation=UNION)


def _apply_keyword_block(model, coupled):
    keyword_block = model.keywordBlock
    keyword_block.synchVersions(storeNodesAndElements=False)
    blocks = tuple(keyword_block.sieBlocks)
    generated = _generated_keyword_text(
        ACTIVE_ELEMENT_SET,
        ACTIVE_POROUS_NODE_SET if coupled else None,
        coupled,
    )
    begin = next((i for i, block in enumerate(blocks) if BEGIN_MARKER in block), None)
    if begin is not None:
        end = next(
            (i for i in range(begin, len(blocks)) if END_MARKER in blocks[i]),
            begin,
        )
        keyword_block.replace(begin, generated)
        for index in range(begin + 1, end + 1):
            keyword_block.replace(index, "** GI2D superseded block")
        return
    step_index = next(
        i for i, block in enumerate(blocks) if block.lstrip().upper().startswith("*STEP")
    )
    keyword_block.insert(max(step_index - 1, 0), generated)


def apply_plan(model, plan):
    assembly = model.rootAssembly
    active_names = tuple(region["instance"] for region in plan["regions"])
    element_groups = []
    porous_node_groups = []
    for region in plan["regions"]:
        instance = assembly.instances[region["instance"]]
        labels = region["element_labels"]
        elements = (
            instance.elements.sequenceFromLabels(labels=labels)
            if labels
            else instance.elements
        )
        element_groups.append(elements)
        if region["porous"]:
            node_labels = region["node_labels"]
            nodes = (
                instance.nodes.sequenceFromLabels(labels=node_labels)
                if node_labels
                else instance.nodes
            )
            porous_node_groups.append(nodes)
    _replace_assembly_set(assembly, ACTIVE_ELEMENT_SET, element_groups, "elements")
    if porous_node_groups:
        _replace_assembly_set(
            assembly, ACTIVE_POROUS_NODE_SET, porous_node_groups, "nodes"
        )
    _apply_keyword_block(model, plan["mode"] == "COUPLED")
    return {
        "active_instances": active_names,
        "porous_instances": plan["porous_instances"],
    }


def audit_input_text(text, plan):
    upper = text.upper()
    errors = []
    if upper.count("TYPE=STRESS, USER") != 1:
        errors.append("expected exactly one user stress initial condition")
    pore_count = upper.count("TYPE=PORE PRESSURE, USER")
    expected_pore = 1 if plan["mode"] == "COUPLED" else 0
    if pore_count != expected_pore:
        errors.append("unexpected user pore-pressure initial-condition count")
    begin = upper.find(BEGIN_MARKER)
    end = upper.find(END_MARKER)
    if begin < 0 or end < begin:
        errors.append("generated GI2D keyword markers are missing")
        generated = ""
    else:
        generated = upper[begin : end + len(END_MARKER)]
    for removed in plan["first_step_removed"]:
        if removed.upper() in generated:
            errors.append("inactive instance appears in generated initial fields: %s" % removed)
    surfaces, ties = _parse_input_surfaces_and_ties(text)
    evidence = dict(
        (str(name).upper(), value)
        for name, value in plan.get("tie_surface_evidence", {}).items()
    )
    tolerance = 1.0e-8
    for tie in ties:
        referenced = (tie["secondary"], tie["main"])
        if not all(referenced):
            errors.append("Tie %s does not reference two surfaces" % tie["name"])
            continue
        spans = []
        coverages = []
        for surface_name in referenced:
            input_surface = _resolve_input_surface(surface_name, surfaces)
            if input_surface is None:
                errors.append(
                    "Tie %s references missing surface %s" % (tie["name"], surface_name)
                )
                continue
            if not input_surface:
                errors.append(
                    "Tie %s surface %s is empty" % (tie["name"], surface_name)
                )
            item = evidence.get(surface_name)
            if item is None:
                errors.append(
                    "Tie %s surface %s has no region/geometry evidence"
                    % (tie["name"], surface_name)
                )
                continue
            if int(item.get("entity_count", 0)) <= 0:
                errors.append(
                    "Tie %s surface %s has empty CAE geometry evidence"
                    % (tie["name"], surface_name)
                )
            span = item.get("span")
            expected = item.get("expected_span")
            if span is not None:
                spans.append((surface_name, tuple(span)))
            intervals = tuple(tuple(value) for value in item.get("intervals", ()))
            if not intervals and span is not None:
                intervals = (tuple(span),)
            coverages.append((surface_name, intervals))
            gaps = tuple(item.get("coverage_gaps", ()))
            if gaps:
                errors.append(
                    "Tie %s surface %s lacks continuous coverage; gaps=%s"
                    % (tie["name"], surface_name, gaps)
                )
            if span is not None and expected is not None:
                scale = max(abs(expected[1] - expected[0]), 1.0)
                if (
                    span[0] > expected[0] + tolerance * scale
                    or span[1] < expected[1] - tolerance * scale
                ):
                    errors.append(
                        "Tie %s surface %s has obvious partial geometry coverage"
                        % (tie["name"], surface_name)
                    )
        if len(spans) == 2:
            first = spans[0][1]
            second = spans[1][1]
            scale = max(abs(first[1] - first[0]), abs(second[1] - second[0]), 1.0)
            if abs(first[0] - second[0]) > tolerance * scale or abs(
                first[1] - second[1]
            ) > tolerance * scale:
                errors.append(
                    "Tie %s main/secondary surfaces have obvious partial span mismatch"
                    % tie["name"]
                )
        if len(coverages) == 2:
            first_intervals = coverages[0][1]
            second_intervals = coverages[1][1]
            if len(first_intervals) != len(second_intervals):
                errors.append(
                    "Tie %s main/secondary continuous coverage intervals differ"
                    % tie["name"]
                )
            else:
                for first_interval, second_interval in zip(
                    first_intervals, second_intervals
                ):
                    scale = max(
                        abs(first_interval[1] - first_interval[0]),
                        abs(second_interval[1] - second_interval[0]),
                        1.0,
                    )
                    if abs(first_interval[0] - second_interval[0]) > tolerance * scale or abs(
                        first_interval[1] - second_interval[1]
                    ) > tolerance * scale:
                        errors.append(
                            "Tie %s main/secondary continuous coverage intervals differ"
                            % tie["name"]
                        )
                        break
    return {"ok": not errors, "errors": tuple(errors), "ties": ties}


def _resolve_job(mdb, model_name, job_name=""):
    if job_name:
        return validate_job_model(mdb.jobs[job_name], model_name)
    matches = [job for job in mdb.jobs.values() if str(job.model) == str(model_name)]
    if len(matches) != 1:
        raise ValueError("select exactly one job for model %s" % model_name)
    return matches[0]


def _set_members(set_object, entity_name):
    members = []

    def visit(value, inherited_instance=""):
        if value is None:
            return
        if hasattr(value, "label"):
            instance_name = getattr(value, "instanceName", None)
            if instance_name is None:
                instance_name = getattr(
                    getattr(value, "instance", None), "name", inherited_instance
                )
            members.append((str(instance_name), int(value.label)))
            return
        next_instance = getattr(value, "instanceName", inherited_instance)
        try:
            for item in value:
                visit(item, next_instance)
        except TypeError:
            return

    visit(getattr(set_object, entity_name, ()))
    return tuple(members)


def regenerate_project_pair(model_name, output_dir, gravity=10.0, k0=1.0):
    from abaqus import mdb

    model = mdb.models[model_name]
    plan = inspect_model(model, gravity=gravity, k0=k0)
    paths = write_project_pair(output_dir, plan)
    return {"plan": plan, "paths": paths}


def regenerate_project_pair_3d(model_name, output_dir, gravity=10.0, k0=1.0):
    from abaqus import mdb

    model = mdb.models[model_name]
    plan = inspect_model_3d(model, gravity=gravity, k0=k0)
    paths = write_project_pair_3d(output_dir, plan)
    return {"plan": plan, "paths": paths}



def _generate_and_apply_precompute(mdb, model_name, job_name, output_dir,
                                    gravity=10.0, k0=1.0, is_3d=True):
    """Generate pre-computed initial conditions and inject into keyword block."""
    import tempfile
    model = mdb.models[model_name]
    job = _resolve_job(mdb, model_name, job_name)

    # Build plan (3D or 2D)
    if is_3d:
        plan = inspect_model_3d(model, gravity=gravity, k0=k0)
    else:
        plan = inspect_model(model, gravity=gravity, k0=k0)

    # Collect element centroids and node coords
    assembly = model.rootAssembly
    elements_data = []
    nodes_data = []

    for region in plan["regions"]:
        inst_name = region["instance"]
        instance = assembly.instances[inst_name]
        el_labels = region.get("element_labels", ())

        if el_labels:
            elements = instance.elements.sequenceFromLabels(labels=list(el_labels))
        else:
            elements = instance.elements

        for el in elements:
            # Compute element centroid
            try:
                nds = el.getNodes()
            except Exception:
                connectivity = tuple(int(v) for v in el.connectivity)
                inst_nodes = tuple(instance.nodes)
                nds = tuple(
                    inst_nodes[i] if 0 <= i < len(inst_nodes) else None
                    for i in connectivity
                )
                nds = tuple(n for n in nds if n is not None)
            if not nds:
                continue
            cx = sum(float(n.coordinates[0]) for n in nds) / len(nds)
            cy = sum(float(n.coordinates[1]) for n in nds) / len(nds)
            cz = sum(float(n.coordinates[2]) for n in nds) / len(nds)
            elements_data.append((int(el.label), inst_name, cx, cy, cz))

            # Collect unique nodes for pore pressure
            if region.get("porous"):
                for n in nds:
                    nodes_data.append((int(n.label), inst_name,
                                       float(n.coordinates[0]),
                                       float(n.coordinates[1]),
                                       float(n.coordinates[2])))

    # Pre-compute
    el_stresses, nd_pressures = precompute_all_stresses(
        plan, tuple(elements_data), tuple(nodes_data)
    )

    # Write keyword blocks
    staging_parent = os.path.abspath(output_dir)
    if not os.path.isdir(staging_parent):
        os.makedirs(staging_parent)
    staging_dir = tempfile.mkdtemp(prefix=".gi2d-pc-", dir=staging_parent)
    try:
        paths = write_project_pair_precompute(
            staging_dir, plan, el_stresses, nd_pressures
        )

        # Inject into model keyword block
        keyword_text = render_keyword_blocks(plan, el_stresses, nd_pressures)
        _inject_keyword_block(model, keyword_text)

        # Commit
        final_paths = _commit_staged_project_pair(paths, output_dir)
        # No Fortran subroutine to set on job
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    applied = apply_plan(model, plan)
    return {"plan": plan, "paths": final_paths, "applied": applied, "job": job.name}


def _inject_keyword_block(model, keyword_text):
    """Inject GI2D keyword block before the first *STEP in the model."""
    keyword_block = model.keywordBlock
    keyword_block.synchVersions(storeNodesAndElements=False)
    blocks = tuple(keyword_block.sieBlocks)

    # Remove existing GI2D blocks
    cleaned = []
    skipping = False
    for block in blocks:
        block_str = str(block)
        if "** GI2D BEGIN" in block_str:
            skipping = True
            if "** GI2D END" in block_str:
                skipping = False
            continue
        if skipping:
            if "** GI2D END" in block_str:
                skipping = False
            continue
        cleaned.append(block)

    step_index = next(
        (i for i, blk in enumerate(cleaned)
         if str(blk).lstrip().upper().startswith("*STEP")),
        len(cleaned),
    )
    cleaned.insert(step_index, keyword_text)

    # Replace all blocks
    for i in range(len(cleaned)):
        if i < len(blocks):
            keyword_block.replace(i, cleaned[i])
        else:
            keyword_block.insert(i, cleaned[i])
    for i in range(len(cleaned), len(blocks)):
        keyword_block.replace(i, "** GI2D superseded")




def _snapshot_generated_model_state(model):
    """Capture the plugin-owned model state needed for failure rollback."""
    assembly = getattr(model, "rootAssembly", None)
    saved_sets = {}
    if assembly is not None:
        for name in tuple(getattr(assembly, "sets", {}).keys()):
            if str(name).startswith("GI2D_"):
                item = assembly.sets[name]
                saved_sets[str(name)] = {
                    "elements": getattr(item, "elements", None),
                    "nodes": getattr(item, "nodes", None),
                }
    keyword_block = getattr(model, "keywordBlock", None)
    blocks = None
    if keyword_block is not None:
        keyword_block.synchVersions(storeNodesAndElements=False)
        blocks = tuple(keyword_block.sieBlocks)
    return {"sets": saved_sets, "keyword_blocks": blocks}


def _restore_generated_model_state(model, snapshot):
    assembly = getattr(model, "rootAssembly", None)
    if assembly is not None:
        for name in tuple(getattr(assembly, "sets", {}).keys()):
            if str(name).startswith("GI2D_"):
                try:
                    del assembly.sets[name]
                except Exception:
                    pass
        for name, contents in snapshot.get("sets", {}).items():
            kwargs = {}
            if contents.get("elements") is not None:
                kwargs["elements"] = contents["elements"]
            if contents.get("nodes") is not None:
                kwargs["nodes"] = contents["nodes"]
            if kwargs:
                assembly.Set(name=name, **kwargs)
    old_blocks = snapshot.get("keyword_blocks")
    keyword_block = getattr(model, "keywordBlock", None)
    if old_blocks is not None and keyword_block is not None:
        current = tuple(keyword_block.sieBlocks)
        for index, block in enumerate(old_blocks):
            if index < len(current):
                keyword_block.replace(index, block)
            else:
                keyword_block.insert(index, block)
        for index in range(len(old_blocks), len(current)):
            keyword_block.replace(index, "** GI2D rollback padding")


def _commit_staged_project_pair(staged_paths, output_dir):
    output_dir = os.path.abspath(output_dir)
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    final_paths = dict(
        (kind, os.path.join(output_dir, os.path.basename(path)))
        for kind, path in staged_paths.items()
    )
    originals = {}
    for kind, path in final_paths.items():
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                originals[kind] = handle.read()
        else:
            originals[kind] = None
    try:
        for kind, final_path in final_paths.items():
            os.replace(staged_paths[kind], final_path)
    except Exception:
        for kind, final_path in final_paths.items():
            content = originals[kind]
            if content is None:
                if os.path.isfile(final_path):
                    os.remove(final_path)
            else:
                temporary = final_path + ".rollback.tmp"
                with open(temporary, "wb") as handle:
                    handle.write(content)
                os.replace(temporary, final_path)
        raise
    return final_paths


def _generate_and_apply(
    mdb,
    model_name,
    job_name,
    output_dir,
    gravity=10.0,
    k0=1.0,
    inspector=inspect_model,
    writer=write_project_pair,
    applier=apply_plan,
    snapshotter=_snapshot_generated_model_state,
    restorer=_restore_generated_model_state,
):
    model = mdb.models[model_name]
    job = _resolve_job(mdb, model_name, job_name)
    plan = inspector(model, gravity=gravity, k0=k0)
    old_subroutine = str(getattr(job, "userSubroutine", ""))
    snapshot = snapshotter(model)
    staging_parent = os.path.abspath(output_dir)
    if not os.path.isdir(staging_parent):
        os.makedirs(staging_parent)
    staging_dir = tempfile.mkdtemp(prefix=".gi2d-stage-", dir=staging_parent)
    try:
        staged_paths = writer(staging_dir, plan)
        applied = applier(model, plan)
        final_fortran = os.path.abspath(
            os.path.join(output_dir, os.path.basename(staged_paths["fortran"]))
        )
        job.setValues(userSubroutine=final_fortran)
        paths = _commit_staged_project_pair(staged_paths, output_dir)
    except Exception:
        try:
            restorer(model, snapshot)
        except Exception:
            pass
        if hasattr(job, "setValues"):
            try:
                job.setValues(userSubroutine=old_subroutine)
            except Exception:
                pass
        raise
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    return {"plan": plan, "paths": paths, "applied": applied, "job": job.name}


def generate_and_apply(model_name, job_name, output_dir, gravity=10.0, k0=1.0, method="tin"):
    from abaqus import mdb

    model = mdb.models[model_name]
    is_3d = _model_has_3d_elements(model)

    # Pre-compute path: no Fortran, direct keyword injection
    if method == "precompute":
        return _generate_and_apply_precompute(
            mdb, model_name, job_name, output_dir,
            gravity=gravity, k0=k0, is_3d=is_3d,
        )

    # Fortran path (TIN or raycast) – use appropriate inspector/writer
    if is_3d:
        return _generate_and_apply(
            mdb,
            model_name,
            job_name,
            output_dir,
            gravity=gravity,
            k0=k0,
            inspector=inspect_model_3d,
            writer=write_project_pair_3d,
        )
    return _generate_and_apply(
        mdb,
        model_name,
        job_name,
        output_dir,
        gravity=gravity,
        k0=k0,
    )


def write_input_and_audit(model_name, job_name, gravity=10.0, k0=1.0):
    from abaqus import mdb
    from abaqusConstants import OFF

    model = mdb.models[model_name]
    plan = inspect_model(model, gravity=gravity, k0=k0)
    job = _resolve_job(mdb, model_name, job_name)
    subroutine_path = str(getattr(job, "userSubroutine", ""))
    source_text = ""
    if os.path.isfile(subroutine_path):
        with open(subroutine_path, "r") as handle:
            source_text = handle.read()
    assembly_sets = model.rootAssembly.sets
    element_set = (
        assembly_sets[ACTIVE_ELEMENT_SET]
        if ACTIVE_ELEMENT_SET in assembly_sets else None
    )
    pore_set = (
        assembly_sets[ACTIVE_POROUS_NODE_SET]
        if ACTIVE_POROUS_NODE_SET in assembly_sets else None
    )
    state_report = audit_generated_state(
        subroutine_path,
        source_text,
        plan,
        _set_members(element_set, "elements") if element_set is not None else (),
        _set_members(pore_set, "nodes") if pore_set is not None else (),
    )
    if not state_report["ok"]:
        state_report["input_path"] = ""
        return state_report
    job.writeInput(consistencyChecking=OFF)
    path = os.path.abspath(job.name + ".inp")
    with open(path, "r") as handle:
        report = audit_input_text(handle.read(), plan)
    report["errors"] = tuple(state_report["errors"]) + tuple(report["errors"])
    report["ok"] = not report["errors"]
    report["input_path"] = path
    return report


def dispatch_action(
    action="inspect",
    model_name="",
    job_name="",
    output_dir="",
    gravity=10.0,
    k0=1.0,
):
    from abaqus import mdb

    action = str(action).strip().lower()
    if not model_name:
        model_name = list(mdb.models.keys())[-1]
    if action == "inspect":
        model = mdb.models[model_name]
        if _model_has_3d_elements(model):
            return inspect_model_3d(model, gravity=gravity, k0=k0)
        return inspect_model(model, gravity=gravity, k0=k0)
    if action == "generate":
        return generate_and_apply(
            model_name, job_name, output_dir, gravity=gravity, k0=k0
        )
    if action == "audit":
        return write_input_and_audit(
            model_name, job_name, gravity=gravity, k0=k0
        )
    raise ValueError("unknown action: %s" % action)
