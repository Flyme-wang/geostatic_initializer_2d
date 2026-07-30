"""Minimal AFX dialog for Geostatic Initializer v2 (2D/3D)."""

from abaqusGui import *
from abaqusConstants import *


class GeostaticInitializerDB(AFXDataDialog):
    ID_INSPECT = AFXDataDialog.ID_LAST + 1
    ID_GENERATE = AFXDataDialog.ID_LAST + 2
    ID_AUDIT = AFXDataDialog.ID_LAST + 3

    def __init__(self, form):
        self.form = form  # store form ref for onCmdMethod
        AFXDataDialog.__init__(
            self,
            form,
            "Geostatic Initializer v2 (2D/3D)",
            0,
            DIALOG_ACTIONS_SEPARATOR | DIALOG_ACTIONS_RIGHT,
        )

        source = FXGroupBox(self, "Model and job", FRAME_GROOVE | LAYOUT_FILL_X)
        AFXTextField(source, 36, "Model name (blank = last):", form.modelNameKw, 0)
        AFXTextField(source, 36, "Job name:", form.jobNameKw, 0)
        AFXTextField(source, 52, "Project output directory:", form.outputDirKw, 0)

        physics = FXGroupBox(self, "Initial state", FRAME_GROOVE | LAYOUT_FILL_X)
        AFXTextField(physics, 12, "Gravity magnitude:", form.gravityKw, 0)
        AFXTextField(physics, 12, "Global K0:", form.k0Kw, 0)
        FXLabel(
            physics,
            "The first analysis step controls initial activation.\n"
            "Instances removed by ModelChange receive no initial stress or pore pressure.",
            None,
            JUSTIFY_LEFT,
        )

        method_gb = FXGroupBox(self, "Solving method", FRAME_GROOVE | LAYOUT_FILL_X)
        method_row = FXHorizontalFrame(method_gb, LAYOUT_FILL_X)
        FXLabel(method_row, "Method:")
        self.method_combo = FXComboBox(method_row, 25, 2, self, 1000 + 1,
                                       LAYOUT_FILL_X)
        self.method_combo.appendItem("TIN surface (Fortran SIGINI)")
        self.method_combo.appendItem("Pre-compute direct assign (no Fortran)")
        self.method_combo.setCurrentItem(0)
        self.method_info_btn = FXButton(method_row, " ? ", self, 1000 + 3)
        FXMAPFUNC(self, SEL_COMMAND, 1000 + 1, GeostaticInitializerDB.onCmdMethod)
        FXMAPFUNC(self, SEL_CHANGED, 1000 + 1, GeostaticInitializerDB.onCmdMethod)
        FXMAPFUNC(self, SEL_COMMAND, 1000 + 3, GeostaticInitializerDB.onCmdMethodInfo)
        self.method_value = "tin"
        form.methodKw.setValue("tin")

        for label, message_id in (
            ("Inspect model", self.ID_INSPECT),
            ("Generate and apply", self.ID_GENERATE),
            ("Write input and audit", self.ID_AUDIT),
        ):
            self.appendActionButton(label, self, message_id)
            FXMAPFUNC(self, SEL_COMMAND, message_id, AFXDataDialog.onCmdApply)
        self.appendActionButton(self.CANCEL)

    def onCmdMethodInfo(self, sender, sel, ptr):
        idx = self.method_combo.getCurrentItem()
        if idx == 0:
            msg = (
                "TIN Surface Method (Fortran SIGINI)\n"
                "================================\n\n"
                "Builds a Triangulated Irregular Network (TIN) from the top-surface\n"
                "node coordinates and the computed vertical stress profile.\n"
                "The Fortran SIGINI user subroutine interpolates the vertical stress\n"
                "at each integration point by locating the containing TIN triangle\n"
                "and performing barycentric interpolation.\n\n"
                "K0 consolidation:  sigma_h = K0 * sigma_v\n"
                "Requires a Fortran compiler to build the .for subroutine.\n"
                "Best suited for complex 3D geometries where the ground surface\n"
                "is irregular and element-level pre-computation is impractical."
            )
        else:
            msg = (
                "Pre-compute Direct Assign Method\n"
                "================================\n\n"
                "No Fortran subroutine needed.  Initial stresses are pre-computed\n"
                "in Python using the vertical stress profile and written directly\n"
                "into the Abaqus input deck via *INITIAL CONDITIONS, TYPE=STRESS.\n\n"
                "Each element is assigned its own stress tensor based on its\n"
                "centroid elevation.  Pore pressures are computed similarly.\n\n"
                "Advantages:  no Fortran compiler required; works with all\n"
                "Abaqus versions; stress values are visible in the .inp file\n"
                "for audit and manual verification.\n"
                "Best suited for models where a direct element-by-element\n"
                "assignment is sufficient and a Fortran toolchain is unavailable."
            )
        showAFXInformationDialog(self, msg)

    def onCmdMethod(self, sender, sel, ptr):
        idx = sender.getCurrentItem()
        if idx == 0:
            self.method_value = "tin"
            self.form.methodKw.setValue("tin")
        else:
            self.method_value = "precompute"
            self.form.methodKw.setValue("precompute")
