"""Human final review bound to committed fMRIPrep and registration evidence."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

from network_fmri.milestones import write_json_atomic
from network_fmri.mriqc import _git, _gitlink


def review_path(study, subject):
    if not re.fullmatch(r'[A-Za-z0-9]+', subject):
        raise ValueError('invalid subject')
    return Path(study) / f'code/network_fmri/output_review/sub-{subject}.json'


def _committed_json(root, path, runner):
    relative = path.relative_to(root).as_posix()
    committed = _git(root, 'show', f'HEAD:{relative}', runner=runner)
    if committed.strip() != path.read_text().strip():
        raise RuntimeError(f'review evidence has uncommitted changes: {relative}')
    return json.loads(committed)


def current_evidence(study, source_project, subject, *, runner=subprocess.run):
    study = Path(study).resolve()
    source = (study / source_project).resolve()
    if not source.is_relative_to(study / 'derivatives') or source.parent != study / 'derivatives':
        raise ValueError('invalid fMRIPrep project')
    review = source.with_name(source.name + '+review')
    from network_fmri.registration_qc import VERSION
    registration = source.with_name(source.name.replace('fMRIPrep-', f'fmriprepviz-{VERSION}-', 1))
    commits = {}
    for root in (source, review, registration):
        relative = root.relative_to(study).as_posix()
        commit = _git(root, 'rev-parse', 'HEAD', runner=runner)
        if _gitlink(study, 'HEAD', relative, runner=runner) != commit:
            raise RuntimeError(f'outputs are not registered at their current commit: {relative}')
        if _git(root, 'status', '--porcelain', '--untracked-files=all', runner=runner):
            raise RuntimeError(f'outputs have uncommitted changes: {relative}')
        commits[relative] = commit
    checks = _committed_json(review, review / 'code/network_fmri/fmriprep-evidence.json', runner)
    viz = _committed_json(registration, registration / 'code/network_fmri/registration-qc.json', runner)
    source_commit = commits[source_project]
    if (checks['inputs']['source_commit'] != source_commit
            or viz['inputs']['source_commit'] != source_commit
            or viz['inputs']['source_project'] != source_project):
        raise RuntimeError('final review evidence belongs to different fMRIPrep outputs')
    result = next((r for r in checks['subjects'] if r['subject'] == subject), None)
    if result is None or not any(r['subject'] == subject for r in viz['inputs']['subjects']):
        raise RuntimeError('subject is missing from final review evidence')
    issues = list(result['issues'])
    if checks['status'] != 'success' or viz['status'] != 'success':
        issues.append('automated output checks are not successful')
    return {'source_project': source_project, 'source_commit': source_commit,
            'output_commits': commits}, issues


def record_review(config, subject, decision, reviewer, notes='', *, runner=subprocess.run):
    if subject not in config.subjects:
        raise ValueError('subject is not in this campaign')
    if decision not in {'approved', 'needs-correction'} or not reviewer.strip():
        raise ValueError('a valid decision and reviewer are required')
    if decision == 'needs-correction' and not notes.strip():
        raise ValueError('needs-correction requires notes describing the issue')
    from network_fmri.processing import ProcessingManager
    stage = next(s for s in ProcessingManager(config, runner=runner).plan() if s.stage == 'fmriprep')
    if stage.state != 'complete':
        raise RuntimeError('fMRIPrep must be merged before final review')
    study = config.mechababs.study_dir
    inputs, issues = current_evidence(study, stage.project, subject, runner=runner)
    if decision == 'approved' and issues:
        raise RuntimeError('cannot approve failed output checks: ' + '; '.join(issues))
    path = review_path(study, subject)
    prior = path.read_bytes() if path.exists() else None
    write_json_atomic(path, {'schema_version': 1, 'subject': subject, 'decision': decision,
                            'reviewer': reviewer.strip(), 'reviewed_at': datetime.now(timezone.utc).isoformat(),
                            'notes': notes.strip(), 'inputs': inputs})
    try:
        runner(('datalad', 'save', '-d', str(study), '-m', f'Final output review: sub-{subject} {decision}',
                '--', str(path)), check=True)
    except Exception:
        if prior is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(prior)
        raise
    return path


def read_review(study, subject, *, source_project=None, runner=subprocess.run):
    """Only return a committed decision for the exact current evidence."""
    path = review_path(study, subject)
    if not path.exists():
        return None
    try:
        value = _committed_json(Path(study), path, runner)
        if not isinstance(value, dict) or not isinstance(value.get('inputs'), dict):
            return None
        if (value['subject'] != subject or value['decision'] not in {'approved', 'needs-correction'}
                or not value.get('reviewer') or not value.get('reviewed_at')
                or (value['decision'] == 'needs-correction' and not value.get('notes'))):
            return None
        source = source_project or value['inputs']['source_project']
        current, issues = current_evidence(study, source, subject, runner=runner)
        if value['inputs'] != current or (value['decision'] == 'approved' and issues):
            return None
        return value
    except (RuntimeError, ValueError, OSError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError):
        return None
