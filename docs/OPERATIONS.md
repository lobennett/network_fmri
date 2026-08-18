# Operations

## Monitoring

```bash
squeue --me | grep nf-
grep -rh 'failed after' $SCRATCH/network_fmri/logs/*/*.err   # hard failures
grep -rh 'exported'     $SCRATCH/network_fmri/logs/*/*.out | wc -l
```

Resubmit failures with the same `--cohort` plus explicit subjects, so output paths
stay cohort-scoped:

```bash
network_fmri submit fw-heudiconv --cohort validation --subject s180 s247 --live
```

## Flywheel returns HTTP 500 under load

`ApiException: (500) Unexpected error during deserialization` during curate. Seen
on 5 of 8 tasks at `--throttle 8`, including a subject that had just succeeded when
run alone. Server-side and load-related.

Both curate and export retry (`--retries`, default 3). `--throttle 3` ran 51 tasks
with 2 transient retries and no hard failures.

## Export destroys its output directory on conflict

`fw-heudiconv-export` calls `shutil.rmtree` on its output root as soon as a file it
wants to write already exists. Consequences:

- every array task exports into a directory it owns
- `export()` wipes its target before downloading, so a partial export cannot
  poison later retries
- never point `--out` at a shared or precious tree

`fw-heudiconv-export --dry-run` also deletes its destination on the way out, so it
is never used.

## git-annex version

datalad >= 1.0 requires git-annex >= 10.20230126. Sherlock's `system/git-annex`
module is 8.20210622 and is rejected. `dataset.py` provisions a current build with
`datalad-installer` into `$SCRATCH/git-annex` on first use; no module, no container.

If the install fails on TLS, that is uv's CPython having no CA path —
`dataset.py` already sets `SSL_CERT_FILE` from `certifi` for the installer
subprocess.

Do not put a git-annex standalone bundle's `bin/` on PATH; those shims need the
bundle's own environment, and `bin/` also shadows `git`, which fails confusingly
inside datalad.

## Container cache

Keep `APPTAINER_CACHEDIR` on `$SCRATCH` — the layer cache is large and `$HOME` is
15 GB. Pulled images go to `$SCRATCH/containers` (`--cache` or
`NETWORK_FMRI_CONTAINERS` to move them). Pulls are atomic: written to a temp name
and renamed, so concurrent tasks cannot read a half-written image.

Apptainer does not mount Lustre automatically. `container.py` binds `/scratch`,
`/oak` and `/home/groups` when present; without that a dataset under `/scratch` is
invisible inside the container.

## Merging is slow

`rsync -a` over ~1 TB on Lustre runs for hours and will exceed an interactive
timeout. It is idempotent, so a partial merge resumes. Submit it.

## Scratch purge

`$SCRATCH` deletes files unmodified for 90 days, and `touch` does not count. That
applies to the exported trees, the venv, the pulled containers, the provisioned
git-annex, and DataLad annex objects. Versioning is not durability — decide where
the canonical copy lives on `$OAK`.
