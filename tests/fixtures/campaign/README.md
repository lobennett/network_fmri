# Campaign reconstruction fixtures

`mechababs-base/` contains unmodified `mechababs/select.py`, `merge_config.py`,
and their MIT license from `lobennett/mechababs` commit
`ccb3f5b76bf17df09403ecef922b5c0acb8c78f3`. Tests apply the published patch to
these source files and execute the reconstructed consumers offline. Keep these
base files unchanged when refreshing the patch against the same commit.

`sessions.tsv` is synthetic: a fieldmap-only visit, anatomical-only visit,
functional-only visit, and a multimodal subject with modalities split over visits.
It contains no participant data.
