"""Console output formatting for devbox CLI."""
from __future__ import annotations

from datetime import datetime
from typing import List, Dict, Any, Optional, Union

from rich.table import Table
from rich.console import Console


class ConsoleOutput:
    """Handles formatting and displaying output to the console."""

    def __init__(self):
        """Initialize console output with a Rich console instance."""
        self.console = Console()

    def print_instances(self, instances: List[Dict[str, Any]]) -> None:
        """Print a table of EC2 instances.

        Args:
            instances: List of instance dictionaries
        """
        if not instances:
            self.console.print("[yellow]No instances found.[/yellow]")
            return

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Instance ID", style="green")
        table.add_column("Project", style="magenta")
        table.add_column("Public IP", style="yellow")
        table.add_column("State", style="blue")
        table.add_column("Type", style="cyan")
        table.add_column("Uptime", style="white")

        for instance in instances:
            launch_time = instance.get('LaunchTime')
            if launch_time:
                uptime = self._format_timedelta(datetime.now(launch_time.tzinfo) - launch_time)
            else:
                uptime = "N/A"

            table.add_row(
                instance['InstanceId'],
                instance.get('Project', ''),
                instance.get('PublicIpAddress', ''),
                instance.get('State', '').capitalize(),
                instance.get('InstanceType', ''),
                uptime
            )

        self.console.print(f"\n[bold underline]EC2 Instances ({len(instances)})[/bold underline]")
        self.console.print(table)

    def print_volumes(self, volumes: List[Dict[str, Any]], show_orphaned: bool = False) -> None:
        """Print a table of EBS volumes.

        Args:
            volumes: List of volume dictionaries
            show_orphaned: If True, highlight orphaned volumes
        """
        if not volumes:
            self.console.print("[yellow]No volumes found.[/yellow]")
            return

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Volume ID", style="green")
        table.add_column("Project", style="magenta")
        table.add_column("State", style="yellow")
        table.add_column("Size (GiB)", justify="right")
        table.add_column("AZ", style="blue")
        table.add_column("Orphaned", justify="center")

        for volume in volumes:
            is_orphaned = volume.get('IsOrphaned', False)
            state = volume.get('State', '').lower()

            # Determine row style based on state
            row_style = None
            if state == 'available':
                row_style = "red"
            elif state == 'in-use':
                row_style = "green"

            table.add_row(
                volume['VolumeId'],
                volume.get('Project', ''),
                state.capitalize(),
                str(volume.get('Size', 0)),
                volume.get('AvailabilityZone', ''),
                "✓" if is_orphaned else "✗",
                style=row_style
            )

        title = "EBS Volumes"
        if show_orphaned:
            title += " (Orphaned Only)"

        self.console.print(f"\n[bold underline]{title} ({len(volumes)})[/bold underline]")
        self.console.print(table)

    def print_snapshots(self, snapshots: List[Dict[str, Any]], show_orphaned: bool = False) -> None:
        """Print a table of EBS snapshots.

        Args:
            snapshots: List of snapshot dictionaries
            show_orphaned: If True, highlight orphaned snapshots
        """
        if not snapshots:
            self.console.print("[yellow]No snapshots found.[/yellow]")
            return

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Snapshot ID", style="green")
        table.add_column("Project", style="magenta")
        table.add_column("Size (GiB)", justify="right")
        table.add_column("Progress", style="yellow")
        table.add_column("Created", style="blue")
        table.add_column("Orphaned", justify="center")

        for snapshot in snapshots:
            is_orphaned = snapshot.get('IsOrphaned', False)
            created = snapshot.get('StartTime')
            created_str = created.strftime('%Y-%m-%d %H:%M') if created else "N/A"

            # Determine row style based on orphan status
            row_style = "red" if is_orphaned and show_orphaned else None

            table.add_row(
                snapshot['SnapshotId'],
                snapshot.get('Project', ''),
                str(snapshot.get('VolumeSize', 0)),
                snapshot.get('Progress', ''),
                created_str,
                "✓" if is_orphaned else "✗",
                style=row_style
            )
        
        title = "EBS Snapshots"
        if show_orphaned:
            title += " (Orphaned Only)"
        
        self.console.print(f"\n[bold underline]{title} ({len(snapshots)})[/bold underline]")
        self.console.print(table)
    
    def print_error(self, message: str) -> None:
        """Print an error message.
        
        Args:
            message: Error message to display
        """
        self.console.print(f"[red]Error: {message}[/red]")
    
    def print_success(self, message: str) -> None:
        """Print a success message.
        
        Args:
            message: Success message to display
        """
        self.console.print(f"[green]{message}[/green]")
    
    def print_warning(self, message: str) -> None:
        """Print a warning message.
        
        Args:
            message: Warning message to display
        """
        self.console.print(f"[yellow]Warning: {message}[/yellow]")
    
    @staticmethod
    def _format_timedelta(delta) -> str:
        """Format a timedelta as a human-readable string.
        
        Args:
            delta: A datetime.timedelta object
            
        Returns:
            Formatted string like "1d 02:30:45"
        """
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if days > 0:
            return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
