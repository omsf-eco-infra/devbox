"""
DevBox - A tool for managing AWS EC2 development environments.

This package provides a command-line interface for managing EC2 instances,
volumes, and snapshots for development environments.
"""

__version__ = "0.1.0"

# Import key components for easier access
from .devbox_manager import DevBoxManager
from .console_output import ConsoleOutput
from .launch import main as launch_instance
from .cli import cli as devbox_cli

# Alias for backward compatibility
main = devbox_cli

__all__ = [
    "DevBoxManager",
    "ConsoleOutput",
    "launch_instance",
    "devbox_cli",
    "main",
]
