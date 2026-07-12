# EMTG Studio

EMTG Studio is the local browser interface for mission editing, autonomous
outer-loop searches, solution history, and time-aware trajectory comparison.
It binds only to the loopback interface and opens a tokenized local URL.

## Start from source

Install `requirements-studio.txt`, build the frontend once, and launch from the
repository root:

```powershell
cd PyEMTG\Studio\frontend
npm.cmd ci
npm.cmd run build
cd ..\..\..
python -m PyEMTG.Studio
```

The release bundle uses `EMTGStudio.exe` and does not require a separate Node
installation. The browser can be closed independently; stopping the launcher
pauses the active worker at its next recoverable checkpoint.

Each extracted suite owns its mutable state under `_local/studio` beside
`EMTGStudio.exe`. This includes the SQLite catalog and app-owned campaign
artifacts. It is created on first launch and is not included in the release
archive, so separate extracted copies do not share a solution history.

## Mission View

- Use the catalog filters to select a start body, end body, dates, feasibility,
  propellant, thrust, or campaign.
- Check up to ten solutions to overlay them. The complete path remains visible
  while the bright segment and spacecraft marker follow the time controller.
- Earth and the selected arrival body are enabled as SPICE body tracks by
  default. The scene tree can add or remove other kernel-backed planets, moons,
  barycenters, and asteroids. Each body shows its full path over the cumulative
  selected-mission span, a bright elapsed tail, and a marker synchronized to
  the main time controller.
- The inspector shows scalar metrics and can request a dense propagation-only
  ephemeris. Event trajectories remain available while that task is queued.

## Campaign Planner

The new-search form defaults to a real EMTG configuration discovered from the
local solver, universe, hardware, and base-mission assets. The status banner
must say **Real EMTG ready** before a real campaign can be queued. The synthetic
evaluator is available only through the explicit **Use synthetic debug
evaluator** checkbox; its results are nonphysical qualification data and every
such job carries a yellow **SYNTHETIC** badge.

Start and end bodies use searchable selectors populated from the SPICE objects
covered by the active mission universe. Typing filters the native dropdown, and
the selected start body is removed from the end-body choices (and vice versa).
Each option shows its SPICE ID and object category.

Expand **View immutable run JSON** on any queue entry to inspect the exact
campaign snapshot used by that run. **Delete run** permanently removes an
inactive job, all of its solution and trajectory catalog rows, and its managed
run directory. A running job must be cancelled before it can be deleted.

Only one campaign runs at a time; its evaluations use the lower of the global
and per-job core limits. Core changes apply on the next recoverable evaluation
batch. Jobs and completed solutions persist in `_local/studio/studio.sqlite`.

## Mission Configuration

The editor reads the generated mission/journey option metadata and opens or
saves `.emtgopt` files under the selected workspace. Global, spacecraft,
journey, solver, physics, and output settings are grouped and searchable.

## Build the Windows bundle

Install the Studio Python requirements, then run:

```powershell
packaging\studio\build.ps1
```

This creates the one-folder bundle under `dist\EMTGStudio`.

To create the distributable Windows archive, run:

```powershell
packaging\studio\build.ps1 -ReleaseArchive
```

The resulting `dist\EMTGStudio-windows-x64.zip` is created before the local
developer state is restored and is verified to contain no `_local` entries.
