"""Check the shell and array-file boundary without submitting Slurm jobs."""

import json
import os
import subprocess
import sys

from network_fmri.glm import submit


def test_array_manifests_are_immutable_across_submissions(tmp_path):
    first = submit._write_list(tmp_path, "lev1_units.txt", ["sub-s03 flanker"])
    second = submit._write_list(tmp_path, "lev1_units.txt", ["sub-s10 nBack"])
    assert first != second
    assert first.read_text() == "sub-s03 flanker\n"
    assert second.read_text() == "sub-s10 nBack\n"


def test_lev1_shell_preserves_argument_boundaries(tmp_path, monkeypatch):
    launcher = tmp_path / "fake glm"
    launcher.write_text(
        f"#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n"
    )
    launcher.chmod(0o755)
    monkeypatch.setattr(submit, "GLM", str(launcher))
    observed = []

    def run_shell(name, body, args, log_dir, n_tasks):
        result = subprocess.run(
            ["bash", "-c", body],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "SLURM_ARRAY_TASK_ID": "1"},
        )
        observed.extend(json.loads(result.stdout))
        return "12345"

    monkeypatch.setattr(submit, "_sbatch", run_shell)
    output = tmp_path / "model outputs"
    literal = "/input with spaces/$literal;not-a-command"
    submit.lev1(
        [
            "--subjects",
            "s03",
            "--tasks",
            "flanker",
            "--results-dir",
            str(output),
            "--",
            "--bids-dir",
            literal,
        ]
    )
    assert observed == [
        "lev1",
        "--subj-id",
        "sub-s03",
        "--task-name",
        "flanker",
        "--results-dir",
        str(output),
        "--space",
        "MNI",
        "--bids-dir",
        literal,
    ]


def test_printing_glm_submission_does_not_create_output_tree(tmp_path):
    output = tmp_path / "outputs"
    submit.lev1(
        [
            "--subjects",
            "s03",
            "--tasks",
            "flanker",
            "--results-dir",
            str(output),
            "--print",
        ]
    )
    assert not output.exists()
