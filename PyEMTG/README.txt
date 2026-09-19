PyEMTG scientific helpers and historical GUI

PyEMTG.Results provides independent native-result parsing and scientific
artifact inventories. Option helpers and optional OuterLoop search can also be
used without launching the historical GUI. See ../README.md, ../SUPPORT.md,
and ../docs/0_Users/native_results.md for the current interfaces and scope.

The wxPython application in PyEMTG.py is retained as a historical developer
workflow. Its Python, wxPython, plotting, and scientific-library dependencies
require separate installation and qualification. The managed command-line
release does not include or qualify that GUI. No current GUI dependency
combination, legacy Python distribution, or wxPython version is recommended
on the strength of the command-line or Results tests.

For GUI development, inspect the source imports and configure PyEMTG.options
for the local EMTG executable and mission data. Record the tested Python and
library versions when reporting a working configuration or a problem. Older
GUI instructions in archived NASA documentation describe historical setups.
