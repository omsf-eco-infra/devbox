"""Core functionality for managing devbox instances and resources."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple, Any, TYPE_CHECKING

from botocore.exceptions import ClientError

# Import local modules
from . import utils

if TYPE_CHECKING:
    from mypy_boto3_ec2.client import EC2Client
    from mypy_boto3_ec2.service_resource import EC2ServiceResource
    from mypy_boto3_dynamodb.service_resource import Table as DynamoDBTable
    from mypy_boto3_ssm.client import SSMClient


class DevBoxManager:
    """Manages devbox instances and related AWS resources."""

    def __init__(
        self,
        ssm_client=None,
        ec2_client=None,
        ec2_resource=None,
        dynamodb_resource=None,
        prefix: str = "devbox"
    ):
        """Initialize the DevBoxManager with AWS clients.

        Args:
            ssm_client: Optional pre-configured SSM client
            ec2_client: Optional pre-configured EC2 client
            ec2_resource: Optional pre-configured EC2 resource
            dynamodb_resource: Optional pre-configured DynamoDB resource
            prefix: SSM parameter prefix (default: "devbox")
        """
        self.ssm = ssm_client or utils.get_ssm_client()
        self.ec2 = ec2_client or utils.get_ec2_client()
        self.ec2_resource = ec2_resource or utils.get_ec2_resource()
        self.dynamodb = dynamodb_resource or utils.get_dynamodb_resource()
        self.prefix = prefix

    def get_table(self, table_param: str = "snapshotTable") -> Any:
        """Get a DynamoDB table using the table name from SSM.

        Args:
            table_param: Parameter name in SSM containing the table name

        Returns:
            A DynamoDB Table resource
        """
        table_name = utils.get_ssm_parameter(f"/{self.prefix}/{table_param}")
        return utils.get_dynamodb_table(table_name)

    def list_instances(self, project: Optional[str] = None, console=None) -> List[Dict]:
        """List EC2 instances, optionally filtered by project.

        Args:
            project: Optional project name to filter instances
            console: Optional console output handler

        Returns:
            List of instance dictionaries with relevant attributes
        """
        filters = [
            {"Name": "instance-state-name", "Values": ["running"]},
            {"Name": "tag-key", "Values": ["Project"]}
        ]

        if project:
            filters.append({"Name": "tag:Project", "Values": [project]})

        try:
            response = self.ec2.describe_instances(Filters=filters)
            instances = []

            for reservation in response.get('Reservations', []):
                for instance in reservation.get('Instances', []):
                    instance_info = {
                        'InstanceId': instance['InstanceId'],
                        'Project': utils.get_project_tag(instance.get('Tags', [])),
                        'PublicIpAddress': instance.get('PublicIpAddress', ''),
                        'LaunchTime': instance.get('LaunchTime'),
                        'State': instance.get('State', {}).get('Name', 'unknown'),
                        'InstanceType': instance.get('InstanceType', '')
                    }
                    instances.append(instance_info)

            return instances

        except ClientError as e:
            raise utils.AWSClientError(
                f"Failed to list instances: {str(e)}",
                error_code=e.response.get('Error', {}).get('Code'),
                original_exception=e
            )

    def list_volumes(self, project: Optional[str] = None, console=None, orphan_only: bool = False) -> List[Dict]:
        """List EBS volumes, optionally filtered to show only orphaned volumes.

        Args:
            project: Optional project name to filter volumes
            console: Optional console output handler
            orphan_only: If True, only return volumes in 'available' state

        Returns:
            List of volume dictionaries with relevant attributes
        """
        filters = [{"Name": "tag-key", "Values": ["Project"]}]

        if project:
            filters.append({"Name": "tag:Project", "Values": [project]})

        try:
            response = self.ec2.describe_volumes(Filters=filters)
            volumes = []

            for volume in response.get('Volumes', []):
                if orphan_only and volume.get('State') != 'available':
                    continue

                volume_info = {
                    'VolumeId': volume['VolumeId'],
                    'Project': utils.get_project_tag(volume.get('Tags', [])),
                    'State': volume.get('State', ''),
                    'Size': volume.get('Size', 0),
                    'AvailabilityZone': volume.get('AvailabilityZone', ''),
                    'IsOrphaned': volume.get('State') == 'available'
                }
                volumes.append(volume_info)

            return volumes

        except ClientError as e:
            raise utils.AWSClientError(
                f"Failed to list volumes: {str(e)}",
                error_code=e.response.get('Error', {}).get('Code'),
                original_exception=e
            )

    def list_snapshots(self, project: Optional[str] = None, console=None, orphan_only: bool = False) -> List[Dict]:
        """List EBS snapshots, optionally filtered to show only orphaned snapshots.

        Args:
            project: Optional project name to filter snapshots
            console: Optional console output handler
            orphan_only: If True, only return snapshots not associated with any AMI

        Returns:
            List of snapshot dictionaries with relevant attributes
        """
        try:
            # Get all snapshots with Project tag
            filters = [{"Name": "tag-key", "Values": ["Project"]}]
            if project:
                filters.append({"Name": "tag:Project", "Values": [project]})

            response = self.ec2.describe_snapshots(
                OwnerIds=['self'],
                Filters=filters
            )

            snapshots = []

            for snapshot in response.get('Snapshots', []):
                # Check if snapshot is associated with any AMI
                is_orphan = True
                if snapshot.get('VolumeId'):
                    try:
                        img_resp = self.ec2.describe_images(
                            Filters=[
                                {
                                    'Name': 'block-device-mapping.snapshot-id',
                                    'Values': [snapshot['SnapshotId']]
                                }
                            ]
                        )
                        is_orphan = not bool(img_resp.get('Images', []))
                    except ClientError:
                        # If we can't check, assume it's not an orphan
                        is_orphan = False

                if orphan_only and not is_orphan:
                    continue

                snapshot_info = {
                    'SnapshotId': snapshot['SnapshotId'],
                    'Project': utils.get_project_tag(snapshot.get('Tags', [])),
                    'Progress': snapshot.get('Progress', ''),
                    'VolumeSize': snapshot.get('VolumeSize', 0),
                    'StartTime': snapshot.get('StartTime'),
                    'IsOrphaned': is_orphan
                }
                snapshots.append(snapshot_info)

            return snapshots

        except ClientError as e:
            raise utils.AWSClientError(
                f"Failed to list snapshots: {str(e)}",
                error_code=e.response.get('Error', {}).get('Code'),
                original_exception=e
            )

    def terminate_instance(self, identifier: str, console=None) -> Tuple[bool, str]:
        """Terminate an instance by ID or project name.

        Args:
            identifier: Either an instance ID or project name
            console: Optional console output handler

        Returns:
            A tuple of (success: bool, message: str)
        """
        try:
            # First, try to find instances by project name
            instances = self.list_instances(project=identifier)

            if len(instances) > 1:
                return False, f"Multiple instances found for project '{identifier}'. Please specify instance ID instead."
            elif len(instances) == 1:
                instance_id = instances[0]['InstanceId']
                project = instances[0]['Project']
            else:
                # If no instances found by project name, try as instance ID
                try:
                    response = self.ec2.describe_instances(InstanceIds=[identifier])
                    instance = response['Reservations'][0]['Instances'][0]
                    instance_id = instance['InstanceId']
                    project = utils.get_project_tag(instance.get('Tags', []))
                    if not project:
                        return False, f"Instance {identifier} is not managed by devbox (missing Project tag)."
                except (ClientError, KeyError, IndexError):
                    return False, f"No instance found with ID or project name: {identifier}"

            # Terminate the instance
            self.ec2.terminate_instances(InstanceIds=[instance_id])
            return True, f"Terminating instance {instance_id} (project: {project})."

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'UnknownError')
            return False, f"Error terminating instance: {error_code} - {str(e)}"
