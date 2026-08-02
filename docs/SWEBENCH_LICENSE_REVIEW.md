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
| Remaining 11 task repositories | Exact base commits selected by the pinned 500-row source | Not yet inspected across all selected commits | Local preparation and redistribution review remain incomplete |

The official pinned [dataset tree](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/tree/91aa3ed51b709be6457e12d00300a6a596d4c6a3)
contains the dataset card and data but no standalone license file. The
[exact pinned dataset card](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/blob/91aa3ed51b709be6457e12d00300a6a596d4c6a3/README.md)
shows dataset metadata without a license field. The separate pinned
[harness license](https://raw.githubusercontent.com/SWE-bench/SWE-bench/726c5461e2ef52d83cf1ea2107870a8bb3328d57/LICENSE)
is therefore not used to fill that gap.

## First public preparation pilot

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
