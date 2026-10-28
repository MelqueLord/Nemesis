"""Configura os dados do Tcl/Tk antes da importação da interface no executável."""

import os
from pathlib import Path
import sys


if hasattr(sys, "_MEIPASS"):
    raiz = Path(sys._MEIPASS)
    os.environ["TCL_LIBRARY"] = str(raiz / "_tcl_data")
    os.environ["TK_LIBRARY"] = str(raiz / "_tk_data")
