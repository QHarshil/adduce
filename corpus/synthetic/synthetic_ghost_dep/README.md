# synthetic_ghost_dep

Positive control: the code imports cv2 (opencv-python) and yaml (pyyaml) but
the manifest declares only numpy. R-DEP-010 must flag the two ghosts.
