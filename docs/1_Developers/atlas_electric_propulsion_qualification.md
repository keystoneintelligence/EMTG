# Electric-Propulsion Atlas Qualification

## Status

The electric-propulsion Atlas baseline and nearby-hardware matrix is qualified
for the tested AEPS/IPOPT FBLT fixture as of 2026-08-02. This is a bounded
qualification claim for the Atlas mutation, materialization, solver execution,
and output-validation path. It is not a blanket qualification of every EMTG
electric-propulsion transcription or hardware model.

## Qualified revision and executable

- EMTG revision: `0ef9d8a04625b51133cd6d216a9e1d2c43eb82ba`
- Fixture: `testatron/tests/integration_asteroid_missions/A20136163_AEPS_IPOPT_FBLT.emtgopt`
- Solver: `bin/EMTGv9.exe`
- Solver SHA256: `4a7b39a1825a7a238c398c3cb288db707cbec26cf764e0b7a780166e0f80360b`
- Python: 3.10.11
- Pytest: 8.4.2
- Platform: Windows

The preflight first verified parameter discovery, mutation planning, immutable
hardware-variant materialization, and architecture identity. The real-solver
matrix then ran the unmodified anchor twice and a nearby multi-axis hardware
perturbation twice. Every run had the full 1,200-second NLP budget and was
validated against the asteroid integration output contract.

## Results

| Case | Repetitions | Final mass (kg) | Worst constraint | Result |
| --- | ---: | ---: | ---: | --- |
| Baseline | 2 | 1685.852656 | -7.37979e-09 | Passed identically |
| Nearby hardware | 2 | 1686.719710 | -3.68918e-09 | Passed identically |

All four cases reached `LT_rndzvs` at `A20136163`, emitted mission and
ephemeris artifacts, and satisfied the `1.0e-5` feasibility envelope. The
complete matrix passed in 4,841.80 seconds.

## Reproduction

```powershell
$env:EMTG_RUN_OUTERLOOP_INTEGRATION = "1"
python -m pytest tests/test_outerloop_emtg_integration.py::test_aeps_real_atlas_baseline_and_nearby_hardware_matrix -vv -s -p no:cacheprovider
```

For GitHub Actions, manually dispatch `IPOPT Open-Source Solver` with
`run_aeps_atlas_qualification` enabled. The workflow builds an IPOPT-only EMTG
executable from source, runs the same four-case gate, and uploads the temporary
case directories and solver logs as a 30-day evidence artifact.
