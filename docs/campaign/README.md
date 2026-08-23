# Campaign snapshot

Reference copies of the mechababs campaign's study-specific config. The LIVE copies are in
the campaign dataset at `$SCRATCH/mechababs_campaigns/r01network/code/mechababs/` — edit
there, then refresh these. They are duplicated here because the campaign lives on
90-day-purge scratch and these files are the only record of the preprocessing half.

- `MRIQC-24.0.2.yaml`, `fMRIPrep-25.2.5.yaml`, `XCP-D-26.0.2.yaml` — pipeline configs
  (flag decisions documented inline; see also ../SCAN-NOTES.md §7)
- `sherlock.yaml` — cluster config incl. `array_throttle: 12`
- `mechababs-local-patches.diff` — our two patches to the vendored mechababs:
  per-pipeline `processing_level`, and `cluster_resources_override` applied after the
  cluster block (and stripped from the babs config)

To recreate the campaign from nothing: mechababs `bootstrap.sh` + `configure` with these
pipelines/cluster, `add-dataset` per cohort against `$SCRATCH/network_fmri/<cohort>/bids`,
apply the patches, build the container shims from
`/home/groups/russpold/singularity_images/{mriqc_24.0.2,fmriprep_25.2.5,xcpd_26.0.2}.sif`.
CAUTION: `configure` REWRITES the ledger — never run it on a campaign with in-flight cells.
Failed cells: `iterate` only reports them; retry with `babs submit <project>`.
