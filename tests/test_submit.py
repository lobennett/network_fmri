"""Render + DAG-wiring tests for the network_fmri submit/orchestration layer.

Pure: no live Flywheel, no real Slurm. Stage renders are asserted on the sbatch
text; the pipeline DAG mocks ``submit_sbatch`` to return fake job ids and checks
the ``afterok`` dependency chain.
"""

from __future__ import annotations

import argparse

import pytest

from network_fmri.submit import (
    _common,
    curate,
    datalad,
    events,
    export,
    merge,
    pipeline,
    trim,
)
from network_fmri.submit import _slurm


# --------------------------------------------------------------------------- #
# roster resolution
# --------------------------------------------------------------------------- #
def _ns(**kw):
    return argparse.Namespace(**kw)


def test_roster_excluded_dict_yields_keys():
    """`excluded` sample is a {subject: reason} dict → roster is its sorted keys."""
    subjects = _common.resolve_roster(_ns(cohort="excluded", subjects=None))
    assert subjects == sorted(subjects)
    assert "s214" in subjects and "s1320" in subjects
    # reasons must not leak in
    assert all(s.startswith("s") for s in subjects)


def test_roster_discovery_list():
    subjects = _common.resolve_roster(_ns(cohort="discovery", subjects=None))
    assert subjects == ["s03", "s10", "s19", "s29", "s43"]


def test_roster_subjects_override():
    subjects = _common.resolve_roster(_ns(cohort="validation", subjects=["s286", "s76"]))
    assert subjects == ["s286", "s76"]


# --------------------------------------------------------------------------- #
# resolve_resources override logic
# --------------------------------------------------------------------------- #
def test_resolve_resources_uses_default_when_none():
    args = _ns(nthreads=None, mem_gb=None, time=None)
    assert _slurm.resolve_resources(args, curate.DEFAULT_RESOURCES) == curate.DEFAULT_RESOURCES


def test_resolve_resources_override():
    args = _ns(nthreads=16, mem_gb=None, time="08:00:00")
    got = _slurm.resolve_resources(args, curate.DEFAULT_RESOURCES)
    assert got["nthreads"] == 16
    assert got["time"] == "08:00:00"
    assert got["mem_gb"] == curate.DEFAULT_RESOURCES["mem_gb"]  # untouched default


# --------------------------------------------------------------------------- #
# per-stage --dry-run render
# --------------------------------------------------------------------------- #
def _render(stage_mod, argv):
    args = stage_mod.get_parser().parse_args(argv)
    return stage_mod.render(args)


def test_curate_render_array_and_command(tmp_path):
    script = _render(
        curate, ["--cohort", "discovery", "--staging", str(tmp_path)]
    )
    # resources
    assert "--cpus-per-task=2" in script
    assert "--mem=8G" in script
    assert "--time=02:00:00" in script
    # array over the 5-subject discovery roster (0..4)
    assert "--array=0-4%5" in script
    # payload command + uv run prefix (default, no container)
    assert "uv run --no-sync fw2bids discovery --subject" in script
    assert "--live" in script
    # env exports present on the uv path
    assert "UV_PROJECT_ENVIRONMENT=/scratch/users/logben/network_fmri_venv" in script


def test_export_render(tmp_path):
    script = _render(export, ["--cohort", "discovery", "--staging", str(tmp_path)])
    assert "--cpus-per-task=4" in script
    assert "--mem=16G" in script
    assert "--array=0-4%5" in script
    assert "fw2bids export discovery --subject" in script
    assert f"--out {tmp_path}/parts/discovery/" in script


def test_trim_render(tmp_path):
    script = _render(trim, ["--cohort", "validation", "--staging", str(tmp_path)])
    assert "--cpus-per-task=8" in script
    assert "--mem=24G" in script
    # validation roster is 41 subjects → array 0-40
    assert "--array=0-40%5" in script
    assert f"fw2bids trim {tmp_path}/validation --subjects" in script
    assert "--jobs 8" in script


def test_merge_render(tmp_path):
    script = _render(merge, ["--cohort", "discovery", "--staging", str(tmp_path)])
    assert "--array=" not in script  # single job
    assert f"rsync -a {tmp_path}/parts/discovery/*/ {tmp_path}/discovery/" in script


def test_events_render_uses_manifest_and_behavioral(tmp_path):
    script = _render(
        events,
        [
            "--cohort", "discovery", "--staging", str(tmp_path),
            "--behavioral-dir", "/oak/raw", "--manifest", "/some/reconciliation_discovery.tsv",
        ],
    )
    assert "network-events run" in script
    assert "--behavioral-dir /oak/raw" in script
    assert f"--bids-dir {tmp_path}/discovery" in script
    assert "--manifest /some/reconciliation_discovery.tsv" in script


def test_events_default_manifest_is_cohort_specific(tmp_path):
    script = _render(events, ["--cohort", "validation", "--staging", str(tmp_path)])
    assert "reconciliation_validation.tsv" in script


def test_events_rejected_for_excluded(tmp_path):
    with pytest.raises(SystemExit):
        _render(events, ["--cohort", "excluded", "--staging", str(tmp_path)])


def test_datalad_render_loads_git_annex(tmp_path):
    script = _render(datalad, ["--cohort", "discovery", "--staging", str(tmp_path)])
    assert "module load system git-annex" in script
    assert f"fw2bids datalad {tmp_path}/discovery" in script


# --------------------------------------------------------------------------- #
# container seam
# --------------------------------------------------------------------------- #
def test_container_swaps_run_prefix(tmp_path):
    sif = "/home/groups/russpold/singularity_images/network_fmri.sif"
    script = _render(
        curate,
        ["--cohort", "discovery", "--staging", str(tmp_path), "--container", sif],
    )
    assert f"apptainer exec {sif} fw2bids discovery --subject" in script
    assert "uv run --no-sync" not in script
    # no host-venv env exports in container mode
    assert "UV_PROJECT_ENVIRONMENT" not in script


def test_container_default_image(tmp_path):
    script = _render(
        export, ["--cohort", "discovery", "--staging", str(tmp_path), "--container"]
    )
    assert f"apptainer exec {_common.DEFAULT_CONTAINER_IMAGE} fw2bids export" in script


# --------------------------------------------------------------------------- #
# stage main() --dry-run prints without submitting
# --------------------------------------------------------------------------- #
def test_stage_main_dry_run_no_submit(tmp_path, monkeypatch, capsys):
    def _boom(*a, **k):
        raise AssertionError("submit_sbatch must not run on --dry-run")

    monkeypatch.setattr(_common, "submit_sbatch", _boom)
    rc = curate.main(["--cohort", "discovery", "--staging", str(tmp_path), "--dry-run"])
    assert rc == 0
    assert "--array=0-4%5" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# pipeline DAG: order + afterok wiring + events-skip
# --------------------------------------------------------------------------- #
class _SubmitRecorder:
    def __init__(self):
        self.calls = []
        self._n = 0

    def __call__(self, script, dependency=None):
        self._n += 1
        jid = str(1000 + self._n)
        # record the first #SBATCH -J line so we know which stage this was
        job_name = next(
            (ln.split("-J")[1].strip() for ln in script.splitlines() if ln.startswith("#SBATCH -J")),
            "?",
        )
        self.calls.append({"stage": job_name, "dependency": dependency, "jid": jid})
        return f"Submitted batch job {jid}"


def test_pipeline_chains_afterok_in_order(tmp_path, monkeypatch):
    rec = _SubmitRecorder()
    monkeypatch.setattr(pipeline, "submit_sbatch", rec)

    rc = pipeline.main(["--cohort", "discovery", "--staging", str(tmp_path)])
    assert rc == 0

    stages = [c["stage"] for c in rec.calls]
    assert stages == [
        "nf-curate-discovery",
        "nf-export-discovery",
        "nf-merge-discovery",
        "nf-trim-discovery",
        "nf-events-discovery",
        "nf-datalad-discovery",
    ]
    # first stage has no dependency; each subsequent depends on the prior job id
    assert rec.calls[0]["dependency"] is None
    for prev, cur in zip(rec.calls, rec.calls[1:]):
        assert cur["dependency"] == prev["jid"]


def test_pipeline_skips_events_for_excluded(tmp_path, monkeypatch):
    rec = _SubmitRecorder()
    monkeypatch.setattr(pipeline, "submit_sbatch", rec)

    pipeline.main(["--cohort", "excluded", "--staging", str(tmp_path)])
    stages = [c["stage"] for c in rec.calls]
    assert "nf-events-excluded" not in stages
    assert stages == [
        "nf-curate-excluded",
        "nf-export-excluded",
        "nf-merge-excluded",
        "nf-trim-excluded",
        "nf-datalad-excluded",
    ]


def test_pipeline_dry_run_does_not_submit(tmp_path, monkeypatch, capsys):
    def _boom(*a, **k):
        raise AssertionError("no submit on --dry-run")

    monkeypatch.setattr(pipeline, "submit_sbatch", _boom)
    rc = pipeline.main(["--cohort", "discovery", "--staging", str(tmp_path), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    # DAG headers show the dependency wiring
    assert "stage: curate  (dependency: (none))" in out
    assert "stage: export  (dependency: afterok:<curate-jobid>)" in out


def test_pipeline_container_propagates(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "submit_sbatch", lambda *a, **k: "Submitted batch job 42")
    pipeline.main(
        ["--cohort", "discovery", "--staging", str(tmp_path), "--container", "--dry-run"]
    )
    out = capsys.readouterr().out
    assert f"apptainer exec {_common.DEFAULT_CONTAINER_IMAGE}" in out


# --------------------------------------------------------------------------- #
# CLI dispatch: fw2bids submit <stage> / fw2bids pipeline
# --------------------------------------------------------------------------- #
def test_run_dispatch_submit(tmp_path, monkeypatch, capsys):
    from network_fmri import run

    rc = run.main(["submit", "curate", "--cohort", "discovery", "--staging", str(tmp_path), "--dry-run"])
    assert rc == 0
    assert "--array=0-4%5" in capsys.readouterr().out


def test_run_dispatch_pipeline(tmp_path, monkeypatch, capsys):
    from network_fmri import run

    rc = run.main(["pipeline", "--cohort", "discovery", "--staging", str(tmp_path), "--dry-run"])
    assert rc == 0
    assert "stage: curate" in capsys.readouterr().out


def test_submit_dispatch_rejects_unknown_stage():
    from network_fmri import submit

    with pytest.raises(SystemExit):
        submit.main(["bogus"])
