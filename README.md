# EMTG Community Edition

Space mission design builds on decades of shared research. **EMTG Community
Edition** carries NASA's Evolutionary Mission Trajectory Generator forward with
open-source solver access, simpler builds, and Python interfaces that connect
mission results to your own analysis and automation.

This is Keystone Intelligence's community fork of [NASA EMTG](https://github.com/nasa/EMTG),
originally authored by Jacob Englander and the EMTG team. NASA authorship and
notices are retained; NASA does not maintain or endorse Community Edition.

[Project story](https://www.keystoneintelligence.ai/space/emtg) ·
[Downloads](https://github.com/keystoneintelligence/EMTG/releases) ·
[Build guide](BUILDING.md) · [Documentation](docs/README.md) ·
[Contribute](CONTRIBUTING.md)

## What Community Edition brings

| Your starting point | Community Edition |
| --- | --- |
| Explore a mission concept | Design and optimize trajectories with NASA's native mission options and scientific outputs. |
| Start with an open-source solver | IPOPT is the default. Public CLI builds need no commercial solver license; SNOPT remains an explicit option for licensed source builds. |
| Get from source to software | Managed build scripts provision pinned dependencies, run checks, and produce portable CLI bundles. |
| Connect your workflow | Parse native results with `PyEMTG.Results`, or use optional OuterLoop search and continuation. Build application-specific analysis and publication around these interfaces. |
| Build confidence together | Automated option, parser, numerical, native-solver, and packaged-runtime regressions provide a foundation for shared mission test cases. |

The focus is an accessible, general-purpose trajectory engine. You can use it
independently or connect it to the tools you already use. No application service
or commercial solver is required for the managed IPOPT CLI.

## Get started

Download an available bundle from [Releases](https://github.com/keystoneintelligence/EMTG/releases)
and follow [INSTALLING.md](INSTALLING.md), or build from source:

```text
git clone https://github.com/keystoneintelligence/EMTG.git
cd EMTG
```

After installing the [prerequisites](BUILDING.md#complete-bootstrap-prerequisites),
run the appropriate command from a short checkout path:

```powershell
# Windows x64
.\build.ps1
```

```bash
# Ubuntu 22.04 x64, experimental
./build.sh --bootstrap
```

Builds write artifacts to `dist`. Windows x64 is the primary CLI release target;
Ubuntu 22.04 x64 remains experimental. The managed graph has passed hosted
Windows/Linux build, packaging, and relocation checks. See the
[support matrix](SUPPORT.md) and [qualification evidence](docs/1_Developers/qualification.md)
for the tested scope. macOS is a contribution target and is currently unqualified.

Bundles include standard runtime definitions and small text kernels. Supply the
SPICE BSP kernels appropriate to your mission, then use `EMTGv9 --doctor` to
check runtime data and solver availability. Missing mission kernels require
action; they are not a successful mission setup.

Native regressions use a separate [18.8 MB compressed ephemeris fixture](tests/fixtures/ephemeris/README.md)
with recorded hashes and offline staging. It covers the included tests only.

## Build around the engine

`PyEMTG.Results` uses Python's standard library and imports independently of a
GUI or service:

```python
from PyEMTG.Results import EMTGResultParser

result = EMTGResultParser().parse("mission.emtg")
print(result.complete, result.feasible, result.failure_reason)
```

Results retain the distinction between incomplete output, infeasibility, and
execution failure. The [native result interface](docs/0_Users/native_results.md)
also exposes a versioned artifact inventory with hashes, units, and declared
journey context. Optional [OuterLoop](docs/0_Users/outerloop.md) provides search,
continuation, checkpoints, and computational caches. Your caller owns adapters,
mission policy, and publication.

Python qualification uses **3.12**. Historical GUI and optional extension
configurations have separate dependency and qualification requirements.

## Scientific continuity

Native mission formats remain available. The general feasibility tolerance is
`1e-5`, and fixed-step integration remains the default; adaptive integration is
experimental. For an existing option file, explicitly select `NLP_solver_type`:
`0` is SNOPT and `2` is IPOPT. A file omitting the old SNOPT default selects IPOPT
in Community Edition.

Different solvers may converge to different trajectories. Passing the included
regressions is evidence for those cases, not a promise of identical optima or
complete equivalence across every NASA workflow. Read the
[solver migration guide](docs/1_Developers/ipopt.md) and
[NASA-compatible options guide](docs/0_Users/nasa_compatibility.md).

## Help shape the next chapter

Bring a mission question, share a reproducible test case, improve a build, or
contribute a focused fix. We welcome researchers, engineers, and developers
working to make trajectory design easier to use and easier to validate.

Start with [CONTRIBUTING.md](CONTRIBUTING.md) or open an
[issue](https://github.com/keystoneintelligence/EMTG/issues). Include the version,
platform, solver, options, and kernel provenance needed to reproduce your result.
Community support depends on maintainer availability.

The source retains the [NASA Open Source Agreement](EMTG_NOSA_License.pdf),
[NASA notices and disclaimers](README.opensource), and
[third-party notices](THIRD_PARTY_NOTICES.md). Credit NASA's original EMTG work
and identify Community Edition when reporting results produced with this fork.
