# EMTG community fork

Evolutionary Mission Trajectory Generator (EMTG) designs and optimizes space
mission trajectories. This Keystone Intelligence community fork builds on
[NASA's EMTG](https://github.com/nasa/EMTG). NASA authorship and notices are
preserved; NASA does not maintain or endorse this fork.

**Status: community release candidate. Fresh-machine release qualification is
pending.** Existing development checks support the configurations described in
[SUPPORT.md](SUPPORT.md); they do not establish compatibility with every NASA
workflow or every operating system.

## Improvements

- IPOPT is the default open-source nonlinear solver. Licensed SNOPT remains an
  explicit optional backend and is excluded from public bundles.
- Managed Windows and experimental Linux builds pin their dependencies and
  produce portable bundles with dependency notices and runtime-data discovery.
- `PyEMTG.Results` parses native scientific results independently of a GUI or
  service. Optional OuterLoop search, continuation, and local caches remain
  available; applications own their translation and publication policy.
- Regression checks cover options, results, numerical helpers, bounded native
  solves, relocation, and private build paths in release artifacts.

Native scientific formats are retained. General feasibility defaults to `1e-5`,
fixed-step integration remains the default, and adaptive integration is
experimental. IPOPT and SNOPT can converge to different solutions; passing the
tested cases does not establish identical results for all missions.

## Build or run

After installing the [prerequisites](BUILDING.md#complete-bootstrap-prerequisites):

```powershell
# Windows x64, from PowerShell
.\build.ps1
```

```bash
# Ubuntu 22.04 x64, experimental
./build.sh --bootstrap
```

Builds write artifacts to `dist`. See [BUILDING.md](BUILDING.md) for developer
configurations and [INSTALLING.md](INSTALLING.md) for running a portable bundle.
Large SPICE BSP kernels are separate scientific inputs and must be supplied for
the selected mission. Keep Windows checkout and run paths short.

The bounded native regression tests have a separate
[18.8 MB checked-in ephemeris fixture](tests/fixtures/ephemeris/README.md), with
offline staging and recorded hashes. It covers those tests only.

For existing option files, explicitly select the intended solver: `0` is SNOPT
and `2` is IPOPT. Files that omitted NASA's old SNOPT default now select IPOPT.
See [solver semantics and migration](docs/1_Developers/ipopt.md) and
[native result interfaces](docs/0_Users/native_results.md).

## Support and contributions

Use this fork's [issues](https://github.com/keystoneintelligence/EMTG/issues) for
fork-specific problems. Community support is provided as maintainer time allows.
See [CONTRIBUTING.md](CONTRIBUTING.md), the [support matrix](SUPPORT.md), and the
[release checklist](docs/1_Developers/releasing.md).

The source retains the [NASA Open Source Agreement](EMTG_NOSA_License.pdf),
[NASA notices and disclaimers](README.opensource), and
[third-party notices](THIRD_PARTY_NOTICES.md). Please credit NASA's original EMTG
work and identify this fork when reporting results produced with it.
