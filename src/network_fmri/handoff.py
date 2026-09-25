"""Run MRIQC to the scan-review boundary, preserving all human decisions."""
from __future__ import annotations

from collections import Counter
import csv
import fcntl
import json
import logging
from pathlib import Path
import subprocess
import tempfile
import time

from network_fmri.mriqc import prepare_mriqc_review
from network_fmri.pipeline import save_stage_result
from network_fmri.processing import ProcessingManager
from network_fmri.stages.decisions import generate_decisions


def run_processing(config, *, interval=300, manager=None, prepare_review=None,
                   sleep=time.sleep, observe=None):
    """Advance upstream jobs until human review or intervention is required."""
    if interval < 1:
        raise ValueError("poll interval must be at least one second")
    lock = _lock_path(config.mechababs.study_dir)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another processing handoff controller is running") from error
        manager = manager or ProcessingManager(config)
        prepare_review = prepare_review or (lambda stage: _prepare_boundary(config, stage))
        observe = observe or (lambda: _refresh_records(config, manager))
        reviewed, pauses = set(), {}
        while True:
            observe()
            stages = manager.plan()
            advanced = False
            for stage in stages:
                print(f"{stage.stage}: {stage.state}", flush=True)
                if stage.state == "complete":
                    if stage.stage not in reviewed and stage.stage not in pauses:
                        pause = prepare_review(stage.stage)
                        observe()
                        if pause is not None:
                            pauses[stage.stage] = pause
                        else:
                            reviewed.add(stage.stage)
                    continue
                if stage.state == "blocked" or any(name in pauses for name in manager.dependencies(stage.stage)):
                    continue
                if stage.state not in {"ready", "active"}:
                    raise RuntimeError(f"{stage.stage} is {stage.state}; intervention required")
                manager.advance(stage.stage)
                advanced = True
            if advanced:
                sleep(interval)
                continue
            if pauses:
                return next(iter(pauses.values())) if len(pauses) == 1 else {
                    "state": "awaiting-reviews", "reviews": list(pauses.values())}
            if all(stage.state == "complete" for stage in stages):
                return {"state": "awaiting-output-review"}
            raise RuntimeError("processing is blocked; intervention required")


def _refresh_records(config, manager):
    from network_fmri.records import build_index
    from network_fmri.records.history import record_status

    record_status(config, manager.status())
    study = config.mechababs.study_dir
    output = study.parent / ".network-fmri-cache" / study.name / "records.sqlite"
    try:
        build_index(config, output)
    except Exception:
        # The disposable dashboard cache cannot prevent an otherwise valid job
        # handoff. Its previous build time remains visible until refresh succeeds.
        logging.getLogger(__name__).exception("Dashboard refresh failed; retaining last index")


def _prepare_boundary(config, stage):
    from network_fmri import pipeline
    from network_fmri.curation import apply_curation
    from network_fmri.milestones import receipt_path
    from network_fmri.processing import _require_committed_milestone
    from network_fmri.surface_evidence import prepare_surface_evidence

    study = config.mechababs.study_dir
    if stage == "mriqc":
        curated = receipt_path(config.paths.bids_dir, "bids-curated-validated")
        if curated.exists():
            _require_committed_milestone(config.paths.bids_dir, "bids-curated-validated", subprocess.run)
            pipeline.require_committed_approval(config)
            return None
        evidence = prepare_mriqc_review(config)
        manifest = study / "code/network_fmri/scan_decisions.tsv"
        _prepare_decisions(config, evidence.evidence_dir, manifest)
        try:
            pipeline.require_committed_approval(config)
        except RuntimeError as error:
            return {"state": "awaiting-scan-review", "manifest": str(manifest), "reason": str(error)}
        result = apply_curation(config.paths.bids_dir, manifest, config.validator.image)
        save_stage_result(config.paths.bids_dir, result, config=config)
    elif stage == "anatomical":
        from network_fmri.surface_corrections import correction_state, refresh_surface_review
        state = correction_state(config)
        if state and state["phase"] != "ready":
            return {"state": "awaiting-surface-correction", "workspace": state["workspace"]}
        evidence = prepare_surface_evidence(config)
        manifest = study / "code/network_fmri/surface_review.tsv"
        result = refresh_surface_review(config, evidence, state)
        if result is not None:
            save_stage_result(study, result, config=config)
        try:
            pipeline.require_committed_surface_approval(config)
        except RuntimeError as error:
            return {"state": "awaiting-surface-review", "manifest": str(manifest), "reason": str(error)}
    elif stage == "fmriprep":
        from network_fmri.registration_qc import prepare_registration_qc
        output = prepare_registration_qc(config)
        return {"state": "awaiting-output-review", "registration_qc": str(output)}
    else:
        raise ValueError(f"unknown review boundary: {stage}")
    return None


def run_mriqc(config, *, interval=300, manager=None, sleep=time.sleep):
    """Poll upstream through merge, prepare review, then stop without approving.

    Run in a Slurm controller job for unattended operation. Restarting this command
    re-reads campaign state; neither failed jobs nor existing reviews are reset.
    """
    if interval < 1:
        raise ValueError('poll interval must be at least one second')
    root = config.mechababs.study_dir
    lock = _lock_path(root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('another MRIQC handoff controller is running') from error
        manager = manager or ProcessingManager(config)
        while True:
            stage = next(s for s in manager.plan() if s.stage == 'mriqc')
            print(f'mriqc: {stage.state}', flush=True)
            if stage.state == 'complete':
                break
            if stage.state not in {'ready', 'active'}:
                raise RuntimeError(f'MRIQC is {stage.state}; intervention required')
            manager.advance('mriqc')
            sleep(interval)
        evidence = prepare_mriqc_review(config)
        manifest = root / 'code/network_fmri/scan_decisions.tsv'
        created = _prepare_decisions(config, evidence.evidence_dir, manifest)
        with manifest.open(newline='') as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
        flags = Counter(flag for row in rows for flag in row['flags'].split(',') if flag)
        return {
            'state': 'evidence-error' if flags.get('fd_thres_mismatch') else 'awaiting-scan-review',
            'manifest': str(manifest), 'evidence_dir': str(evidence.evidence_dir),
            'created': created, 'rows': len(rows), 'flags': dict(flags),
        }


def _prepare_decisions(config, evidence: Path, manifest: Path) -> bool:
    metadata = manifest.with_suffix('.meta.json')
    pair = (manifest, metadata)
    if any(path.is_symlink() for path in pair):
        raise RuntimeError('review files must not be symlinks')
    if manifest.exists() != metadata.exists():
        raise RuntimeError('incomplete review pair; recover it before restarting')
    if not manifest.exists():
        result = generate_decisions(config.paths.bids_dir, mriqc_dir=evidence, output=manifest)
        save_stage_result(config.mechababs.study_dir, result)
        return True

    # Generate a fresh baseline elsewhere. Compare provenance, not reviewer edits.
    # A changed input requires explicit regeneration/migration, never overwriting.
    with tempfile.TemporaryDirectory(prefix='network-review-check-') as directory:
        baseline = Path(directory) / 'scan_decisions.tsv'
        generate_decisions(config.paths.bids_dir, mriqc_dir=evidence, output=baseline)
        stored = json.loads(metadata.read_text())
        current = json.loads(baseline.with_suffix('.meta.json').read_text())
        for value in (stored, current):
            for field in ('approved_manifest_sha256', 'approved_metadata_sha256'):
                value.pop(field, None)
        if stored != current:
            raise RuntimeError('review evidence changed; existing decisions were preserved')
        review_fields = {'decision', 'approved', 'reason_code', 'reason_detail', 'reviewer', 'reviewed_at', 'notes'}
        def immutable_rows(path):
            with path.open(newline='') as stream:
                rows = csv.DictReader(stream, delimiter='\t')
                return sorted(json.dumps({k: v for k, v in row.items() if k not in review_fields},
                                         sort_keys=True) for row in rows)
        if immutable_rows(manifest) != immutable_rows(baseline):
            raise RuntimeError('review evidence changed; existing decisions were preserved')
    return False


def _lock_path(root: Path) -> Path:
    directory = subprocess.run(
        ('git', 'rev-parse', '--absolute-git-dir'), cwd=root,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return Path(directory) / 'network-fmri-handoff.lock'
