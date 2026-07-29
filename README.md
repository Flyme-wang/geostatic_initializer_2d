# Geostatic Initializer 2D

This Abaqus/CAE 2024 plugin generates a project-local
`generate_geostatic_initial_fields.py` and `initial_fields_generated.for` pair.
It detects regions removed in the first analysis step and excludes those staged
deposits from initial stress and pore pressure. This is required for
`Model-3Layer-Screenshot-22000`: only `Part-Gray` is initialized; `Part-Purple`
and `Part-LightRed` enter later without inherited initial stress or pressure.

## Install

Copy the complete `geostatic_initializer_2d` directory into an Abaqus plugin
search directory, for example:

```text
C:\Users\<user>\abaqus_plugins\geostatic_initializer_2d
```

Restart Abaqus/CAE. The command appears at
`Plug-ins -> Geostatic Initializer 2D` in the Property, Load, Mesh, and Job
modules.

## Use

1. Mesh the 2D layered model and configure its first geostatic/deposition step.
2. Open the plugin and run **Inspect model**.
3. Enter an existing job name and a project output directory.
4. Run **Generate and apply**. The plugin creates active assembly sets, injects
   one guarded `*INITIAL CONDITIONS, TYPE=STRESS, USER` block and, for coupled
   models, one pore-pressure USER block, then binds the generated `.for` to the
   job. It does not submit the job.
5. Run **Write input and audit** before submission.

The project-local Python entry can be run later inside Abaqus/CAE to regenerate
the sibling Fortran file after geometry, mesh, or material changes.

## Current limits

- Two-dimensional planar or axisymmetric layered continuum meshes only.
- Multiple section/material assignments in one instance are split by local
  element label and mapped in `SIGINI` using `GETPARTINFO`.
- Constant density, void ratio, saturation, fluid specific weight, and K0.
- Supported linear pore elements are listed in `geostatic_initializer_core.py`.
- Profiles are extracted from element free-boundary edges, so a valid mapped
  CPE4/CPE4P mesh may use different x stations on its upper, lower, and internal
  rows. Each material region must still have exactly two continuous,
  single-valued, non-crossing upper/lower boundary chains; holes, branches, and
  non-single-valued boundaries are blocked instead of guessed.
- Initial head follows the active local ground surface. Perched or disconnected
  aquifers are not supported.
- Every porous region must have readable, active void-ratio and saturation
  fields; missing or ambiguous coverage blocks generation instead of defaulting.
- When one instance contains multiple porous section regions with independent
  void-ratio or saturation fields, the current kernel cannot yet disambiguate
  those fields by section-node coverage and blocks generation explicitly.
- Abaqus/CAE does not expose USER-defined `SIGINI` stress through its initial
  condition API. The MVP therefore injects a marker-guarded model-data keyword
  block and uses `keywordBlock` again for input auditing.
- The kernel API calls that create union assembly sets and edit `keywordBlock`
  require a live Abaqus/CAE validation pass before production analysis.
