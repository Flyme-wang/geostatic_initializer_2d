"""Minimal AFX dialog for Geostatic Initializer 2D."""

from abaqusGui import *
from abaqusConstants import *


class GeostaticInitializerDB(AFXDataDialog):
    ID_INSPECT = AFXDataDialog.ID_LAST + 1
    ID_GENERATE = AFXDataDialog.ID_LAST + 2
    ID_AUDIT = AFXDataDialog.ID_LAST + 3

    def __init__(self, form):
        AFXDataDialog.__init__(
            self,
            form,
            "Geostatic Initializer 2D",
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
        self.method_tin = AFXRadioButton(method_gb, "TIN Interpolation (Fortran SIGINI, fast)", None, 0, LAYOUT_FILL_X)
        self.method_precompute = AFXRadioButton(method_gb, "Pre-compute & Direct Assign (no Fortran, robust)", None, 0, LAYOUT_FILL_X)
        self.method_tin.setCheck(True)
        self.method_tin.setSelector(1000 + 1)
        self.method_precompute.setSelector(1000 + 2)
        FXMAPFUNC(self, SEL_COMMAND, 1000 + 1, GeostaticInitializerDB.onCmdMethod)
        FXMAPFUNC(self, SEL_COMMAND, 1000 + 2, GeostaticInitializerDB.onCmdMethod)
        self.method_value = "tin"
        form.methodKw.setValue("tin")

        for label, message_id in (
            ("Inspect model", self.ID_INSPECT),
            ("Generate and apply", self.ID_GENERATE),
            ("Write input and audit", self.ID_AUDIT),
        ):
            self.appendActionButton(label, self, message_id)
            FXMAPFUNC(self, SEL_COMMAND, message_id, AFXDataDialog.onCmdApply)
    def onCmdMethod(self, sender, sel, ptr):
        if sender == self.method_tin:
            self.method_value = "tin"
            self.form.methodKw.setValue("tin")
        elif sender == self.method_precompute:
            self.method_value = "precompute"
            self.form.methodKw.setValue("precompute")

        self.appendActionButton(self.CANCEL)
