"""ImpulseCalc — mean-line + OpenFOAM cascade + technical video (standalone)."""

from .meanline import MeanlineInputs, MeanlineResult, compute_meanline

__version__ = "1.0.0"
__all__ = ["MeanlineInputs", "MeanlineResult", "compute_meanline", "__version__"]
