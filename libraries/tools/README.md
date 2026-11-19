# Mascope Tools

**Mascope Tools** is a Python library providing utilities for data processing, analysis, and visualization, designed to support the Mascope platform and related scientific workflows.

## Features

- **Alignment Tools**  
  Utilities for calibration and alignment of datasets.
  - [`alignment/calibration.py`](src/mascope_tools/alignment/calibration.py): Calibration routines and helpers.
  - [`alignment/utils.py`](src/mascope_tools/alignment/utils.py): Supporting functions for alignment tasks.

- **Composition Assignment**  
  Tools for chemical composition analysis and filtering.
  - [`composition/config.py`](src/mascope_tools/composition/config.py): Domain-specific constants and configuration.
  - [`composition/finder.py`](src/mascope_tools/composition/finder.py): Algorithms for finding chemical compositions.
  - [`composition/heuristic_filter.py`](src/mascope_tools/composition/heuristic_filter.py): Heuristic filters for composition candidates.
  - [`composition/models.py`](src/mascope_tools/composition/models.py): Data models for composition analysis.
  - [`composition/mass_accuracy.py`](src/mascope_tools/composition/mass_accuracy.py): A sample's fitted mass accuracy and the width a fit is judged at.
  - [`composition/scoring.py`](src/mascope_tools/composition/scoring.py): What a sample is scored at (`pattern_scoring_for_model`, `pattern_scoring_for_sample`), and candidates scored against a peak list in bulk (`score_candidates`).
  - [`composition/measurement_model.py`](src/mascope_tools/composition/measurement_model.py): The one measurement model a run scores and caps at (`MeasurementModel`): centre, width and line tolerance, fitted at Stage A (v1) and on the run's corroborated commits (v2), recorded on the run and read back with `MeasurementModel.from_run_config`.
  - [`composition/utils.py`](src/mascope_tools/composition/utils.py): Utility functions for composition workflows.
