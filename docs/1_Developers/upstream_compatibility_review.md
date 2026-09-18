# Upstream compatibility review — 2026-09-18

NASA's current `master` and HEAD were checked directly and both identify
`5e8817f7ecd51242684359ca8e23d1e55f3895c0`. The comparison baseline is the prepared
community candidate `a9a9c4bfdab980cea9da98f9fe6088e61e1460aa`; the small test
ephemeris and compatibility fixes described below are subsequent changes.

## Conclusion and limits

The comparison found no deleted NASA tracked files or removed mission models,
phase/objective/state-representation choices, or journey option definitions.
It does **not** establish that every NASA workflow or numerical result remains
equivalent. The review found and fixed a plotting regression, two optional Windows
build/install regressions, and one low-level tolerance-default inconsistency.
Do not describe this candidate as fully NASA-compatible solely from these checks.

The IPOPT CLI can be qualified independently with its declared scope. Complete
NASA workflow preservation also needs licensed SNOPT, optional Python extensions,
the historical GUI and mission-level comparison runs. Those are not established
by source retention, parser tests, or a different solver's passing cases.

## Inventory and retained capabilities

NASA has 35,666 tracked files. The reviewed candidate has 35,862: 196 additions,
104 modified upstream files, and zero deletions. Of the upstream files, 65 C++
source/build files and 16 PyEMTG files changed. Original NASA history and notices
remain in the prepared public history.

| Boundary | Evidence |
| --- | --- |
| Phase types, objectives, state representations | `src/Core/EMTG_enums.h` is byte-identical; phase dispatch remains present. |
| Objective functions | All 47 upstream files under `src/Mission/ObjectiveFunctions` are unchanged. |
| Hardware models | All 33 upstream files under `src/HardwareModels` are unchanged. |
| Force/acceleration models | All 23 upstream files under `src/Astrodynamics/AccelerationModel` are unchanged. |
| Boundary events and constraints | All 208 original files are retained; only `BoundaryEventBase.cpp` changes, guarding a spline-only lookup when SPICE/static ephemerides are selected. |
| Scalatron | All seven upstream files are unchanged; solver orchestration still invokes it. |
| Journey options | All 130 authoritative definitions are unchanged. |
| Mission options | All 166 NASA definitions are represented, allowing five compatibility aliases. The shared definition changes only the solver default/range; 17 new controls are added. |
| Historical option files | Both parsers successfully read all 137 original testatron option files. All shared mission/journey values match after alias mapping, except 111 files that inherit the intentionally changed solver default. |
| NASA option export | Six tests pass with the unmodified NASA parser supplied, including the normally optional parser check. |
| Historical Python APIs | No top-level class/function or class method disappeared from modified modules except five renamed GUI event handlers, whose new names are bound to their controls. |
| MBH, direct NLP, filament search | Retained through the common solver interface; SNOPT code remains behind its opt-in build flag. WORHP was already deprecated upstream. |
| PEATSA and legacy population tools | Retained. PEATSA changes reference the solver-neutral names, with legacy Python option aliases also retained. |
| Propulator and PyHardware | Source, optional build targets and install rules retained; Windows filenames restored as described below. Full ABI/import qualification remains separate. |

The five option aliases are `snopt_feasibility_tolerance` →
`NLP_feasibility_tolerance`, `snopt_optimality_tolerance` →
`NLP_optimality_tolerance`, `snopt_major_iterations` → `NLP_iteration_limit`,
`snopt_max_run_time` → `NLP_max_run_time`, and `NLP_max_step` →
`snopt_major_step_limit`. Both native and Python parsers accept the old spellings.

## Findings

1. **Fixed: Gregorian axes in the legacy population viewer.** Moving plotting
   dependencies to lazy imports omitted `matplotlib.ticker`, although the date
   axis code still referenced it. Restored the lazy import. The focused formatter
   regression fails against the previous candidate and passes with the fix.
   This is not a qualification of the whole wxPython GUI or every Matplotlib version.
2. **Fixed: Windows Python extension filenames.** The build refactor removed
   the old post-build `.dll` → `.pyd` copies. Both optional targets now directly
   produce prefix-free `.pyd` files on Windows. A compiled CMake fixture verifies
   their build/install filenames. Actual module imports still need qualification
   with matching Python/Boost.Python; the fixture does not establish that ABI.
3. **Fixed: Windows dynamic SNOPT runtime staging.** The former Windows
   SNOPT DLL copy/install handling was removed with the old output layout. The
   new runtime staging/install logic previously handled IPOPT DLLs only. Optional
   SNOPT builds now stage and install the legacy runtime layouts, including
   configuration-specific paths, with `EMTG_SNOPT_RUNTIME_DLL` for custom layouts.
   CMake fixtures check copied contents, static/disabled exclusion and missing
   overrides. Public IPOPT-only builds continue excluding SNOPT. Licensed solver
   execution and its additional vendor runtime dependencies remain unqualified.
4. **Fixed: direct `NLPoptions()` construction.** The low-level constructor now
   matches the mission-level C++ and Python default of `1e-5`. A native regression
   checks direct/default-mission construction and preservation of caller-selected
   settings. Strict IPOPT tests retain their explicit `1e-8` tolerance; ordinary
   mission-driven construction still copies the mission setting unchanged.
5. **Test reproducibility: native MBH random seed.** One final bounded test run
   failed to find a feasible solution within its existing five-second budget.
   Its base options used `MBH_RNG_seed=-1` (clock seed); OuterLoop's request seed
   currently assigns the separate `seed_MBH` initial-guess flag, not the native
   RNG seed. Fixed-seed inputs produced feasible solutions with both the prior
   and rebuilt executables. The smoke fixture now explicitly selects native seed
   `7`, preserving budgets, strict tolerances and all scientific assertions.
   Production OuterLoop seed mapping is unchanged to avoid silently changing
   caller search behavior. Callers needing reproducible native MBH must set
   `MBH_RNG_seed` in their base options; correcting that mapping is separate work.

## Intentional differences requiring scientific qualification

- IPOPT (`2`) replaces SNOPT (`0`) as the default. Old files omitting solver
  selection therefore choose IPOPT. SNOPT remains an explicitly selected,
  licensed option; solver outputs need not have equal optima.
- Solver-neutral option output needs the explicit NASA exporter for NASA's
  vocabulary. New fork-only numerical settings cannot silently pass through it.
- Internal C++ solver option/accessor names changed. Retaining legacy file and
  Python option aliases does not establish a stable C++ API/ABI for external code.
- Natural cubic splines replace GSL storage/evaluation in ephemeris and covariance
  readers. Spline support remains, but broad mission equivalence is not implied.
- Derivative boundary guards, evaluation caching, AD and integration changes
  require their numerical tests and mission-level evidence. Fixed-step integration
  stays the default; the adaptive implementation and tolerance mapping changed.
- Thrust-output unit corrections and the ephemeris-reader mass-slot correction
  intentionally fix upstream defects. They should not be reverted to make a
  byte-for-byte comparison with defective output pass.
- Kernel load order is deterministic. Overlapping BSPs can change science when
  users select a different inventory; record hashes and precedence.
- CLI data discovery, explicit missing-input failures and install locations
  changed. Scripts relying on old implicit paths or `.SNOPTcrash` filenames
  require migration; generic solver crash output now uses `.NLPcrash`.
- Universe numeric fields are parsed as numbers instead of evaluated Python
  expressions. Arbitrary expressions in those fields are not preserved behavior.

The historical testatron comparison threshold remains `1e-10` by default.
The separate smoke selection has its own relaxed smoke policy and must not be
used as evidence of NASA historical scientific equivalence. Stored NASA truth
outputs were not changed by this work.

## Evidence from this review

- Isolated public-checkout Python suite after compatibility fixes: 234 passed,
  13 skipped with NASA's parser supplied. Skips cover explicitly opt-in native/
  package checks and the unavailable Fortran compiler probe; native and package
  selections were exercised separately below.
- Rebuilt managed Windows CTest executables: 14 passed, including default and
  explicit caller tolerance checks. This uses the provisioned development machine.
- New fixture preparation/precedence tests and viewer formatter test: seven passed.
- Native bounded selection with the rebuilt executable and new staged fixture:
  eight passed after explicitly seeding the smoke fixture, as described above.
- NASA export/parser selection with the pinned upstream parser: six passed.
- Compact SPK regeneration: two byte-identical outputs; 13,468 state-vector
  comparisons are bit-identical to the recorded full source kernel inventory.
- All four AEPS baseline/nearby-hardware cases passed with the compact fixture,
  unchanged acceptance criteria and the full 1200-second per-case solver budget.
- Six compiled CMake fixtures passed for optional Windows naming/runtime staging.
- Rebuilt Windows release ZIP: both relocation tests passed; executable and
  archive path audits passed. Dependency audit reports only operating-system DLLs.

The AEPS input-reduction check used the previously qualified executable. The
rebuilt executable additionally passed CTest, bounded native and package checks;
the full AEPS matrix has not been repeated on that binary. Neither run qualifies
a new laptop build or the separate Linux IPOPT 3.14.19 graph. Follow
[qualification.md](qualification.md) for those gates.
