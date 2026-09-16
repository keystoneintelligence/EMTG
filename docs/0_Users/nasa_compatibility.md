# NASA option compatibility

The default open-source backend is IPOPT. General feasibility tolerance is
NASA's 1e-5; strict IPOPT qualification cases explicitly choose 1e-8.
Solver tolerances and scientific acceptance envelopes are separate controls.
Fixed-step integration remains the default. Adaptive integration, derivative,
spline, cache, and automatic-differentiation changes require their own checks.

Export a supported options file for NASA's pinned upstream vocabulary:

```python
from PyEMTG.nasa_compatibility import export_nasa_options
report = export_nasa_options('mission.emtgopt', 'nasa.emtgopt', solver='SNOPT')
```

The export uses NASA option names and explicit SNOPT selection. Unknown options
and nondefault fork-only settings fail before writing. Inactive extension
defaults omitted from the file are listed in the returned report. Native
constraint blocks, trial vectors and physical tolerances retain their values.
This is format compatibility, not proof of identical optimized trajectories.
The pinned NASA revision is recorded in `PyEMTG/nasa_options.json`.

`EMTG_NASA_PYEMTG` enables tests against an unmodified NASA Python checkout.
Missing requested assets fail qualification. Licensed SNOPT execution is a
separate gate: use testatron's `--emtg_solver SNOPT`, NASA solver tolerances
`--emtg_feasibility_tolerance 1e-5 --emtg_optimality_tolerance 1e-5`, and the
historical Comparatron tolerance (default 1e-10). Do not regenerate truth files
or widen scientific comparisons to make solver changes pass.
