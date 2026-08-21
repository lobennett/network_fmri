"""The invariants `check` asserts, each against a tree built to violate exactly one.

`check` is what makes a rebuild self-verifying, so a check that silently stops detecting
its defect is worse than no check at all.
"""

import json

import nibabel as nib
import numpy as np

from network_fmri.qa.check import check_anat, check_b0link, check_events, check_trim


def bold(func, name, n_vol=100, tr=1.49, **sidecar):
    func.mkdir(parents=True, exist_ok=True)
    img = nib.Nifti1Image(np.zeros((2, 2, 2, n_vol), dtype=np.int16), np.eye(4))
    img.header.set_zooms((1.0, 1.0, 1.0, tr))
    nib.save(img, func / f"{name}_bold.nii.gz")
    (func / f"{name}_bold.json").write_text(json.dumps(sidecar))


def anat(tree, sub, ses, suffix):
    d = tree / sub / ses / "anat"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sub}_{ses}_{suffix}.nii.gz").touch()


class TestEvents:
    def _tree(self, tmp_path, onsets):
        func = tmp_path / "sub-s01" / "ses-01" / "func"
        bold(func, "sub-s01_ses-01_task-flanker_run-1", n_vol=100,
             NumberOfVolumesDiscardedByUser=7)
        rows = "\n".join(f"{o}\t1.0" for o in onsets)
        (func / "sub-s01_ses-01_task-flanker_run-1_events.tsv").write_text(
            f"onset\tduration\n{rows}\n")
        return tmp_path

    def test_onsets_inside_the_scan_pass(self, tmp_path):
        # 100 volumes x 1.49 s = 149 s.
        assert check_events(self._tree(tmp_path, [0, 50, 148.9])) == []

    def test_onset_at_or_past_the_end_fails(self, tmp_path):
        bad = check_events(self._tree(tmp_path, [0, 50, 200]))
        assert len(bad) == 1 and "after the" in bad[0]

    def test_no_events_file_is_not_a_failure(self, tmp_path):
        """rest runs and unpaired runs legitimately have none."""
        func = tmp_path / "sub-s01" / "ses-01" / "func"
        bold(func, "sub-s01_ses-01_task-rest_run-1", NumberOfVolumesDiscardedByUser=7)
        assert check_events(tmp_path) == []


class TestAnat:
    def test_one_of_each_passes(self, tmp_path):
        anat(tmp_path, "sub-s01", "ses-01", "T1w")
        anat(tmp_path, "sub-s01", "ses-02", "T2w")
        assert check_anat(tmp_path) == []

    def test_two_t1w_across_sessions_fails(self, tmp_path):
        """The qa-reject gate. Two T1w means a mark did not take effect."""
        anat(tmp_path, "sub-s01", "ses-01", "T1w")
        anat(tmp_path, "sub-s01", "ses-05", "T1w")
        bad = check_anat(tmp_path)
        assert len(bad) == 1 and "2 T1w" in bad[0]

    def test_t1w_only_is_allowed(self, tmp_path):
        """s43 has no T2w in any session."""
        anat(tmp_path, "sub-s43", "ses-01", "T1w")
        assert check_anat(tmp_path) == []

    def test_missing_t1w_fails(self, tmp_path):
        anat(tmp_path, "sub-s01", "ses-01", "T2w")
        assert any("no T1w" in b for b in check_anat(tmp_path))


class TestTrim:
    def test_stamped_passes(self, tmp_path):
        bold(tmp_path / "sub-s01" / "ses-01" / "func", "sub-s01_ses-01_task-flanker_run-1",
             NumberOfVolumesDiscardedByUser=7)
        assert check_trim(tmp_path) == []

    def test_unstamped_fails(self, tmp_path):
        bold(tmp_path / "sub-s01" / "ses-01" / "func", "sub-s01_ses-01_task-flanker_run-1",
             RepetitionTime=1.49)
        assert len(check_trim(tmp_path)) == 1


class TestB0Link:
    def _session(self, tmp_path, fmap_fields, bold_fields, with_bold=True):
        ses = tmp_path / "sub-s01" / "ses-01"
        fmap = ses / "fmap"
        fmap.mkdir(parents=True)
        (fmap / "sub-s01_ses-01_run-1_fieldmap.nii.gz").touch()
        (fmap / "sub-s01_ses-01_run-1_fieldmap.json").write_text(json.dumps(fmap_fields))
        if with_bold:
            bold(ses / "func", "sub-s01_ses-01_task-flanker_run-1",
                 NumberOfVolumesDiscardedByUser=7, **bold_fields)
        return tmp_path

    def test_linked_passes(self, tmp_path):
        t = self._session(tmp_path, {"B0FieldIdentifier": "s01_ses-01"},
                          {"B0FieldSource": "s01_ses-01"})
        assert check_b0link(t) == []

    def test_unlinked_bold_fails(self, tmp_path):
        t = self._session(tmp_path, {"B0FieldIdentifier": "s01_ses-01"}, {})
        assert any("no B0FieldSource" in b for b in check_b0link(t))

    def test_unlinked_fieldmap_fails(self, tmp_path):
        t = self._session(tmp_path, {}, {"B0FieldSource": "s01_ses-01"})
        assert any("no B0FieldIdentifier" in b for b in check_b0link(t))

    def test_fieldmap_only_session_is_allowed(self, tmp_path):
        """b0link marks these orphan_fmap by design: nothing to link to."""
        t = self._session(tmp_path, {}, {}, with_bold=False)
        assert check_b0link(t) == []
