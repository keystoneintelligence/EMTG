# Community release checklist

The current public candidate still requires qualification on fresh machines.
History preparation and documentation updates do not complete this checklist.
Record the exact candidate commit for every result; rerun affected checks when
the candidate changes.

## Public source and history

- Use the reviewed public branch and its complete, verified history. Preserve
  NASA authorship, license notices, and the upstream base.
- Review the tracked tree and commits being published for private applications,
  credentials, local paths, generated runs, and internal project inventories.
  Export only the intended public ref; do not mirror a development repository.
- Keep an internal rollback reference and record the public source tree and
  archive hashes separately. Avoid putting private provenance into public files.

## Fresh-machine qualification

1. Check out the exact public candidate into a short path on a fresh Windows x64
   machine and on Ubuntu 22.04 x64. Install only the documented prerequisites.
2. Run `build.ps1` on Windows and `build.sh --bootstrap` on Ubuntu. Retain the
   complete logs, CTest results, dependency graph, SPDX report, and artifact
   checksums. Confirm that tracked sources remain unchanged.
3. Exercise the extracted bundle from another directory using
   `tests/test_packaged_runtime.py` with `EMTG_RELEASE_ROOT` set. Run the release
   path audit on the actual artifacts with the source and dependency-cache roots
   explicitly forbidden. Inspect `--version`, `--capabilities`, and `--doctor`.
4. Run the public fast suites and bounded native IPOPT cases with the declared
   scientific assets. Run the four-case AEPS matrix with its existing budget,
   solver settings, and physical acceptance assertions. Requested qualifications
   with missing assets remain failures or outstanding gates, never passes.
5. Exercise the existing Linux IPOPT workflow, including its AEPS opt-in, against
   this candidate. Its IPOPT 3.14.19 graph and the managed IPOPT 3.14.11 graph
   require separate evidence.
6. Record licensed NASA/SNOPT and optional GUI/extension qualifications separately.
   If unavailable, retain the explicit limits in the support matrix. Consumer
   applications maintain their own end-to-end checks outside public EMTG CI.

The workflow names and test commands are documented in
[qualification.md](qualification.md). A workflow definition or an older passing
revision is not evidence that the candidate passed.

## Publication review

- Update [SUPPORT.md](../../SUPPORT.md) with the configurations actually qualified.
  Keep Linux experimental unless wider support has separate evidence. Do not
  promise macOS, historical GUI, or complete NASA equivalence without validation.
- Confirm that the README, packaged notices, version, release notes, prerequisites,
  and download instructions agree with the produced artifacts. Preserve the
  source and notices required for the included dependency graph.
- Attach only the reviewed candidate's artifacts and checksums. Describe known
  limits and intentional option-default changes in the release notes.
- Create the release tag and publish only after these gates are resolved. The
  existing release workflow can publish when a matching `v*` tag is pushed;
  manually building packages does not authorize a release.
