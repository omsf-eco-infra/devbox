"""Command-line interface for DevBox management.

This module provides the Click-based CLI for managing DevBox instances.
"""
import sys
import click
from typing import Optional

from .devbox_manager import DevBoxManager
from .console_output import ConsoleOutput

DEFAULT_PARAM_PREFIX = '/devbox'
PARAM_PREFIX_ENV_VAR = 'DEVBOX_PARAM_PREFIX'


def param_prefix_option(func):
    """Add shared --param-prefix option with env var support."""
    return click.option(
        '--param-prefix',
        default=DEFAULT_PARAM_PREFIX,
        envvar=PARAM_PREFIX_ENV_VAR,
        show_default=True,
        show_envvar=True,
        help='SSM parameter prefix'
    )(func)


def get_manager(console: ConsoleOutput, param_prefix: str) -> DevBoxManager:
    """Create a DevBoxManager using the requested parameter prefix."""
    manager_prefix = param_prefix.strip('/') or 'devbox'
    try:
        return DevBoxManager(prefix=manager_prefix)
    except Exception as e:
        console.print_error(f"Failed to initialize AWS clients: {str(e)}")
        sys.exit(1)


@click.group()
@click.version_option()
@click.pass_context
def cli(ctx):
    """DevBox - AWS EC2 Development Environment Manager."""
    ctx.ensure_object(dict)
    ctx.obj['console'] = ConsoleOutput()

@cli.command()
@click.argument('project', required=False)
@param_prefix_option
@click.pass_context
def status(
    ctx,
    project: Optional[str] = None,
    param_prefix: str = DEFAULT_PARAM_PREFIX
):
    """Show status of DevBox resources.

    If PROJECT is provided, only show resources for that project.
    Otherwise, show all resources.
    """
    console = ctx.obj['console']
    manager = get_manager(console, param_prefix)

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
@param_prefix_option
@click.pass_context
def terminate(ctx, instance_id: str, param_prefix: str):
    """Terminate a DevBox instance by its ID."""
    console = ctx.obj['console']
    manager = get_manager(console, param_prefix)

    try:
        result = manager.terminate_instance(instance_id, console)
        console.print_success(
            f"Terminating instance {result['instance_id']} (project: {result['project']})."
        )
    except Exception as e:
        console.print_error(f"Failed to terminate instance: {str(e)}")
        sys.exit(1)

@cli.command()
@click.argument('project')
@click.option('--instance-type', help='EC2 instance type (uses last instance type if not specified)')
@click.option('--key-pair', help='SSH key pair name (uses last keypair if not specified)')
@click.option('--volume-size', type=int, default=0, help='Root volume size in GB')
@click.option('--base-ami', help='Base AMI ID for new instances')
@param_prefix_option
@click.pass_context
def launch(ctx, project: str, instance_type: Optional[str], key_pair: Optional[str],
          volume_size: int, base_ami: Optional[str], param_prefix: str):
    """Launch a new DevBox instance.

    PROJECT is the name of the project to launch.
    """
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
@click.argument('project')
@click.option('--base-ami', required=True, help='Base AMI ID for the project')
@param_prefix_option
@click.pass_context
def new(ctx, project: str, base_ami: str, param_prefix: str):
    """Create a new DevBox project without launching an instance.

    PROJECT is the name of the project to create.
    """
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

@cli.command()
@click.argument('project')
@click.option('--force', is_flag=True, help='Skip confirmation prompts')
@click.pass_context
def delete_project(ctx, project: str, force: bool):
    """Delete a DevBox project and its AMI/snapshots."""
    console = ctx.obj['console']
    manager = ctx.obj['manager']

    try:
        item = manager.get_project_item(project)
        if not item:
            console.print_error(f"Project '{project}' not found in the main table.")
            sys.exit(1)

        in_use, reason = manager.project_in_use(project, item)
        if in_use:
            console.print_error(f"Project '{project}' is currently in use: {reason}")
            sys.exit(1)

        if not force:
            if not click.confirm(f"Delete project '{project}' from the main table?"):
                console.print_warning("Project deletion cancelled.")
                return

        manager.delete_project_entry(project)
        console.print_success(f"Deleted project '{project}' from the main table.")

        ami_id = item.get("AMI")
        if not ami_id:
            console.print_warning(f"No AMI recorded for project '{project}'.")
            return

        if not force:
            if not click.confirm(f"Also delete AMI {ami_id} and its backing snapshot(s)?"):
                console.print_warning("AMI cleanup cancelled.")
                return

        result = manager.delete_ami_and_snapshots(ami_id)
        console.print_success(
            f"Deregistered AMI {result['ami_id']} and deleted {result['snapshot_count']} snapshot(s)."
        )

    except Exception as e:
        console.print_error(f"Failed to delete project: {str(e)}")
        sys.exit(1)

def main():
    """Entry point for the CLI."""
    cli(obj={})

if __name__ == "__main__":
    main()
