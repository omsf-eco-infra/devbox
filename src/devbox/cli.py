"""Command-line interface for DevBox management.

This module provides the Click-based CLI for managing DevBox instances.
"""
import sys
import click
from typing import Optional

from .devbox_manager import DevBoxManager
from .console_output import ConsoleOutput

@click.group()
@click.version_option()
@click.pass_context
def cli(ctx):
    """DevBox - AWS EC2 Development Environment Manager."""
    ctx.ensure_object(dict)
    ctx.obj['console'] = ConsoleOutput()

    try:
        ctx.obj['manager'] = DevBoxManager()
    except Exception as e:
        ctx.obj['console'].print_error(f"Failed to initialize AWS clients: {str(e)}")
        sys.exit(1)

@cli.command()
@click.argument('project', required=False)
@click.pass_context
def status(ctx, project: Optional[str] = None):
    """Show status of DevBox resources.

    If PROJECT is provided, only show resources for that project.
    Otherwise, show all resources.
    """
    console = ctx.obj['console']
    manager = ctx.obj['manager']

    try:
        # List instances, volumes, and snapshots
        instances = manager.list_instances(project, console)
        volumes = manager.list_volumes(project, console)
        snapshots = manager.list_snapshots(project, console)

        # Display the results using console methods
        console.print_instances(instances)
        console.print_volumes(volumes)
        console.print_snapshots(snapshots)

    except Exception as e:
        console.print_error(f"Failed to retrieve status: {str(e)}")
        sys.exit(1)

@cli.command()
@click.argument('instance_id')
@click.pass_context
def terminate(ctx, instance_id: str):
    """Terminate a DevBox instance by its ID."""
    console = ctx.obj['console']
    manager = ctx.obj['manager']

    try:
        success, message = manager.terminate_instance(instance_id, console)
        if success:
            console.print_success(message)
        else:
            console.print_error(message)
            sys.exit(1)
    except Exception as e:
        console.print_error(f"Failed to terminate instance: {str(e)}")
        sys.exit(1)

@cli.command()
@click.option('--project', required=True, help='Project name')
@click.option('--instance-type', help='EC2 instance type (uses last instance type if not specified)')
@click.option('--key-pair', help='SSH key pair name (uses last keypair if not specified)')
@click.option('--volume-size', type=int, default=100, help='Root volume size in GB')
@click.option('--base-ami', help='Base AMI ID for new instances')
@click.option('--param-prefix', default='/devbox', help='SSM parameter prefix')
@click.pass_context
def launch(ctx, project: str, instance_type: Optional[str], key_pair: Optional[str],
          volume_size: int, base_ami: Optional[str], param_prefix: str):
    """Launch a new DevBox instance."""
    from .launch import launch_programmatic

    console = ctx.obj['console']

    try:
        launch_programmatic(
            project=project,
            instance_type=instance_type,
            key_pair=key_pair,
            volume_size=volume_size,
            base_ami=base_ami,
            param_prefix=param_prefix
        )
    except Exception as e:
        console.print_error(f"Failed to launch instance: {str(e)}")
        sys.exit(1)

@cli.command()
@click.option('--project', required=True, help='Project name')
@click.option('--base-ami', required=True, help='Base AMI ID for the project')
@click.option('--param-prefix', default='/devbox', help='SSM parameter prefix')
@click.pass_context
def new(ctx, project: str, base_ami: str, param_prefix: str):
    """Create a new DevBox project without launching an instance."""
    from .new import new_project_programmatic

    console = ctx.obj['console']

    try:
        new_project_programmatic(
            project=project,
            base_ami=base_ami,
            param_prefix=param_prefix
        )
    except Exception as e:
        console.print_error(f"Failed to create project: {str(e)}")
        sys.exit(1)

def main():
    """Entry point for the CLI."""
    cli(obj={})

if __name__ == "__main__":
    main()
