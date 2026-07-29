"""AFX command form for Geostatic Initializer 2D."""

from abaqusGui import *

from geostatic_initializer_db import GeostaticInitializerDB


class GeostaticInitializerForm(AFXForm):
    def __init__(self, owner):
        AFXForm.__init__(self, owner)
        self.cmd = AFXGuiCommand(
            self, "dispatch_action", "geostatic_initializer_kernel", True
        )
        self.actionKw = AFXStringKeyword(self.cmd, "action", True, "inspect")
        self.modelNameKw = AFXStringKeyword(
            self.cmd, "model_name", True, "Model-3Layer-Screenshot-22000"
        )
        self.jobNameKw = AFXStringKeyword(self.cmd, "job_name", True, "")
        self.outputDirKw = AFXStringKeyword(self.cmd, "output_dir", True, "")
        self.gravityKw = AFXFloatKeyword(self.cmd, "gravity", True, 10.0)
        self.k0Kw = AFXFloatKeyword(self.cmd, "k0", True, 1.0)
        self.methodKw = AFXStringKeyword(self.cmd, "method", True, "tin")

    def getFirstDialog(self):
        return GeostaticInitializerDB(self)

    def doCustomChecks(self):
        dialog = self.getCurrentDialog()
        actions = {
            dialog.ID_INSPECT: "inspect",
            dialog.ID_GENERATE: "generate",
            dialog.ID_AUDIT: "audit",
        }
        self.actionKw.setValue(actions.get(self.getPressedButtonId(), "inspect"))
        if self.gravityKw.getValue() <= 0.0:
            showAFXErrorDialog(
                getAFXApp().getAFXMainWindow(), "Gravity magnitude must be positive."
            )
            return False
        if self.k0Kw.getValue() < 0.0:
            showAFXErrorDialog(
                getAFXApp().getAFXMainWindow(), "K0 must be nonnegative."
            )
            return False
        if self.actionKw.getValue() == "generate" and not self.outputDirKw.getValue().strip():
            showAFXErrorDialog(
                getAFXApp().getAFXMainWindow(), "Enter a project output directory."
            )
            return False
        return True
