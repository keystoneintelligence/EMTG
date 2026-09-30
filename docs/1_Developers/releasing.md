# Community Edition release checklist

Release the reviewed Community Edition source from `master`. Record the exact
source commit and artifact hashes; earlier qualification does not identify a
newly built binary. The public Windows/Linux graph has passed the checks recorded
in [qualification.md](qualification.md). Each release tag requires its own
matching-source artifacts.

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
- Merge the reviewed presentation/source changes into `master` before selecting
  the final release. A merged pull request does not create a tag or release.
- Use the existing release process: explicitly create an annotated `v<VERSION>`
  tag at the chosen source and push it. `Build Release Packages` builds and
  qualifies the Windows/Linux bundles and publishes them through its existing
  tag-triggered GitHub release job. No release-specific build logic is needed.
- Use the release announcement for the version being published; the 9.2.0 text
  is in [its release notes](../releases/9.2.0.md). Check the published release's
  source tag, assets, checksums, receipts, notices, and support statement, and
  retain the matching workflow result. Report qualification limits accurately.
