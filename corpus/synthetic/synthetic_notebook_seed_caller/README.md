# synthetic_notebook_seed_caller

Control: `utils.py` defines `set_seed`, which seeds python and numpy. No Python
module imports it; the only caller is `experiment.ipynb`, which imports and
calls it before drawing from numpy.

R-DET-001 must pass: a non-test notebook is project code, so its import counts
as a reference to the helper.
