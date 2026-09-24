import hashlib
import json

import nibabel as nib
import numpy as np


def test_trim_versions_survive_rerun_and_link_exact_bytes(tmp_path):
    from network_fmri.prepare.trim import trim_one
    from network_fmri.records.native_lineage import collect_native_lineage
    bold = tmp_path / "sub-s03/ses-01/func/sub-s03_ses-01_task-rest_bold.nii.gz"
    bold.parent.mkdir(parents=True)
    sidecar = bold.with_name(bold.name.replace(".nii.gz", ".json"))
    sidecar.write_text('{"RepetitionTime": 1.5}')
    nib.save(nib.Nifti1Image(np.zeros((2, 2, 2, 12)), np.eye(4)), bold)
    before = hashlib.sha256(bold.read_bytes()).hexdigest()
    assert trim_one(bold) == "trimmed"
    after = hashlib.sha256(bold.read_bytes()).hexdigest()
    provenance = json.loads(sidecar.read_text())["NetworkFMRITrim"]
    assert provenance["input_sha256"] == before
    assert provenance["output_sha256"] == after
    unchanged = sidecar.read_bytes()
    assert trim_one(bold) == "already"
    assert sidecar.read_bytes() == unchanged
    receipt, = collect_native_lineage(tmp_path, "raw")
    assert {row["content_id"] for row in receipt["artifacts"]} == {"sha256:" + before, "sha256:" + after}
    assert receipt["links"][0]["relation"] == "trim_dummy"
