"""bciflow: LSL-based BCI toolkit.

Acquisition (BioSemi bridge, replay, synthetic) publishes Lab Streaming Layer
streams; a declarative node graph consumes them and runs MNE/SciPy/scikit-learn
processing. The same graph runs online (LSL) and offline (files) so that
training and online use identical, causal preprocessing.
"""

__version__ = "0.1.0"

# Default stream names shared by the BioSemi bridge, replay and the synthetic
# generator, so a pipeline cannot tell live data from a simulation.
DEFAULT_EEG_STREAM = "BioSemi"
DEFAULT_MARKER_STREAM = "BioSemi-Triggers"
