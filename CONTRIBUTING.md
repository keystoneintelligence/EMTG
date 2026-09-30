# Contributing to EMTG Community Edition

Bring a mission example, a build improvement, a reproducible regression, or a
focused code fix. Proposals for new analysis tools are welcome; keep the core
scientific engine independent of the applications built around it.

Base pull requests on `master`, the Community Edition branch. Use this fork's [issues](https://github.com/keystoneintelligence/EMTG/issues) to
report bugs or discuss substantial changes. Small focused pull requests are
welcome. Describe the problem, resulting behavior, and validation performed.
Preserve NASA attribution, license notices, and third-party notices.

For development dependencies and builds, start with [BUILDING.md](BUILDING.md).
The usual fast checks from the source root are:

```text
python -m pip install -r requirements-dev.txt
python -m pytest
cmake --preset ci-fast
cmake --build --preset ci-fast
ctest --preset ci-fast
```

Choose additional tests that exercise the changed boundary. Scientific changes
need relevant numerical and native-solver evidence, including explicit solver
selection, options, and kernel provenance. Preserve established physical
acceptance criteria; explain pre-existing failures and unavailable qualifications.
A skipped gate is not a passing gate.

Keep public scientific interfaces independent of application services and their
schemas or credentials. Put application-specific translation and publication
policy in the caller. Do not commit generated runs, credentials, downloaded
dependencies, or large scientific assets; document external inputs by checksum.

For platform or packaging changes, test the actual dependency graph and extracted
bundle. Follow [SUPPORT.md](SUPPORT.md) when describing compatibility and the
[release checklist](docs/1_Developers/releasing.md) before publishing artifacts.
