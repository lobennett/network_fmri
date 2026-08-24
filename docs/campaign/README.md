# Campaign snapshot

Reference copies of the mechababs campaign's study-specific config. The LIVE copies are in
the campaign dataset at `$SCRATCH/mechababs_campaigns/r01network/code/mechababs/` — edit
there, then refresh these. They are duplicated here because the campaign lives on
90-day-purge scratch and these files are the only record of the preprocessing half.

- `MRIQC-24.0.2.yaml`, `fMRIPrep-25.2.5.yaml`, `XCP-D-26.0.2.yaml` — pipeline configs
  (flag decisions documented inline; see also ../SCAN-NOTES.md §7)
- `sherlock.yaml` — cluster config incl. `array_throttle: 12`
- `mechababs-local-patches.diff` — our patches to the vendored mechababs:
  per-pipeline `processing_level`; `cluster_resources_override` applied after the cluster
  block (and stripped from the babs config); and `primary_input`, below
- `babs-local-patches.diff` — one patch to the vendored babs: a `pre_app_commands` config
  key, whose commands run after the input unzip and before the app. XCP-D needs it (below);
  nothing else uses it.

## `primary_input`, and why XCP-D needs it

babs passes `input_datasets[0]` as the BIDS app's positional input directory — its own
comment is "The input dataset is always the first one in the list". merge_config used to
force the raw-BIDS entry first unconditionally, which is right for MRIQC and fMRIPrep but
wrong for anything consuming a predecessor's output: XCP-D was handed `sourcedata/raw` and
would have post-processed the raw tree. `mechababs.primary_input` names the entry that
leads, defaulting to `BIDS`, so only XCP-D sets it. Verify after scaffolding — the
positional in `<project>/code/bids-xcpd_zip.sh` must be
`sourcedata/fMRIPrep-25.2.5/fMRIPrep-25.2.5` (babs resolves a zipped input to
`path_in_babs/<name>`, matching the zip's own top folder).

## The T2w-only anat session, and why XCP-D needs pruning

XCP-D counts a session as anatomical if it holds a T1w **or** a T2w, and accepts only two
shapes: one anatomical session per functional session, or one serving all of them
(`parser.py:1080`). This study acquires the T2w in a different session from the T1w for 9
of 46 subjects — 5 of those because of which duplicate T1w `qa-reject` kept — so fMRIPrep
writes a T2w-only `anat/` there. XCP-D reads that as a second anatomical session and dies
before doing any work: *"Found 11 functional sessions that do not have anatomical data"*.

The XCP-D yaml's `pre_app_commands` drops any `anat/` holding no T1w from XCP-D's unzipped
copy of the input. That costs nothing scientifically: fMRIPrep has already used the T2w to
refine the surfaces, and the surfaces are what XCP-D consumes. One anatomical session
remains, so XCP-D takes the one-to-all path.

Two other approaches were tested and do **not** work: `--bids-filter-file` (the
filter-to-session-list path in `parser.py` is commented out) and a `.bidsignore` (does not
hide the files from the layout query). `--session-id` cannot help either — excluding the
T2w session would also drop its functional runs.

To recreate the campaign from nothing: mechababs `bootstrap.sh` + `configure` with these
pipelines/cluster, `add-dataset` per cohort against `$SCRATCH/network_fmri/<cohort>/bids`,
apply the patches, then `network_fmri shim` once per pipeline (babs clones a shim dataset,
not a `.sif` path — a missing one fails `babs init` with "Failed to clone from any
candidate source URL").
CAUTION: `configure` REWRITES the ledger — never run it on a campaign with in-flight cells.
Failed cells: `iterate` only reports them; retry with `babs submit <project>`.
