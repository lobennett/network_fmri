import pytest

from network_fmri import cli
from network_fmri.registry import COMMANDS, command_usage


def test_help_is_generated_from_the_authoritative_command_registry(capsys):
    assert cli.main(["--help"]) == 0
    output = capsys.readouterr().out
    assert output == command_usage()
    assert "submit fw-heudiconv" in output
    assert "glm-lev2" in output


def test_every_command_route_is_unique():
    routes = [command.route for command in COMMANDS]
    assert len(routes) == len(set(routes))


@pytest.mark.parametrize("command", COMMANDS, ids=lambda item: item.display_name)
def test_every_registered_command_target_loads(command):
    assert callable(command.load())


def test_unknown_command_fails_with_generated_usage(capsys):
    assert cli.main(["not-a-command"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("usage: network_fmri")
