"""fw-heudiconv heuristic: acquisition label -> BIDS filename.

The default ``--heuristic``. Loaded by path, so ``network_fmri`` must be importable
in the job. The Replace* hooks read env set by curate.py; without it they pass
through. See docs/PIPELINE.md.
"""

import os

from network_fmri.fw2bids import sessions
from network_fmri.fw2bids.acquisitions import map_acquisition


def create_key(template, outtype=("nii.gz",), annotation_classes=None):
    if template is None or not template:
        raise ValueError("Template must be a valid format string")
    return template, outtype, annotation_classes


def ReplaceSubject(label):
    """Flywheel subject label -> canonical subject."""
    return os.environ.get(sessions.FORCE_SUBJECT_ENV) or sessions.SUBJECT_ALIASES.get(label, label)


def ReplaceSession(label):
    """Accession -> bare session number (``"01"``); the engine adds the ``ses-``."""
    return sessions.load_env_map().get(label, label)


def _templates_for(entry):
    """BIDS template(s) for a :func:`map_acquisition` result; two for fmap."""
    mod = entry.get("modality")
    base = "sub-{subject}/{session}/" + mod + "/sub-{subject}_{session}"
    if mod == "func":
        # {seqitem} and {echo} are both resolved by the fork.
        return [f"{base}_task-{entry['task']}_run-{{seqitem}}_echo-{{echo}}_bold"]
    if mod == "anat":
        acq = entry.get("acq")
        return [f"{base}{f'_acq-{acq}' if acq else ''}_run-1_{entry['suffix']}"]
    if mod == "dwi":
        acq = entry.get("acq")
        return [f"{base}{f'_acq-{acq}' if acq else ''}_dir-{entry['dir']}_run-1_dwi"]
    if mod == "fmap":
        return [f"{base}_run-1_fieldmap", f"{base}_run-1_magnitude"]
    return []


# A lone _fieldmap requires Units (validator: UNITS_MUST_DEFINE); these are Hz.
MetadataExtras = {
    create_key(_templates_for({"modality": "fmap"})[0]): {"Units": "Hz"},
}


def infotodict(seqinfo):
    """Group series by BIDS key; unmapped and skipped series are left alone."""
    info = {}
    for s in seqinfo:
        label = getattr(s, "acquisition_label", None)
        entry = map_acquisition(label) if label else None
        if entry is None:
            continue
        for template in _templates_for(entry):
            info.setdefault(create_key(template), []).append(s.series_id)
    return info
