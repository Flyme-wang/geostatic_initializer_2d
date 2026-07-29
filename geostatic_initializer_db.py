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

        for label, message_id in (
            ("Inspect model", self.ID_INSPECT),
            ("Generate and apply", self.ID_GENERATE),
            ("Write input and audit", self.ID_AUDIT),
        ):
            self.appendActionButton(label, self, message_id)
            FXMAPFUNC(self, SEL_COMMAND, message_id, AFXDataDialog.onCmdApply)
        self.appendActionButton(self.CANCEL)
