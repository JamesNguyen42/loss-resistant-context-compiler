# SWE-bench license and acquisition review

This is an engineering evidence inventory, not legal advice. It records what
the pinned artifacts themselves declare, keeps missing grants explicit, and
separates local evaluation from redistribution. A repository license does not
by itself establish licensing for complete SWE-bench rows, and the SWE-bench
harness license does not by itself establish licensing for third-party
repository snapshots.

## Review status

| Scope | Exact identity | Observed declaration | Current decision |
| --- | --- | --- | --- |
| Verified dataset | `SWE-bench/SWE-bench_Verified@91aa3ed51b709be6457e12d00300a6a596d4c6a3` | The pinned 3,336-byte dataset card, SHA-256 `6edb58b1b2c44ce858426c4ebc90077827713b75cebc243661e4433c81fd57f5`, has no `license` field or SPDX identifier | Redistribution review remains blocked; raw and derived rows stay ignored and local |
| Evaluation harness | `SWE-bench/SWE-bench@726c5461e2ef52d83cf1ea2107870a8bb3328d57` (`v4.1.0`) | Root `LICENSE` is MIT, SHA-256 `2bd2e08df7147f67a69b42c10efae09bd4bf119df397371036187d5dd1b02f57` | Applies to the harness source only; security review and grader hardening remain incomplete |
| Flask task repository | `pallets/flask@7ee9ceb71e868944a46e1ff00b506772a53a4f1d` | Root `pyproject.toml` declares `BSD-3-Clause`; root `LICENSE.rst` is 1,475 bytes, SHA-256 `489a8e1108509ed98a37bb983e11e0f7e1d31f0bd8f99a79c8448e7ff37d07ea`. `docs/license.rst` applies those terms to source, examples, tests, and docs, but applies the separate 780-byte `artwork/LICENSE.rst`, SHA-256 `2e63a3bd3a1f97e2fa9fa33bbf4f61bbb56ee16beb125b5346b0012388dc3b49`, to the Flask logo | Discovered license-file declarations recorded; broader per-path, third-party, and notice review remains incomplete, and this does not approve redistribution or the remaining repositories |
| Seaborn task repository | `mwaskom/seaborn`, selected ordinals 287--288 | Both prepared commits contain the same 1,491-byte root `LICENSE.md` plus five distinct files under `licences/` | Discovered files inventoried; applicability and redistribution review remain incomplete |
| Requests task repository | `psf/requests`, selected ordinals 290--297 | All eight prepared commits contain root `LICENSE`, `NOTICE`, and `docs/_themes/LICENSE`; their bytes vary by commit, and ordinals 296--297 also contain `ext/LICENSE` | Variants are retained separately rather than flattened into one project-wide conclusion |
| Pylint task repository | `pylint-dev/pylint`, selected ordinals 320--329 | All ten exact commits contain the same 17,984-byte root `LICENSE`; ordinals 324--329 also contain two symlink entries rejected by preparation policy | Four trees prepared and six policy refusals retained; this is not a legal determination or task result |
| Pytest task repository | `pytest-dev/pytest`, selected ordinals 330--348 | Nineteen prepared commits contain three root-`LICENSE` byte variants, four `doc/en/license.rst` variants, and a theme license in ordinals 333--341 | Discovered variants inventoried; per-path and redistribution review remain incomplete |
| Remaining 7 task repositories | Astropy, Django, Matplotlib, Xarray, scikit-learn, Sphinx, and Sympy exact base commits selected by the pinned source | Not yet inspected across all selected commits | 460 rows remain unattempted; local preparation and redistribution review remain incomplete |

The official pinned [dataset tree](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/tree/91aa3ed51b709be6457e12d00300a6a596d4c6a3)
contains the dataset card and data but no standalone license file. The
[exact pinned dataset card](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/blob/91aa3ed51b709be6457e12d00300a6a596d4c6a3/README.md)
shows dataset metadata without a license field. The separate pinned
[harness license](https://raw.githubusercontent.com/SWE-bench/SWE-bench/726c5461e2ef52d83cf1ea2107870a8bb3328d57/LICENSE)
is therefore not used to fill that gap.

## Public preparation evidence

On 2026-08-02, clean code revision `3c79856` acquired
`https://github.com/pallets/flask.git` as a local bare mirror over Git HTTPS.
The retained mirror records that configured URL but does not authenticate
GitHub, the server, or acquisition time. Git was
`2.55.0.windows.3`; the 46,920-byte Git executable had SHA-256
`7b7971dd13f0c3a284e538601f2f9770b3a87dfaccb5fb52d68141c67ed22364`.

The source-bound preparer then replayed selected ordinal 289,
`pallets__flask-5014`, at exact base commit
`7ee9ceb71e868944a46e1ff00b506772a53a4f1d`. The result was:

- mirror self-hash
  `6c27b7e76301f535974ccb144efe7e56612f0d7325364e628d47a0e2ba70ac63`;
- preparation self-hash
  `992a2418b677c220cf351f6e00f33ca667907e113a0dcc912772b846533e75a0`;
- 251 portable regular files totaling 1,578,533 blob bytes;
- ordered entry-binding SHA-256
  `821ad570fa22ab2c759ccc09a52556333dac3c933dcbefc8c45234935df3d1ae`;
- ignored local manifest size 69,106 bytes and file SHA-256
  `b0aa8335fd90447e417df3ac5888bd29df6c3954d77dbf47febe56d476d04635`.

The exact base commit's
[source license](https://raw.githubusercontent.com/pallets/flask/7ee9ceb71e868944a46e1ff00b506772a53a4f1d/LICENSE.rst),
[license scope](https://raw.githubusercontent.com/pallets/flask/7ee9ceb71e868944a46e1ff00b506772a53a4f1d/docs/license.rst),
and separate
[artwork license](https://raw.githubusercontent.com/pallets/flask/7ee9ceb71e868944a46e1ff00b506772a53a4f1d/artwork/LICENSE.rst)
were inspected from mirror bytes. The two example license copies exactly match
the root source-license bytes. This review does not collapse the artwork terms
into BSD-3-Clause.

The prepared tree and full manifest remain under ignored `build/` storage and
are not release artifacts. The preparer reverified the live mirror and output
tree before returning, but its origin-authentication, redistribution-review,
candidate-mount, filesystem/network-isolation, execution, grading, score,
usefulness, and claim-readiness fields all remain false. This is one real
public base-commit preparation, not a candidate run or a SWE-bench result.

Subsequent local work retained every outcome for selected ordinals 287--288,
290--297, and 320--348. Together with the earlier Flask ordinal 289, the
current full-cohort state is:

| Repository | Selected rows attempted | Prepared | Policy-refused | Mirror self-hash |
| --- | ---: | ---: | ---: | --- |
| `mwaskom/seaborn` | 2 | 2 | 0 | `a93f6cc0806e2808992a344e7d84df4e1b4b8c7400808f00965b5daa355c735b` |
| `pallets/flask` | 1 | 1 | 0 | `6c27b7e76301f535974ccb144efe7e56612f0d7325364e628d47a0e2ba70ac63` |
| `psf/requests` | 8 | 8 | 0 | `224d636f04eaa8bafcd3a2dbb9bd16d8e38d354626ebc8e6a6b08608daac7a67` |
| `pylint-dev/pylint` | 10 | 4 | 6 | `49214f704d579df8ea995e6e234fe4f6c72d3d5fb8088148dc95355b62cb7773` |
| `pytest-dev/pytest` | 19 | 19 | 0 | `cb339b9072d403a97f1192ee53e682ce9b013e8f8c87a20744a3b8b11e2a7fd9` |
| **Cohort total** | **40** | **34** | **6** | n/a |

All 34 successful handles passed a fresh source-bound mirror and output-tree
replay. Their ignored manifests total 4,460,409 bytes and bind 17,307 portable
regular files, 114,669,862 blob bytes, 2,063 tree objects, and 867,348 raw tree
bytes. The other 460 selected source rows were not attempted.

An ignored preparation-only prediction ledger reconciles those outcomes over
all 500 source rows. Its 487,299-byte JSON file has SHA-256
`8397d853d7d15bbacb20e56d784d8a270d7b506e39231015b934207670ae63cb`
and self-hash
`cfedda6985ca5c088bf123db678793fc9031461c92ebc755c8a0af80a14e24ff`.
The companion 53,734-byte official-format JSONL has 500 null patches and
SHA-256
`546a3e42b9cf5bfcf7165eb244b5da909ff069971519e0ac7f083b413f950e60`.
The system label is `not-run-preparation-only`; this artifact records no model
or controller execution.

Pylint ordinals 324--329 each refused with exact code
`tree-symlink-forbidden`. Every affected commit contains the same two mode
`120000` entries:

- `tests/functional/s/symlink/_binding/__init__.py`, target
  `../symlink_module/__init__.py`, 29 bytes, SHA-256
  `d512986cba02f0e7227cbee89d821f490536569d4ffacf37aee4de897e5627d9`;
- `tests/functional/s/symlink/_binding/symlink_module.py`, target
  `../symlink_module/symlink_module.py`, 35 bytes, SHA-256
  `3b85c9cde74f087c09a6dc3c31245ebaf1f0e6140e3ae4a28fb481eff4863b6a`.

Those refusals express the link-free export policy. They are not candidate
failures, test failures, or evidence that the upstream repositories are unsafe.

## Discovered license-file variants

This inventory records distinct bytes rather than inferring that a root license
governs every file:

- both Seaborn commits have root `LICENSE.md`, 1,491 bytes, SHA-256
  `0874bfc01308833385b2f5ca62205525739f6a57b8688afea1b078d0f845d6d4`,
  plus `APPDIRS_LICENSE` (`82663369410cf3c5099fce03b172af030baa169aef45a5e660d83817772af564`),
  `HUSL_LICENSE` (`cfd1d7a3a2824aeb1ba88337697be0f4c0d37b5f5ae1c1eff360a9e553053a2b`),
  `NUMPYDOC_LICENSE` (`426a5a484480f57a295db48c2c04f47bb3274752dd82a7fd6541dfa2cb90f641`),
  `PACKAGING_LICENSE` (`b70e7e9b742f1cc6f948b34c16aa39ffece94196364bc88ff0d2180f0028fac5`),
  and `SCIPY_LICENSE` (`0615c3b553439d39155885fedd7078a923974e84330f8d5d7660074e7c8826a3`);
- Requests has three 581-byte root-license variants at ordinals 290--295,
  one 10,142-byte variant at 296--297, five distinct `NOTICE` byte sets, one
  common 1,861-byte docs-theme license, and a 51-byte `ext/LICENSE` only at
  296--297. The prepared manifests preserve the commit-specific path/hash
  binding; no single variant is substituted for another;
- all ten inspected Pylint commits have the same 17,984-byte root `LICENSE`,
  SHA-256
  `f97b14080de8b8490d60eb3d620ebc419943e0779466d1dd0d5d6f68fe195dcd`;
- Pytest root `LICENSE` is 1,091 bytes at ordinals 330--332, SHA-256
  `ca836a5f9ecca3b2f350230faa20a48fb8b145653b5568d784862df864706b9b`,
  then 1,096 bytes with SHA-256
  `4be38574daf05665b6194e4fd84c5ef6ce7c3a2810308a5222d5cdffc2d04d18`
  at 333--339 and
  `7898b9b164d4f93fd9a562fd0f592935518b48c4745303964110eb7f7fe64faf`
  at 340--348. Four distinct `doc/en/license.rst` byte sets and the separate
  theme license at 333--341 remain separately inventoried.

The breadth-first filename scan is useful discovery evidence, not proof that
every applicable license, notice, vendored component, generated file, or
subtree exception has been identified.

## Completion gate

The repository-license review is complete only after every distinct selected
base commit has been checked for all applicable licenses, notices, and
file/subtree exceptions, every selected task has a retained preparation
success or refusal, and an operator has approved the intended
artifact-retention and redistribution policy. Dataset redistribution remains
separately blocked until a documented legal determination or authorization
covers the relevant artifacts and rights holders. No source
snapshot, task document, prepared tree, or candidate/grader artifact may be
published merely because the harness or one task repository is permissively
licensed.
