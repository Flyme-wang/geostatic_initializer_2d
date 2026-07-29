"""Abaqus/CAE registration for Geostatic Initializer 2D."""

from abaqusGui import *

from geostatic_initializer_form import GeostaticInitializerForm


toolset = getAFXApp().getAFXMainWindow().getPluginToolset()
toolset.registerGuiMenuButton(
    object=GeostaticInitializerForm(toolset),
    buttonText="Geostatic Initializer 2D",
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
    version="0.1.0",
    author="Codex",
    description=(
        "Generate staged-aware 2D SIGINI/UPOREP initial fields from the "
        "active Abaqus model."
    ),
)
