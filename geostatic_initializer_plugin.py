"""Abaqus/CAE registration for Geostatic Initializer v2 (2D/3D)."""

from abaqusGui import *

from geostatic_initializer_form import GeostaticInitializerForm


toolset = getAFXApp().getAFXMainWindow().getPluginToolset()
toolset.registerGuiMenuButton(
    object=GeostaticInitializerForm(toolset),
    buttonText="Geostatic Initializer v2 (2D/3D)",
    kernelInitString="import geostatic_initializer_kernel",
    applicableModules=(
        "Part",
        "Property",
        "Assembly",
        "Step",
        "Interaction",
        "Load",
        "Mesh",
        "Job",
        "Visualization",
        "Optimization",
    ),
    version="2.0.0",
    author="Flyme-wang",
    description=(
        "Generate staged-aware 2D/3D SIGINI/UPOREP initial fields with "
        "TIN interpolation or pre-computed direct assignment."
    ),
)
