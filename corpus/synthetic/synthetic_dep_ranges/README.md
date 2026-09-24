# synthetic_dep_ranges

Positive control: the manifest mixes a bare name (torch), a lower bound
(scipy>=1.10.0), an upper bound (requests<=2.32.0), and an exact pin
(numpy==1.26.4). R-DEP-001 flags the three unbounded declarations with their
specifiers, and R-DEP-002 flags the two numerics-bearing libraries that are
not pinned exactly.
