"""fw-heudiconv heuristic for the r01network project.

Keys on the Flywheel **acquisition label** (surfaced by our fw-heudiconv fork as
``SeqInfo.acquisition_label``) and resolves it through
:func:`network_fmri.curation.map_acquisition` — the byte-identical copy of the
legacy bidsify map that produced the canonical Oak BIDS. So BIDS naming matches
Oak by construction, and raw-DICOM ``series_description`` mess (``actual_goNogo``,
snake_case duals, the ``shapeMaching`` typo) is bypassed.

Load with ``fw-heudiconv-curate --heuristic this_file.py``.

Session renumbering (raw accession -> ses-01..) is dynamic: ``run.py`` queries
Flywheel per subject, sorts by timestamp, and writes a ``{accession: "NN"}`` map
to JSON pointed to by ``FWBIDS_SESSION_MAP``; ``ReplaceSession`` consults it.
The multi-echo ``{echo}`` entity is resolved per raw-echo NIfTI by the fork's
``apply_heuristic`` (derivative NIfTIs dropped), yielding ``echo-1/2/3``.

**Cross-subject move (session 22752 -> s10)**: ``ReplaceSubject`` only receives
the subject label, never the session. ``run.py`` curates such a session under its
source Flywheel subject with ``FWBIDS_FORCE_SUBJECT`` set to the target — see
``ReplaceSubject`` below — so it lands under the right BIDS subject.
"""

import os

from network_fmri import curation, session_map


def create_key(template, outtype=("nii.gz",), annotation_classes=None):
    if template is None or not template:
        raise ValueError("Template must be a valid format string")
    return template, outtype, annotation_classes


def ReplaceSubject(label):
    """Flywheel subject label -> BIDS subject label.

    ``FWBIDS_FORCE_SUBJECT`` (set by run.py for a cross-subject reassignment
    invocation) overrides everything; otherwise apply the alias map.
    """
    forced = os.environ.get("FWBIDS_FORCE_SUBJECT")
    if forced:
        return forced
    return curation.SUBJECT_ALIASES.get(label, label)


def ReplaceSession(label):
    """Flywheel session label (accession) -> BIDS session number (``NN``).

    Reads the per-run ``FWBIDS_SESSION_MAP``. Falls back to the raw label if the
    map is absent (offline tests) or lacks the accession.
    """
    return session_map.load_env_map().get(label, label)


def _templates_for(entry):
    """Build the BIDS key template(s) for a map_acquisition() result.

    Returns a list (usually one; two for fmap → fieldmap + magnitude), or []
    to skip. Oak puts ``run-1`` on every modality. Multi-echo func uses
    ``{echo}``, resolved per raw-echo NIfTI by the fork's apply_heuristic (drops
    derivatives → echo-1/2/3); fmap templates end in ``_fieldmap``/``_magnitude``
    so the fork selects the matching NIfTI. Multi-run (7 cases) is a later item.
    """
    mod = entry.get("modality")
    base = "sub-{subject}/{session}/" + mod + "/sub-{subject}_{session}"
    if mod == "func":
        return [f"{base}_task-{entry['task']}_run-1_echo-{{echo}}_bold"]
    if mod == "anat":
        acq = entry.get("acq")
        acq_ent = f"_acq-{acq}" if acq else ""
        return [f"{base}{acq_ent}_run-1_{entry['suffix']}"]
    if mod == "dwi":
        acq = entry.get("acq")
        acq_ent = f"_acq-{acq}" if acq else ""
        return [f"{base}{acq_ent}_dir-{entry['dir']}_run-1_dwi"]
    if mod == "fmap":
        return [f"{base}_run-1_fieldmap", f"{base}_run-1_magnitude"]
    return []


def infotodict(seqinfo):
    """Assign each seqinfo series to a BIDS key via the acquisition-label map.

    Keys are created lazily per distinct template so we don't have to enumerate
    the whole battery up front. Skips unmapped labels, localizers
    (SKIP_ACQUISITIONS -> map_acquisition returns None), and NEVER_DOWNLOAD.
    """
    info = {}
    for s in seqinfo:
        label = getattr(s, "acquisition_label", None)
        if not label:
            continue
        fw_subject = getattr(s, "patient_id", None)  # fork: patient_id = subject.label
        bids_subject = ReplaceSubject(fw_subject) if fw_subject else None
        # NEVER_DOWNLOAD is keyed on (bids_subject, flywheel_session); session is
        # not on SeqInfo, so this check is completed during the dry-run phase.
        entry = curation.map_acquisition(label)
        if entry is None:
            continue
        for template in _templates_for(entry):
            key = create_key(template)
            info.setdefault(key, []).append(s.series_id)
    return info
