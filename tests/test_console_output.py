"""Unit tests for devbox console_output module."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

from devbox.console_output import ConsoleOutput


class TestConsoleOutput:
    """Test ConsoleOutput initialization."""

    def test_init_creates_console(self):
        """Test that initialization creates a Rich console."""
        console_output = ConsoleOutput()
        assert hasattr(console_output, "console")
        assert console_output.console is not None

    @patch("devbox.console_output.Console")
    def test_init_uses_rich_console(self, mock_console_class):
        """Test that initialization uses Rich Console class."""
        mock_console_instance = MagicMock()
        mock_console_class.return_value = mock_console_instance

        console_output = ConsoleOutput()

        mock_console_class.assert_called_once()
        assert console_output.console == mock_console_instance


class TestPrintInstances:
    """Test print_instances method."""

    def setup_method(self):
        """Set up test instances."""
        self.console_output = ConsoleOutput()
        self.console_output.console = MagicMock()

    def test_print_instances_empty_list(self):
        """Test printing empty instances list."""
        self.console_output.print_instances([])

        # Should print no instances found message
        self.console_output.console.print.assert_called_once_with(
            "[yellow]No instances found.[/yellow]"
        )

    def test_print_instances_single_instance(self):
        """Test printing single instance."""
        launch_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        instances = [
            {
                "InstanceId": "i-1234567890abcdef0",
                "Project": "test-project",
                "PublicIpAddress": "192.168.1.1",
                "State": "running",
                "InstanceType": "t3.medium",
                "LaunchTime": launch_time,
            }
        ]

        self.console_output.print_instances(instances)

        # Should create and print table
        assert self.console_output.console.print.call_count == 2
        calls = self.console_output.console.print.call_args_list

        # First call should be the header
        assert "[bold underline]EC2 Instances (1)[/bold underline]" in str(calls[0])

    def test_print_instances_multiple_instances(self):
        """Test printing multiple instances."""
        instances = [
            {
                "InstanceId": "i-1234567890abcdef0",
                "Project": "project-1",
                "PublicIpAddress": "192.168.1.1",
                "State": "running",
                "InstanceType": "t3.medium",
                "LaunchTime": datetime.now(timezone.utc),
            },
            {
                "InstanceId": "i-0987654321fedcba0",
                "Project": "project-2",
                "PublicIpAddress": "192.168.1.2",
                "State": "stopped",
                "InstanceType": "m5.large",
                "LaunchTime": datetime.now(timezone.utc),
            },
        ]

        self.console_output.print_instances(instances)

        # Should print header with count
        calls = self.console_output.console.print.call_args_list
        assert "[bold underline]EC2 Instances (2)[/bold underline]" in str(calls[0])

    def test_print_instances_missing_optional_fields(self):
        """Test printing instances with missing optional fields."""
        instances = [
            {
                "InstanceId": "i-1234567890abcdef0",
                # Missing optional fields like Project, PublicIpAddress, etc.
            }
        ]

        # Should not raise an exception
        self.console_output.print_instances(instances)
        assert self.console_output.console.print.call_count == 2

    def test_print_instances_no_launch_time(self):
        """Test printing instances without launch time."""
        instances = [
            {
                "InstanceId": "i-1234567890abcdef0",
                "Project": "test-project",
                "State": "running",
                "InstanceType": "t3.medium",
                # No LaunchTime
            }
        ]

        self.console_output.print_instances(instances)
        # Should handle missing launch time gracefully
        assert self.console_output.console.print.call_count == 2


class TestPrintVolumes:
    """Test print_volumes method."""

    def setup_method(self):
        """Set up test volumes."""
        self.console_output = ConsoleOutput()
        self.console_output.console = MagicMock()

    def test_print_volumes_empty_list(self):
        """Test printing empty volumes list."""
        self.console_output.print_volumes([])

        self.console_output.console.print.assert_called_once_with(
            "[yellow]No volumes found.[/yellow]"
        )

    def test_print_volumes_single_volume(self):
        """Test printing single volume."""
        volumes = [
            {
                "VolumeId": "vol-1234567890abcdef0",
                "Project": "test-project",
                "State": "available",
                "Size": 100,
                "AvailabilityZone": "us-east-1a",
                "IsOrphaned": True,
            }
        ]

        self.console_output.print_volumes(volumes)

        assert self.console_output.console.print.call_count == 2
        calls = self.console_output.console.print.call_args_list
        assert "[bold underline]EBS Volumes (1)[/bold underline]" in str(calls[0])

    def test_print_volumes_orphaned_only_flag(self):
        """Test printing volumes with orphaned only flag."""
        volumes = [
            {
                "VolumeId": "vol-1234567890abcdef0",
                "Project": "test-project",
                "State": "available",
                "Size": 100,
                "AvailabilityZone": "us-east-1a",
                "IsOrphaned": True,
            }
        ]

        self.console_output.print_volumes(volumes, show_orphaned=True)

        calls = self.console_output.console.print.call_args_list
        assert "EBS Volumes (Orphaned Only)" in str(calls[0])

    @pytest.mark.parametrize(
        "state,expected_style",
        [
            ("available", "red"),
            ("in-use", "green"),
            ("creating", None),
        ],
    )
    def test_print_volumes_state_styling(self, state, expected_style):
        """Test volume state styling."""
        volumes = [
            {
                "VolumeId": "vol-1234567890abcdef0",
                "Project": "test-project",
                "State": state,
                "Size": 100,
                "AvailabilityZone": "us-east-1a",
                "IsOrphaned": False,
            }
        ]

        with patch("devbox.console_output.Table") as mock_table_class:
            mock_table = MagicMock()
            mock_table_class.return_value = mock_table

            self.console_output.print_volumes(volumes)

            # Check if add_row was called with correct style
            mock_table.add_row.assert_called_once()
            call_args = mock_table.add_row.call_args
            if expected_style:
                assert call_args[1]["style"] == expected_style


class TestPrintSnapshots:
    """Test print_snapshots method."""

    def setup_method(self):
        """Set up test snapshots."""
        self.console_output = ConsoleOutput()
        self.console_output.console = MagicMock()

    def test_print_snapshots_empty_list(self):
        """Test printing empty snapshots list."""
        self.console_output.print_snapshots([])

        self.console_output.console.print.assert_called_once_with(
            "[yellow]No snapshots found.[/yellow]"
        )

    def test_print_snapshots_single_snapshot(self):
        """Test printing single snapshot."""
        start_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        snapshots = [
            {
                "SnapshotId": "snap-1234567890abcdef0",
                "Project": "test-project",
                "VolumeSize": 100,
                "Progress": "100%",
                "StartTime": start_time,
                "IsOrphaned": False,
            }
        ]

        self.console_output.print_snapshots(snapshots)

        assert self.console_output.console.print.call_count == 2
        calls = self.console_output.console.print.call_args_list
        assert "[bold underline]EBS Snapshots (1)[/bold underline]" in str(calls[0])

    def test_print_snapshots_orphaned_only_flag(self):
        """Test printing snapshots with orphaned only flag."""
        snapshots = [
            {
                "SnapshotId": "snap-1234567890abcdef0",
                "Project": "test-project",
                "VolumeSize": 100,
                "Progress": "100%",
                "StartTime": datetime.now(timezone.utc),
                "IsOrphaned": True,
            }
        ]

        self.console_output.print_snapshots(snapshots, show_orphaned=True)

        calls = self.console_output.console.print.call_args_list
        assert "EBS Snapshots (Orphaned Only)" in str(calls[0])

    def test_print_snapshots_no_start_time(self):
        """Test printing snapshots without start time."""
        snapshots = [
            {
                "SnapshotId": "snap-1234567890abcdef0",
                "Project": "test-project",
                "VolumeSize": 100,
                "Progress": "100%",
                "IsOrphaned": False,
                # No StartTime
            }
        ]

        self.console_output.print_snapshots(snapshots)
        # Should handle missing start time gracefully
        assert self.console_output.console.print.call_count == 2

    def test_print_snapshots_orphaned_styling(self):
        """Test orphaned snapshot styling."""
        snapshots = [
            {
                "SnapshotId": "snap-1234567890abcdef0",
                "Project": "test-project",
                "VolumeSize": 100,
                "Progress": "100%",
                "StartTime": datetime.now(timezone.utc),
                "IsOrphaned": True,
            }
        ]

        with patch("devbox.console_output.Table") as mock_table_class:
            mock_table = MagicMock()
            mock_table_class.return_value = mock_table

            self.console_output.print_snapshots(snapshots, show_orphaned=True)

            # Check if add_row was called with red style for orphaned
            call_args = mock_table.add_row.call_args
            assert call_args[1]["style"] == "red"


class TestMessageMethods:
    """Test message printing methods."""

    def setup_method(self):
        """Set up test console."""
        self.console_output = ConsoleOutput()
        self.console_output.console = MagicMock()

    def test_print_error(self):
        """Test error message printing."""
        message = "Something went wrong"
        self.console_output.print_error(message)

        self.console_output.console.print.assert_called_once_with(
            f"[red]Error: {message}[/red]"
        )

    def test_print_success(self):
        """Test success message printing."""
        message = "Operation completed successfully"
        self.console_output.print_success(message)

        self.console_output.console.print.assert_called_once_with(
            f"[green]{message}[/green]"
        )

    def test_print_warning(self):
        """Test warning message printing."""
        message = "This is a warning"
        self.console_output.print_warning(message)

        self.console_output.console.print.assert_called_once_with(
            f"[yellow]Warning: {message}[/yellow]"
        )

    @pytest.mark.parametrize(
        "method_name,expected_color,has_prefix",
        [
            ("print_error", "red", True),
            ("print_success", "green", False),
            ("print_warning", "yellow", True),
        ],
    )
    def test_message_methods_formatting(self, method_name, expected_color, has_prefix):
        """Test message method formatting."""
        message = "test message"
        method = getattr(self.console_output, method_name)
        method(message)

        call_args = self.console_output.console.print.call_args[0][0]
        assert f"[{expected_color}]" in call_args
        assert f"[/{expected_color}]" in call_args
        assert message in call_args


class TestFormatTimedelta:
    """Test _format_timedelta static method."""

    @pytest.mark.parametrize(
        "delta,expected",
        [
            (timedelta(seconds=30), "00:00:30"),
            (timedelta(minutes=5, seconds=30), "00:05:30"),
            (timedelta(hours=2, minutes=30, seconds=45), "02:30:45"),
            (timedelta(days=1), "1d 00:00:00"),
            (timedelta(days=2, hours=3, minutes=15, seconds=30), "2d 03:15:30"),
            (timedelta(days=1, hours=0, minutes=0, seconds=0), "1d 00:00:00"),
            (timedelta(seconds=0), "00:00:00"),
        ],
    )
    def test_format_timedelta(self, delta, expected):
        """Test timedelta formatting."""
        result = ConsoleOutput._format_timedelta(delta)
        assert result == expected

    def test_format_timedelta_large_values(self):
        """Test formatting with large time values."""
        delta = timedelta(days=365, hours=23, minutes=59, seconds=59)
        result = ConsoleOutput._format_timedelta(delta)
        assert result == "365d 23:59:59"

    def test_format_timedelta_edge_cases(self):
        """Test edge cases for timedelta formatting."""
        # Just over 1 hour
        delta = timedelta(hours=1, seconds=1)
        result = ConsoleOutput._format_timedelta(delta)
        assert result == "01:00:01"

        # Just under 1 day
        delta = timedelta(hours=23, minutes=59, seconds=59)
        result = ConsoleOutput._format_timedelta(delta)
        assert result == "23:59:59"


class TestIntegrationScenarios:
    """Test integration scenarios."""

    def setup_method(self):
        """Set up test console."""
        self.console_output = ConsoleOutput()
        self.console_output.console = MagicMock()

    def test_realistic_instance_data(self):
        """Test with realistic instance data."""
        launch_time = datetime.now(timezone.utc) - timedelta(hours=2, minutes=30)
        instances = [
            {
                "InstanceId": "i-0123456789abcdef0",
                "Project": "my-devbox-project",
                "PublicIpAddress": "54.123.45.67",
                "State": "running",
                "InstanceType": "t3.medium",
                "LaunchTime": launch_time,
            }
        ]

        self.console_output.print_instances(instances)

        # Should successfully print without errors
        assert self.console_output.console.print.call_count == 2

    def test_mixed_volume_states(self):
        """Test with volumes in different states."""
        volumes = [
            {
                "VolumeId": "vol-available123",
                "Project": "project-1",
                "State": "available",
                "Size": 100,
                "AvailabilityZone": "us-east-1a",
                "IsOrphaned": True,
            },
            {
                "VolumeId": "vol-inuse456",
                "Project": "project-2",
                "State": "in-use",
                "Size": 200,
                "AvailabilityZone": "us-east-1b",
                "IsOrphaned": False,
            },
        ]

        self.console_output.print_volumes(volumes)

        # Should handle mixed states correctly
        assert self.console_output.console.print.call_count == 2

    def test_partial_data_handling(self):
        """Test handling of partial/incomplete data."""
        # Instance with minimal data
        instances = [{"InstanceId": "i-minimal"}]
        self.console_output.print_instances(instances)

        # Volume with minimal data
        volumes = [{"VolumeId": "vol-minimal"}]
        self.console_output.print_volumes(volumes)

        # Snapshot with minimal data
        snapshots = [{"SnapshotId": "snap-minimal"}]
        self.console_output.print_snapshots(snapshots)

        # Should handle all minimal data without errors
        assert self.console_output.console.print.call_count == 6  # 2 calls per method


class TestTableCreation:
    """Test table creation and formatting."""

    def setup_method(self):
        """Set up test console."""
        self.console_output = ConsoleOutput()
        self.console_output.console = MagicMock()

    @patch("devbox.console_output.Table")
    def test_instances_table_structure(self, mock_table_class):
        """Test instances table column structure."""
        mock_table = MagicMock()
        mock_table_class.return_value = mock_table

        instances = [
            {"InstanceId": "i-test", "Project": "test-project", "State": "running"}
        ]

        self.console_output.print_instances(instances)

        # Check table creation
        mock_table_class.assert_called_once_with(
            show_header=True, header_style="bold cyan"
        )

        # Check columns were added
        expected_columns = [
            "Instance ID",
            "Project",
            "Public IP",
            "State",
            "Type",
            "Uptime",
        ]

        add_column_calls = mock_table.add_column.call_args_list
        assert len(add_column_calls) == len(expected_columns)

    @patch("devbox.console_output.Table")
    def test_volumes_table_structure(self, mock_table_class):
        """Test volumes table column structure."""
        mock_table = MagicMock()
        mock_table_class.return_value = mock_table

        volumes = [{"VolumeId": "vol-test"}]

        self.console_output.print_volumes(volumes)

        # Check columns were added
        expected_columns = [
            "Volume ID",
            "Project",
            "State",
            "Size (GiB)",
            "AZ",
            "Orphaned",
        ]

        add_column_calls = mock_table.add_column.call_args_list
        assert len(add_column_calls) == len(expected_columns)

    @patch("devbox.console_output.Table")
    def test_snapshots_table_structure(self, mock_table_class):
        """Test snapshots table column structure."""
        mock_table = MagicMock()
        mock_table_class.return_value = mock_table

        snapshots = [{"SnapshotId": "snap-test"}]

        self.console_output.print_snapshots(snapshots)

        # Check columns were added
        expected_columns = [
            "Snapshot ID",
            "Project",
            "Size (GiB)",
            "Progress",
            "Created",
            "Orphaned",
        ]

        add_column_calls = mock_table.add_column.call_args_list
        assert len(add_column_calls) == len(expected_columns)
