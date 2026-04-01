"""Unit tests for devbox CLI module."""

import pytest
from unittest.mock import MagicMock, patch, call
from click.testing import CliRunner

from devbox.cli import cli, status, terminate, launch, delete_project, main
from devbox.utils import AWSClientError


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "DevBox - AWS EC2 Development Environment Manager" in result.output
    assert "launch" in result.output
    assert "status" in result.output
    assert "terminate" in result.output


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    # TODO: fix this up; version could fail due to package not being installed
    assert result.exit_code in [0, 1]


@patch("devbox.cli.run_status_command")
@patch("devbox.cli.ConsoleOutput")
def test_cli_context_initialization_success(mock_console_class, mock_run_status_command):
    mock_console = MagicMock()
    mock_console_class.return_value = mock_console

    runner = CliRunner()
    result = runner.invoke(cli, ["status"])

    assert result.exit_code == 0
    mock_console_class.assert_called_once()
    mock_run_status_command.assert_called_once_with(
        project=None,
        param_prefix="/devbox",
        console=mock_console,
    )


@patch("devbox.cli.run_status_command")
@patch("devbox.cli.ConsoleOutput")
def test_cli_context_initialization_failure(mock_console_class, mock_run_status_command):
    mock_console = MagicMock()
    mock_console_class.return_value = mock_console
    mock_run_status_command.side_effect = Exception("Remote status failed")

    runner = CliRunner()
    result = runner.invoke(cli, ["status"])

    assert result.exit_code == 1
    mock_console.print_error.assert_called_once()


class TestStatusCommand:
    def setup_method(self):
        self.runner = CliRunner()

    def test_status_help(self):
        self.runner = CliRunner()
        result = self.runner.invoke(status, ["--help"])

        assert result.exit_code == 0
        assert "Show status of DevBox resources" in result.output
        assert "PROJECT" in result.output

    @patch("devbox.cli.run_status_command")
    @patch("devbox.cli.ConsoleOutput")
    def test_status_no_project_filter(self, mock_console_class, mock_run_status_command):
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        self.runner = CliRunner()
        result = self.runner.invoke(cli, ["status"])

        assert result.exit_code == 0
        mock_run_status_command.assert_called_once_with(
            project=None,
            param_prefix="/devbox",
            console=mock_console,
        )

    @patch("devbox.cli.run_status_command")
    @patch("devbox.cli.ConsoleOutput")
    def test_status_with_project_filter(self, mock_console_class, mock_run_status_command):
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        self.runner = CliRunner()
        result = self.runner.invoke(cli, ["status", "my-project"])

        assert result.exit_code == 0
        mock_run_status_command.assert_called_once_with(
            project="my-project",
            param_prefix="/devbox",
            console=mock_console,
        )

    @patch("devbox.cli.run_status_command")
    @patch("devbox.cli.ConsoleOutput")
    def test_status_with_param_prefix_option(
        self, mock_console_class, mock_run_status_command
    ):
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(
            cli, ["status", "--param-prefix", "/custom/devbox"]
        )

        assert result.exit_code == 0
        mock_run_status_command.assert_called_once_with(
            project=None,
            param_prefix="/custom/devbox",
            console=mock_console,
        )

    @patch("devbox.cli.run_status_command")
    @patch("devbox.cli.ConsoleOutput")
    def test_status_manager_error(self, mock_console_class, mock_run_status_command):
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console
        mock_run_status_command.side_effect = AWSClientError("AWS error")

        self.runner = CliRunner()
        result = self.runner.invoke(cli, ["status"])

        assert result.exit_code == 1
        mock_console.print_error.assert_called_once()
        error_call = mock_console.print_error.call_args[0][0]
        assert "Failed to retrieve status" in error_call

    @patch("devbox.cli.run_status_command")
    @patch("devbox.cli.ConsoleOutput")
    def test_status_general_exception(self, mock_console_class, mock_run_status_command):
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console
        mock_run_status_command.side_effect = Exception("General error")

        self.runner = CliRunner()
        result = self.runner.invoke(cli, ["status"])

        assert result.exit_code == 1
        mock_console.print_error.assert_called_once()


class TestTerminateCommand:
    def setup_method(self):
        self.runner = CliRunner()

    def test_terminate_help(self):
        self.runner = CliRunner()
        result = self.runner.invoke(terminate, ["--help"])

        assert result.exit_code == 0
        assert "Terminate a DevBox instance by its ID" in result.output
        assert "INSTANCE_ID" in result.output

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_terminate_success(self, mock_console_class, mock_manager_class):
        # TODO: redo this test so that it is meaningful? too much mock here
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        mock_manager.terminate_instance.return_value = {
            "instance_id": "i-1234567890abcdef0",
            "project": "test-project",
        }

        self.runner = CliRunner()
        result = self.runner.invoke(cli, ["terminate", "i-1234567890abcdef0"])

        assert result.exit_code == 0
        mock_manager.terminate_instance.assert_called_once_with(
            "i-1234567890abcdef0", mock_console
        )
        mock_console.print_success.assert_called_once_with(
            "Terminating instance i-1234567890abcdef0 (project: test-project)."
        )

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_terminate_failure(self, mock_console_class, mock_manager_class):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        # TODO: maybe don't mock this?
        mock_manager.terminate_instance.side_effect = Exception("Instance not found")

        result = self.runner.invoke(cli, ["terminate", "i-nonexistent"])

        assert result.exit_code == 1
        mock_manager.terminate_instance.assert_called_once_with("i-nonexistent", mock_console)
        mock_console.print_error.assert_called_once_with(
            "Failed to terminate instance: Instance not found"
        )

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_terminate_with_param_prefix_option(
        self, mock_console_class, mock_manager_class
    ):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager
        mock_manager.terminate_instance.return_value = {
            "instance_id": "i-1234567890abcdef0",
            "project": "test-project",
        }

        result = self.runner.invoke(
            cli, ["terminate", "i-1234567890abcdef0", "--param-prefix", "/custom"]
        )

        assert result.exit_code == 0
        mock_manager_class.assert_called_once_with(prefix="custom")

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_terminate_exception(self, mock_console_class, mock_manager_class):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        mock_manager.terminate_instance.side_effect = Exception("Unexpected error")

        result = self.runner.invoke(cli, ["terminate", "i-error"])

        assert result.exit_code == 1
        mock_console.print_error.assert_called_once()
        error_call = mock_console.print_error.call_args[0][0]
        assert "Failed to terminate instance" in error_call

    def test_terminate_missing_instance_id(self):
        result = self.runner.invoke(cli, ["terminate"])

        assert result.exit_code == 2  # Click argument error
        assert "Missing argument" in result.output


class TestLaunchCommand:
    def setup_method(self):
        self.runner = CliRunner()

    def test_launch_help(self):
        result = self.runner.invoke(launch, ["--help"])

        assert result.exit_code == 0
        assert "Launch a new DevBox instance" in result.output
        assert "PROJECT" in result.output  # Now a positional argument
        assert "--instance-type" in result.output
        assert "--key-pair" in result.output
        assert "--no-assign-dns" in result.output
        assert "--dns-subdomain" in result.output

    @patch("devbox.launch.launch_programmatic")
    @patch("devbox.cli.ConsoleOutput")
    def test_launch_success(self, mock_console_class, mock_launch):
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(
            cli,
            [
                "launch",
                "test-project",  # Now positional
                "--instance-type",
                "t3.medium",
                "--key-pair",
                "my-key",
            ],
        )

        assert result.exit_code == 0
        mock_launch.assert_called_once_with(
            project="test-project",
            instance_type="t3.medium",
            key_pair="my-key",
            volume_size=0,  # default
            base_ami=None,
            param_prefix="/devbox",  # default
            userdata_file=None,  # default
            assign_dns=True,
            dns_subdomain=None,
        )

    @patch("devbox.launch.launch_programmatic")
    @patch("devbox.cli.ConsoleOutput")
    def test_launch_with_all_options(self, mock_console_class, mock_launch):
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(
            cli,
            [
                "launch",
                "full-project",  # Now positional
                "--instance-type",
                "m5.large",
                "--key-pair",
                "full-key",
                "--volume-size",
                "200",
                "--base-ami",
                "ami-12345678",
                "--param-prefix",
                "/custom",
            ],
        )

        assert result.exit_code == 0
        mock_launch.assert_called_once_with(
            project="full-project",
            instance_type="m5.large",
            key_pair="full-key",
            volume_size=200,
            base_ami="ami-12345678",
            param_prefix="/custom",
            userdata_file=None,  # default
            assign_dns=True,
            dns_subdomain=None,
        )

    @patch("devbox.launch.launch_programmatic")
    @patch("devbox.cli.ConsoleOutput")
    def test_launch_exception(self, mock_console_class, mock_launch):
        """Test launch command with exception."""
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console
        mock_launch.side_effect = Exception("Launch failed")

        result = self.runner.invoke(
            cli,
            [
                "launch",
                "error-project",  # Now positional
                "--instance-type",
                "t3.medium",
                "--key-pair",
                "error-key",
            ],
        )

        assert result.exit_code == 1
        mock_console.print_error.assert_called_once()
        error_call = mock_console.print_error.call_args[0][0]
        assert "Failed to launch instance" in error_call

    def test_launch_missing_required_options(self):
        """Test launch command with missing required arguments."""
        # Missing project (now required positional argument)
        result = self.runner.invoke(cli, ["launch"])
        assert result.exit_code in [2, 3]  # Allow both Click and our error codes
        assert "Missing argument" in result.output or "Error" in result.output

    @patch("devbox.launch.launch_programmatic")
    @patch("devbox.cli.ConsoleOutput")
    def test_launch_with_optional_parameters_only(
        self, mock_console_class, mock_launch
    ):
        """Test launch command with only required project argument (instance-type and key-pair now optional)."""
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(cli, ["launch", "test-project"])

        # Should succeed at CLI parsing level since instance-type and key-pair are optional
        assert result.exit_code == 0
        mock_launch.assert_called_once_with(
            project="test-project",
            instance_type=None,  # Should be None when not specified
            key_pair=None,  # Should be None when not specified
            volume_size=0,
            base_ami=None,
            param_prefix="/devbox",
            userdata_file=None,  # default
            assign_dns=True,
            dns_subdomain=None,
        )

    @patch("devbox.launch.launch_programmatic")
    @patch("devbox.cli.ConsoleOutput")
    def test_launch_with_instance_type_only(self, mock_console_class, mock_launch):
        """Test launch command with only instance-type specified."""
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(
            cli, ["launch", "test-project", "--instance-type", "t3.large"]
        )

        assert result.exit_code == 0
        mock_launch.assert_called_once_with(
            project="test-project",
            instance_type="t3.large",
            key_pair=None,  # Should be None when not specified
            volume_size=0,
            base_ami=None,
            param_prefix="/devbox",
            userdata_file=None,  # default
            assign_dns=True,
            dns_subdomain=None,
        )

    @patch("devbox.launch.launch_programmatic")
    @patch("devbox.cli.ConsoleOutput")
    def test_launch_with_key_pair_only(self, mock_console_class, mock_launch):
        """Test launch command with only key-pair specified."""
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(
            cli, ["launch", "test-project", "--key-pair", "my-keypair"]
        )

        assert result.exit_code == 0
        mock_launch.assert_called_once_with(
            project="test-project",
            instance_type=None,  # Should be None when not specified
            key_pair="my-keypair",
            volume_size=0,
            base_ami=None,
            param_prefix="/devbox",
            userdata_file=None,  # default
            assign_dns=True,
            dns_subdomain=None,
        )

    @patch("devbox.launch.launch_programmatic")
    @patch("devbox.cli.ConsoleOutput")
    def test_launch_with_dns_flags(self, mock_console_class, mock_launch):
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(
            cli,
            [
                "launch",
                "test-project",
                "--no-assign-dns",
                "--dns-subdomain",
                "my-custom-label",
            ],
        )

        assert result.exit_code == 0
        mock_launch.assert_called_once_with(
            project="test-project",
            instance_type=None,
            key_pair=None,
            volume_size=0,
            base_ami=None,
            param_prefix="/devbox",
            userdata_file=None,
            assign_dns=False,
            dns_subdomain="my-custom-label",
        )

    @pytest.mark.parametrize(
        "volume_size,expected",
        [
            ("50", 50),
            ("100", 100),
            ("500", 500),
            ("0", 0),
        ],
    )
    @patch("devbox.launch.launch_programmatic")
    @patch("devbox.cli.ConsoleOutput")
    def test_launch_volume_size_parsing(
        self, mock_console_class, mock_launch, volume_size, expected
    ):
        """Test launch command volume size parsing."""
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(
            cli,
            [
                "launch",
                "test-project",  # Now positional
                "--instance-type",
                "t3.medium",
                "--key-pair",
                "my-key",
                "--volume-size",
                volume_size,
            ],
        )

        assert result.exit_code == 0
        call_args = mock_launch.call_args
        assert call_args[1]["volume_size"] == expected

    @patch("devbox.launch.launch_programmatic")
    @patch("devbox.cli.ConsoleOutput")
    def test_launch_with_userdata_file(self, mock_console_class, mock_launch, tmp_path):
        """Test launch command with userdata file option."""
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        userdata_file = tmp_path / "userdata.sh"
        userdata_file.write_text("#!/bin/bash\necho 'Hello World'")

        result = self.runner.invoke(
            cli,
            [
                "launch",
                "test-project",
                "--instance-type",
                "t3.medium",
                "--key-pair",
                "my-key",
                "--userdata-file",
                str(userdata_file),
            ],
        )

        assert result.exit_code == 0
        call_args = mock_launch.call_args
        assert "userdata_file" in call_args[1]
        assert call_args[1]["userdata_file"] == str(userdata_file)

    def test_launch_userdata_file_not_found(self):
        """Test launch command with non-existent userdata file."""
        result = self.runner.invoke(
            cli,
            [
                "launch",
                "test-project",
                "--instance-type",
                "t3.medium",
                "--key-pair",
                "my-key",
                "--userdata-file",
                "/nonexistent/userdata.sh",
            ],
        )

        assert result.exit_code != 0
        assert "does not exist" in result.output or "Invalid value" in result.output


class TestDeleteProjectCommand:
    def setup_method(self):
        self.runner = CliRunner()

    def test_delete_project_help(self):
        result = self.runner.invoke(delete_project, ["--help"])

        assert result.exit_code == 0
        assert "Delete a DevBox project" in result.output
        assert "PROJECT" in result.output
        assert "--force" in result.output
        assert "--param-prefix" in result.output

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_delete_project_force_success(self, mock_console_class, mock_manager_class):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        mock_manager.get_project_item.return_value = {"project": "demo", "AMI": "ami-12345678"}
        mock_manager.project_in_use.return_value = (False, "")
        mock_manager.delete_ami_and_snapshots.return_value = {
            "ami_id": "ami-12345678",
            "snapshot_count": 2,
        }

        result = self.runner.invoke(cli, ["delete-project", "demo", "--force"])

        assert result.exit_code == 0
        mock_manager.delete_project_entry.assert_called_once_with("demo")
        mock_manager.delete_ami_and_snapshots.assert_called_once_with("ami-12345678")
        mock_manager.assert_has_calls(
            [
                call.delete_ami_and_snapshots("ami-12345678"),
                call.delete_project_entry("demo"),
            ],
            any_order=False,
        )

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_delete_project_in_use(self, mock_console_class, mock_manager_class):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        mock_manager.get_project_item.return_value = {"project": "demo", "AMI": "ami-12345678"}
        mock_manager.project_in_use.return_value = (True, "EC2 instances in states: running.")

        result = self.runner.invoke(cli, ["delete-project", "demo"])

        assert result.exit_code == 1
        mock_manager.delete_project_entry.assert_not_called()
        mock_manager.delete_ami_and_snapshots.assert_not_called()
        mock_console.print_error.assert_called_once()

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_delete_project_not_found(self, mock_console_class, mock_manager_class):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        mock_manager.get_project_item.return_value = None

        result = self.runner.invoke(cli, ["delete-project", "missing"])

        assert result.exit_code == 1
        mock_manager.project_in_use.assert_not_called()
        mock_manager.delete_project_entry.assert_not_called()
        mock_console.print_error.assert_called_once()

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_delete_project_cancel_first_prompt(self, mock_console_class, mock_manager_class):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        mock_manager.get_project_item.return_value = {"project": "demo", "AMI": "ami-12345678"}
        mock_manager.project_in_use.return_value = (False, "")

        result = self.runner.invoke(cli, ["delete-project", "demo"], input="n\n")

        assert result.exit_code == 0
        mock_manager.delete_project_entry.assert_not_called()
        mock_manager.delete_ami_and_snapshots.assert_not_called()
        mock_console.print_warning.assert_called_once()

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_delete_project_cancel_ami_cleanup(self, mock_console_class, mock_manager_class):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        mock_manager.get_project_item.return_value = {"project": "demo", "AMI": "ami-12345678"}
        mock_manager.project_in_use.return_value = (False, "")

        result = self.runner.invoke(cli, ["delete-project", "demo"], input="y\nn\n")

        assert result.exit_code == 0
        mock_manager.delete_project_entry.assert_called_once_with("demo")
        mock_manager.delete_ami_and_snapshots.assert_not_called()
        mock_console.print_warning.assert_called_once()

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_delete_project_no_ami_recorded_still_deletes_entry(
        self, mock_console_class, mock_manager_class
    ):
        """Project records can exist without AMI metadata (legacy/partial table entries)."""
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        mock_manager.get_project_item.return_value = {"project": "demo"}
        mock_manager.project_in_use.return_value = (False, "")

        result = self.runner.invoke(cli, ["delete-project", "demo", "--force"])

        assert result.exit_code == 0
        mock_manager.delete_ami_and_snapshots.assert_not_called()
        mock_manager.delete_project_entry.assert_called_once_with("demo")
        mock_console.print_warning.assert_called_once()

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_delete_project_ami_cleanup_failure_does_not_delete_entry(
        self, mock_console_class, mock_manager_class
    ):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        mock_manager.get_project_item.return_value = {"project": "demo", "AMI": "ami-12345678"}
        mock_manager.project_in_use.return_value = (False, "")
        mock_manager.delete_ami_and_snapshots.side_effect = AWSClientError("AMI cleanup failed")

        result = self.runner.invoke(cli, ["delete-project", "demo", "--force"])

        assert result.exit_code == 1
        mock_manager.delete_ami_and_snapshots.assert_called_once_with("ami-12345678")
        mock_manager.delete_project_entry.assert_not_called()
        mock_console.print_error.assert_called_once()

    @pytest.mark.parametrize(
        "scenario,expected_error,expect_project_in_use_called,expect_delete_entry_called",
        [
            ("get_project_item", "lookup failed", False, False),
            ("project_in_use", "usage check failed", True, False),
            ("delete_project_entry", "delete failed", True, True),
        ],
    )
    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_delete_project_exceptions(
        self,
        mock_console_class,
        mock_manager_class,
        scenario,
        expected_error,
        expect_project_in_use_called,
        expect_delete_entry_called,
    ):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        if scenario == "get_project_item":
            mock_manager.get_project_item.side_effect = Exception("lookup failed")
        else:
            mock_manager.get_project_item.return_value = {"project": "demo"}
            if scenario == "project_in_use":
                mock_manager.project_in_use.side_effect = Exception("usage check failed")
            else:
                mock_manager.project_in_use.return_value = (False, "")
                mock_manager.delete_project_entry.side_effect = Exception("delete failed")

        result = self.runner.invoke(cli, ["delete-project", "demo", "--force"])

        assert result.exit_code == 1
        mock_manager.delete_ami_and_snapshots.assert_not_called()
        if expect_project_in_use_called:
            mock_manager.project_in_use.assert_called_once_with("demo", {"project": "demo"})
        else:
            mock_manager.project_in_use.assert_not_called()
        if expect_delete_entry_called:
            mock_manager.delete_project_entry.assert_called_once_with("demo")
        else:
            mock_manager.delete_project_entry.assert_not_called()
        mock_console.print_error.assert_called_once_with(
            f"Failed to delete project: {expected_error}"
        )

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_delete_project_with_param_prefix_option(
        self, mock_console_class, mock_manager_class
    ):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        mock_manager.get_project_item.return_value = {"project": "demo"}
        mock_manager.project_in_use.return_value = (False, "")

        result = self.runner.invoke(
            cli,
            ["delete-project", "demo", "--force", "--param-prefix", "/custom/devbox"],
        )

        assert result.exit_code == 0
        mock_manager_class.assert_called_once_with(prefix="custom/devbox")
        mock_manager.delete_ami_and_snapshots.assert_not_called()
        mock_manager.delete_project_entry.assert_called_once_with("demo")


@patch("devbox.cli.cli")
def test_main_calls_cli(mock_cli):
    """Test main function calls CLI."""
    main()
    mock_cli.assert_called_once_with(obj={})


class TestIntegrationScenarios:
    """Test integration scenarios."""

    def setup_method(self):
        """Set up test runner."""
        self.runner = CliRunner()

    @patch("devbox.cli.run_status_command")
    @patch("devbox.cli.ConsoleOutput")
    def test_status_empty_results(self, mock_console_class, mock_run_status_command):
        """Test status command with empty results."""
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(cli, ["status"])

        assert result.exit_code == 0
        mock_run_status_command.assert_called_once_with(
            project=None,
            param_prefix="/devbox",
            console=mock_console,
        )

    @patch("devbox.cli.run_status_command")
    @patch("devbox.cli.ConsoleOutput")
    def test_status_with_realistic_data(self, mock_console_class, mock_run_status_command):
        """Test status command with realistic data."""
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(cli, ["status", "my-devbox"])

        assert result.exit_code == 0
        mock_run_status_command.assert_called_once_with(
            project="my-devbox",
            param_prefix="/devbox",
            console=mock_console,
        )

    @patch("devbox.launch.launch_programmatic")
    @patch("devbox.cli.ConsoleOutput")
    def test_launch_realistic_scenario(self, mock_console_class, mock_launch):
        """Test launch command with realistic parameters."""
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(
            cli,
            [
                "launch",
                "my-development-box",  # Now positional
                "--instance-type",
                "t3.large",
                "--key-pair",
                "my-ec2-keypair",
                "--volume-size",
                "150",
                "--base-ami",
                "ami-0abcdef1234567890",
                "--param-prefix",
                "/mycompany/devbox",
            ],
        )

        assert result.exit_code == 0
        mock_launch.assert_called_once_with(
            project="my-development-box",
            instance_type="t3.large",
            key_pair="my-ec2-keypair",
            volume_size=150,
            base_ami="ami-0abcdef1234567890",
            param_prefix="/mycompany/devbox",
            userdata_file=None,  # default
            assign_dns=True,
            dns_subdomain=None,
        )


class TestErrorHandlingPatterns:
    """Test error handling patterns across commands."""

    def setup_method(self):
        """Set up test runner."""
        self.runner = CliRunner()

    @patch("devbox.cli.run_status_command")
    @patch("devbox.cli.ConsoleOutput")
    def test_context_initialization_error_handling(
        self, mock_console_class, mock_run_status_command
    ):
        """Test error handling during context initialization."""
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console
        mock_run_status_command.side_effect = AWSClientError("Remote status failure")

        result = self.runner.invoke(cli, ["status"])

        assert result.exit_code == 1
        mock_console.print_error.assert_called_once()
        error_call = mock_console.print_error.call_args[0][0]
        assert "Remote status failure" in error_call

    @pytest.mark.parametrize(
        "command,args",
        [
            (["status"], None),
            (["terminate", "i-test"], None),
        ],
    )
    @patch("devbox.cli.run_status_command")
    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_consistent_error_exit_codes(
        self, mock_console_class, mock_manager_class, mock_run_status_command, command, args
    ):
        """Test consistent error exit codes across commands."""
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        if "status" in command:
            mock_run_status_command.side_effect = Exception("Test error")
        elif "terminate" in command:
            mock_manager.terminate_instance.side_effect = Exception("Test error")

        result = self.runner.invoke(cli, command)

        assert result.exit_code == 1
        mock_console.print_error.assert_called()

    @patch("devbox.launch.launch_programmatic")
    @patch("devbox.cli.ConsoleOutput")
    def test_launch_error_exit_code(self, mock_console_class, mock_launch):
        """Test launch command error exit code."""
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console
        mock_launch.side_effect = Exception("Launch error")

        result = self.runner.invoke(
            cli,
            [
                "launch",
                "test",  # Now positional
                "--instance-type",
                "t3.medium",
                "--key-pair",
                "test-key",
            ],
        )

        assert result.exit_code == 1
        mock_console.print_error.assert_called()


class TestCommandChaining:
    """Test command chaining and isolation."""

    def setup_method(self):
        """Set up test runner."""
        self.runner = CliRunner()

    @patch("devbox.cli.run_status_command")
    @patch("devbox.cli.ConsoleOutput")
    def test_multiple_status_calls(self, mock_console_class, mock_run_status_command):
        """Test multiple status calls are independent."""
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        # First call
        result1 = self.runner.invoke(cli, ["status"])
        assert result1.exit_code == 0

        # Second call with different project
        result2 = self.runner.invoke(cli, ["status", "different-project"])
        assert result2.exit_code == 0

        # Verify both calls were made with correct parameters
        expected_calls = [
            call(project=None, param_prefix="/devbox", console=mock_console),
            call(project="different-project", param_prefix="/devbox", console=mock_console),
        ]
        mock_run_status_command.assert_has_calls(expected_calls)

    @patch("devbox.cli.run_status_command")
    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_command_state_isolation(
        self,
        mock_console_class,
        mock_manager_class,
        mock_run_status_command,
    ):
        """Test commands don't affect each other's state."""
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager

        mock_manager.terminate_instance.return_value = {
            "instance_id": "i-test",
            "project": "test-project",
        }

        # Run status then terminate
        result1 = self.runner.invoke(cli, ["status"])
        result2 = self.runner.invoke(cli, ["terminate", "i-test"])

        assert result1.exit_code == 0
        assert result2.exit_code == 0

        # Verify each command called its respective methods
        mock_run_status_command.assert_called_once_with(
            project=None,
            param_prefix="/devbox",
            console=mock_console,
        )
        mock_manager.terminate_instance.assert_called_once_with("i-test", mock_console)


class TestParamPrefixEnvironmentOverrides:
    def setup_method(self):
        self.runner = CliRunner()

    @patch("devbox.cli.run_status_command")
    @patch("devbox.cli.ConsoleOutput")
    def test_status_uses_param_prefix_from_env(
        self, mock_console_class, mock_run_status_command
    ):
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(
            cli, ["status"], env={"DEVBOX_PARAM_PREFIX": "/env/devbox"}
        )

        assert result.exit_code == 0
        mock_run_status_command.assert_called_once_with(
            project=None,
            param_prefix="/env/devbox",
            console=mock_console,
        )

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_terminate_uses_param_prefix_from_env(
        self, mock_console_class, mock_manager_class
    ):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager
        mock_manager.terminate_instance.return_value = {
            "instance_id": "i-1234567890abcdef0",
            "project": "test-project",
        }

        result = self.runner.invoke(
            cli,
            ["terminate", "i-1234567890abcdef0"],
            env={"DEVBOX_PARAM_PREFIX": "/env/devbox"},
        )

        assert result.exit_code == 0
        mock_manager_class.assert_called_once_with(prefix="env/devbox")

    @patch("devbox.cli.DevBoxManager")
    @patch("devbox.cli.ConsoleOutput")
    def test_delete_project_uses_param_prefix_from_env(
        self, mock_console_class, mock_manager_class
    ):
        mock_console = MagicMock()
        mock_manager = MagicMock()
        mock_console_class.return_value = mock_console
        mock_manager_class.return_value = mock_manager
        mock_manager.get_project_item.return_value = {"project": "test-project"}
        mock_manager.project_in_use.return_value = (False, "")

        result = self.runner.invoke(
            cli,
            ["delete-project", "test-project", "--force"],
            env={"DEVBOX_PARAM_PREFIX": "/env/devbox"},
        )

        assert result.exit_code == 0
        mock_manager_class.assert_called_once_with(prefix="env/devbox")
        mock_manager.delete_project_entry.assert_called_once_with("test-project")

    @patch("devbox.launch.launch_programmatic")
    @patch("devbox.cli.ConsoleOutput")
    def test_launch_uses_param_prefix_from_env(self, mock_console_class, mock_launch):
        mock_console = MagicMock()
        mock_console_class.return_value = mock_console

        result = self.runner.invoke(
            cli, ["launch", "test-project"], env={"DEVBOX_PARAM_PREFIX": "/env/devbox"}
        )

        assert result.exit_code == 0
        mock_launch.assert_called_once_with(
            project="test-project",
            instance_type=None,
            key_pair=None,
            volume_size=0,
            base_ami=None,
            param_prefix="/env/devbox",
            userdata_file=None,
            assign_dns=True,
            dns_subdomain=None,
        )
