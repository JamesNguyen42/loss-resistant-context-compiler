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
| Xarray task repository | `pydata/xarray`, selected ordinals 298--319 | All 22 exact commits contain the same 10,273-byte Apache License 2.0 root `LICENSE`; packaging metadata declares `Apache` through ordinal 315 and `Apache-2.0` at 316--319 | All 22 trees prepared; broader per-path, notice, third-party, and redistribution review remains incomplete |
| Pylint task repository | `pylint-dev/pylint`, selected ordinals 320--329 | All ten exact commits contain the same 17,984-byte root `LICENSE`; ordinals 324--329 also contain two symlink entries rejected by preparation policy | Four trees prepared and six policy refusals retained; this is not a legal determination or task result |
| Pytest task repository | `pytest-dev/pytest`, selected ordinals 330--348 | Nineteen prepared commits contain three root-`LICENSE` byte variants, four `doc/en/license.rst` variants, and a theme license in ordinals 333--341 | Discovered variants inventoried; per-path and redistribution review remain incomplete |
| scikit-learn task repository | `scikit-learn/scikit-learn`, selected ordinals 349--380 | All 32 exact commits contain one of four root `COPYING` byte variants and the same vendored `sklearn/svm/src/liblinear/COPYRIGHT`; 28 distinct root packaging-declaration byte variants were also retained | All 32 trees prepared; the filename-based inventory is discovery evidence only, and applicability and redistribution review remain incomplete |
| Sphinx task repository | `sphinx-doc/sphinx`, selected ordinals 381--424 | All 44 exact commits contain a root `LICENSE`; the engineering inventory retains five root-license byte variants plus root packaging-declaration variants | All 44 trees prepared; the filename/path-based inventory is discovery evidence only, and applicability and redistribution review remain incomplete |
| SymPy task repository | `sympy/sympy`, selected ordinals 425--499 | All 75 exact commits contain root `LICENSE`, `data/TeXmacs/LICENSE`, and mode-`100755` root `setup.py`; 37 contain `setup.cfg` and 58 contain `sympy/parsing/latex/LICENSE.txt`; the engineering inventory retains 320 occurrences across 59 exact variants | All 75 trees prepared; the filename/path-based inventory is discovery evidence only, and applicability and redistribution review remain incomplete |
| Remaining 3 task repositories | Astropy, Django, and Matplotlib exact base commits selected by the pinned source | Not yet inspected across all selected commits | 287 rows remain unattempted; local preparation and redistribution review remain incomplete |

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

Subsequent local work retained every outcome for the contiguous selected range
287--348. That historical through-348 checkpoint verified the configured
`https://github.com/pydata/xarray.git` mirror and prepared ordinals 298--319.
Those 22 ignored manifests total 1,558,923 bytes and bind 6,160 regular files,
138,899,190 blob bytes, 681 tree objects, and 285,569 raw tree bytes. No Xarray
row was refused. The mirror self-hashes recorded at that checkpoint were:

| Repository | Selected rows attempted | Prepared | Policy-refused | Mirror self-hash |
| --- | ---: | ---: | ---: | --- |
| `mwaskom/seaborn` | 2 | 2 | 0 | `a93f6cc0806e2808992a344e7d84df4e1b4b8c7400808f00965b5daa355c735b` |
| `pallets/flask` | 1 | 1 | 0 | `6c27b7e76301f535974ccb144efe7e56612f0d7325364e628d47a0e2ba70ac63` |
| `psf/requests` | 8 | 8 | 0 | `224d636f04eaa8bafcd3a2dbb9bd16d8e38d354626ebc8e6a6b08608daac7a67` |
| `pydata/xarray` | 22 | 22 | 0 | `35b8f2ac2aba3d3860fc1da39c6211f4d1c953f7d7a22d7493551eadab413cf9` |
| `pylint-dev/pylint` | 10 | 4 | 6 | `49214f704d579df8ea995e6e234fe4f6c72d3d5fb8088148dc95355b62cb7773` |
| `pytest-dev/pytest` | 19 | 19 | 0 | `cb339b9072d403a97f1192ee53e682ce9b013e8f8c87a20744a3b8b11e2a7fd9` |
| **Cohort total** | **62** | **56** | **6** | n/a |

All 56 successful handles at that checkpoint passed a fresh source-bound mirror
and output-tree replay. Their ignored manifests total 6,019,332 bytes and bind
23,467 portable regular files, 253,569,052 blob bytes, 2,744 tree objects, and
1,152,917 raw tree bytes. The other 438 selected source rows were not attempted.

The predecessor verified checkpoint extended that contiguous range through
ordinal 380. A fresh `--verify-only` replay at clean code revision
`d512b04156728cb9a4088f250ead395192572b34` revalidated all seven live mirrors,
all 88 manifests, and every prepared output tree against that checkpoint's
worker and Git executable. Its mirror self-hashes are:

| Repository | Selected rows attempted | Prepared | Policy-refused | Mirror self-hash |
| --- | ---: | ---: | ---: | --- |
| `mwaskom/seaborn` | 2 | 2 | 0 | `24a3012396869f467762c60b0899cda49b10bd86120473f763c5fb368a18a196` |
| `pallets/flask` | 1 | 1 | 0 | `e57b2ca55771492c111acbf492de7815b5a7c986cae79b2981a5faff2aa72857` |
| `psf/requests` | 8 | 8 | 0 | `986075417331430465c071886459e0036bb49483610507678e86b7a8dff4624d` |
| `pydata/xarray` | 22 | 22 | 0 | `3e35b0ae7c29272c1c3fed67ffe9235748f306f4e79b69988d0954c97fe29230` |
| `pylint-dev/pylint` | 10 | 4 | 6 | `9870e1c5f0b4d9298335fc491fa0bee7e49608e5a556c8f822419d079dd9eede` |
| `pytest-dev/pytest` | 19 | 19 | 0 | `2650e52441093e8fe99a68291321d0ec069de5e68f298d13083e1b33500a25e0` |
| `scikit-learn/scikit-learn` | 32 | 32 | 0 | `11fd8e824611cfb7ad2e3209ea7d7c14af3b6bbcf0f03c5a6d6de7d0e1150888` |
| **Cohort total** | **94** | **88** | **6** | n/a |

The 88 ignored manifests total 16,333,450 bytes and bind 64,056 portable
regular files, 801,780,869 blob bytes, 7,603 tree objects, and 3,208,763 raw
tree bytes. The 72,728-byte summary has file SHA-256
`21ded8d8bff3038e1064abaff01d952e06fe60d165617f3e3e213d55ba99b781`
and self-hash
`f11f8bd4235c1469fbe8dd8865d92519af82f8c831ae5901a82b182bd221b96d`.
The predecessor checkpoint's 42,849-byte worker has SHA-256
`cb9c8680d74022b7db8aea8b376790a49131f5fd1a1c0b93ba74341302c8a44f`;
the 46,920-byte Git executable remains SHA-256
`7b7971dd13f0c3a284e538601f2f9770b3a87dfaccb5fb52d68141c67ed22364`.
These release- and environment-bound values do not replace the historical
checkpoint above and do not authenticate repository origin or authorship.
Repository-origin authentication, repository and dataset redistribution review,
legal determination, candidate mount, filesystem/network isolation, execution,
grading, external score, usefulness, claim readiness, and self-hash author or
producer authentication all remain false. The scikit-learn inventory's only
positive characterization is `engineering_inventory_only`.

The through-348 checkpoint also retained an ignored preparation-only prediction
ledger over all 500 source rows. Its 488,750-byte JSON file has SHA-256
`2219e2f1526c51f4c965a7af41364c393075151fecc2e2b4553eb037e359b772`
and self-hash
`b5668f76a4949d42310ce644de007696a6d9ee3725d3d0514edabf6887082f34`.
Its companion 53,734-byte official-format JSONL has 500
`model_patch: null` rows and
SHA-256
`546a3e42b9cf5bfcf7165eb244b5da909ff069971519e0ac7f083b413f950e60`.
The predecessor through-380 ledger is 490,862 bytes with file SHA-256
`dc8d7c10b7ab4ad4cf24123296abf502f614a1740337e356ce281e7bffcdf8af`
and self-hash
`3fa05e48739ca1fdc1e8295724b68f45f42728f9229cf908dfeaa6fd29192e5d`.
It historically reconciles 88 prepared, 6 refused, and 406 unattempted rows.
Its JSONL is
byte-for-byte unchanged: the same 53,734 bytes, exact 500-row source order, 500
`model_patch: null` rows, and SHA-256 above. The system label remains
`not-run-preparation-only`; neither checkpoint records model or controller
execution.

The historical mixed-provenance through-424 checkpoint preserves that
predecessor segment at `d512b04156728cb9a4088f250ead395192572b34` and adds Sphinx
ordinals 381--424 at
`f12aa37d91bc4b2c3fa37e0ab882df6b035a902d`. All 44 Sphinx rows prepared
with no refusal, producing 44 manifests / 18,731,287 manifest bytes / 66,143
files / 736,223,680 blob bytes / 18,382 tree objects / 3,334,382 raw tree
bytes. The combined state at that point was 138 attempted, 132 prepared, the
same six Pylint refusals, and 362 unattempted. Its 132 manifests total
35,064,737 bytes and bind
130,199 files, 1,538,004,549 blob bytes, 25,985 tree objects, and 6,543,145 raw
tree bytes.

The historical through-424 reconciliation artifacts are:

- 45,469-byte reconciliation, raw SHA-256
  `c6882b68d817e374ebd6be00fa32a7ee2a2ac2d265c2f8f1fc8c858138fea23e`,
  self-hash
  `7bf6599e2a258b588a92b9bab41c899ba799b0fc81ff1bd41f355b86aa92a789`;
- 493,768-byte ledger, raw SHA-256
  `3be02f725ec0e0041bde5af9ba02288c9b15823f3c0fcfd1ad84445d78276bb8`,
  self-hash
  `5543f9e28a11a12bdf9a0bf9f3473926b80b0357910eed1e2fb2fcdc9cd002cc`;
- predecessor-identical 53,734-byte official JSONL with 500
  `model_patch: null` rows,
  SHA-256
  `546a3e42b9cf5bfcf7165eb244b5da909ff069971519e0ac7f083b413f950e60`;
- 31,722-byte Sphinx summary, raw SHA-256
  `fc11dfd91d75ee903359a6f9158956342422f1a4b8f6ef58cb18247af484949b`,
  self-hash
  `f62a65f0f54a23a07d4385f168a4696c7437d90a55e9e8ca80b9d24a282b5bb3`;
- 24,447-byte, 46-event Sphinx journal, SHA-256
  `6655d51f482a0039c3e1a070287456c6ef8f3936fb77b02b10590fa648f38b93`;
- 1,646-byte Sphinx mirror evidence, raw SHA-256
  `159f53c82a222e9cad63d114737d6baf11136f0ee162ca13d56387dc842ae12f`,
  self-hash
  `c0a2425237dbff9c8d471a82bbb3730bca316b5019c0db05c23358a3bdd67a6d`;
- 96,201-byte Sphinx engineering inventory, raw SHA-256
  `e02f07b4feb49ab7ebf378ef7614b8acd01a7367921acfe449ec0e2a5ef08840`,
  self-hash
  `5df1f68df2405b8365074cce05b7ff2391b6ac945f81dd47951956a918ddae25`.

A historical clean CPython 3.14.6 `--verify-only` replay completed in
2,236.794 seconds and reproduced the exact through-424 counts, file hashes, and
self-hashes. This adds replay evidence only; it does not strengthen the
inventory's legal, applicability, redistribution, or origin claims.

No public candidate, model, agent, hidden test, grader, GPU, or inference ran at
that checkpoint. Candidate mount, filesystem/network isolation, execution,
grading, resolution, score, usefulness, and claim readiness remained absent or
false.

The current through-499 checkpoint adds SymPy ordinals 425--499 at clean
generation revision `c3375577b1e555a56fa6c64ed754bc9eabd64b8f`. All 75
unique tasks and commits prepared with no refusal, from
`sympy__sympy-11618@360290c4c401e386db60723ddb0109ed499c9f6e` through
`sympy__sympy-24661@a36caf5c74fe654cedc488e8a8a05fad388f8406`, producing
75 manifests / 31,999,044 manifest bytes / 128,638 files / 2,253,028,012 blob
bytes / 15,474 tree objects / 5,906,718 raw tree bytes. The current combined
state is 213 attempted, 207 prepared, the same six Pylint refusals, and 287
unattempted. Its
207 manifests total 67,063,781 bytes and bind 258,837 files, 3,791,032,561 blob
bytes, 41,459 tree objects, and 12,449,863 raw tree bytes.

The SymPy and fresh through-499 artifacts are:

- 50,679-byte SymPy summary, raw SHA-256
  `17ccdf6c9941e83493dab39c13ba34a34c500a600bb0d4b8c5836fcad485d505`,
  self-hash
  `8c1d52330a69882e7ffe86851a7e684d33060fb2b476e52681ab50688b678ca0`;
- 40,345-byte, 77-event SymPy journal, SHA-256
  `37fe9b7fc0383e74b8c5cb7194c0d2e6becfab5d656e62ee1aca4d760b799254`;
- 1,634-byte SymPy mirror evidence, raw SHA-256
  `ca82d8be6381caf83743042d3452f49c15338f36562479212231080e740126d2`,
  non-authenticating self-hash
  `0220123b7d117760950386d21ab58b4a6aa5de5dcc19b4abe62f274674d66859`;
- 217,198-byte SymPy engineering inventory, raw SHA-256
  `be81e87227e3d6f9438b71a83ec48460d92545f829e252ae9606271e00b02db0`,
  self-hash
  `ac9cb5190e77294b655b86b63a398a8bd0407475322c0ca4e58a72805ca52066`;
- 67,322-byte through-499 reconciliation, raw SHA-256
  `46452d7005493080b0f3bc9e09ff38ce7cf3d534a59a29b8e6499dcac48afcbe`,
  self-hash
  `d26d2a29b94363f3442ba576998725394d00d51335c9750ee466cc99032aea4e`;
- 498,718-byte through-499 ledger, raw SHA-256
  `e57df1b4c11ae8c274e636d488151b2e2fd865026d877f7e7a345c9bc6b97b3d`,
  self-hash
  `1f962ed25a7644b03bd36bb0b0b4ea79f29da8129ac0441a0084e7fb83d5b4dc`;
- predecessor-identical 53,734-byte official JSONL with exactly 500
  LF-terminated `model_patch: null` rows, SHA-256
  `546a3e42b9cf5bfcf7165eb244b5da909ff069971519e0ac7f083b413f950e60`.

Publication resumed and completed in 7,595.734 seconds. A separate
`--verify-only` replay completed in 4,920.234 seconds and reproduced the exact
through-499 counts, file hashes, and self-hashes. This adds replay evidence
only; it does not strengthen the inventory's legal, applicability,
redistribution, or origin claims.

No public candidate, model, agent, hidden test, grader, GPU, or inference ran at
the current checkpoint. Candidate mount, filesystem/network isolation,
execution, grading, resolution, score, usefulness, legal determination,
redistribution approval, origin authentication, and claim readiness remain
absent or false.

After completing the late physical block 287--499, the next and smallest
untouched physical repository block is Astropy ordinals 0--21: 22 unique tasks
and commits from
`astropy__astropy-12907@d16bfe05a744909de4b27f5875fe0d4ed41ce607`
through
`astropy__astropy-8872@b750a0e6ee76fb6b8a099a4d16ec51977be46bf6`.
This does not mean every physical ordinal through 499 has been prepared.

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
- all 22 Xarray commits have the same 10,273-byte
  [root `LICENSE`](https://raw.githubusercontent.com/pydata/xarray/7c4e2ac83f7b4306296ff9b7b51aaf016e5ad614/LICENSE), SHA-256
  `73ba74dfaa520b49a401b5d21459a8523a146f3b7518a833eea5efa85130bf68`.
  The file declares Apache License 2.0. Root packaging metadata uses the label
  `Apache` through ordinal 315 and `Apache-2.0` at ordinals 316--319; both are
  inventory observations, not a whole-tree legal conclusion;
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
  theme license at 333--341 remain separately inventoried;
- the 32 scikit-learn commits contain four root `COPYING` byte variants:
  SHA-256
  `3f046f2b5eea2c3b852dae4feea90f92a64ced2a6ac23ea137db2a5ac347ce22`
  at ordinals 349--354,
  `0a5f5c0e4d0b33493b8e62cbbeb63b84067a3bc3865723a4628768d789ed9fce`
  at 355--372 and 380,
  `e39bf18719108f9e94b578020077dae2c8f0cede14cd7caa11e9a13c44b5e5ab`
  at 373--376, and
  `d81e7339cfef557debf6d8214e4a31acd01b3144d2b39e42513c3c7e5c29e5c2`
  at 377--379. All 32 also contain the same 1,486-byte
  `sklearn/svm/src/liblinear/COPYRIGHT`, SHA-256
  `36f048db5651dd450f03e513016b77036cc99a44a66b2fdcecf4f6418657e0e1`.
  The inventory separately retains 10 `setup.cfg`, 13 `setup.py`, and 5
  `pyproject.toml` byte variants; `pyproject.toml` appears in seven commits.
  Altogether those are 33 distinct candidate-byte variants across five paths.
  The 91,472-byte inventory has file SHA-256
  `4130d4f1f7dc65ee0d0ef5babe2fa1fe278f834c84994ebf11001774a5623887`
  and self-hash
  `5adcfab541bbd7769812d529e725cd745dbc6ea57c73b5a55538b814984b71fd`.
- the 44 Sphinx commits retain 129 candidate occurrences partitioned into 32
  exact variants. Five variants are root `LICENSE`; the root packaging
  declarations contribute three `pyproject.toml`, eight `setup.cfg`, and
  sixteen `setup.py` variants. Every retained candidate has Git mode `100644`.
  The 96,201-byte inventory and its raw/self hashes are recorded above. This is
  engineering discovery only: it does not establish legal applicability,
  redistribution permission, or repository-origin authentication.
- the 75 SymPy commits retain 320 candidate occurrences partitioned into 59
  exact variants. Path occurrence/variant counts are root `LICENSE` 75/12,
  `data/TeXmacs/LICENSE` 75/1, `setup.cfg` 37/11, `setup.py` 75/34, and
  `sympy/parsing/latex/LICENSE.txt` 58/1. All 75 `setup.py` occurrences have Git
  mode `100755`; the other 245 occurrences are `100644`. The inventory and its
  raw/self hashes are recorded above. This is engineering discovery only: it
  does not establish legal applicability, redistribution permission, or
  repository-origin authentication.

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
