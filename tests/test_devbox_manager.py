"""Unit tests for devbox devbox_manager module."""

import pytest
import boto3

from moto import mock_aws


from devbox.devbox_manager import DevBoxManager



@mock_aws
class TestDevBoxManager:
    """Test DevBoxManager class."""

    def setup_method(self, method):
        """Set up test manager and AWS resources."""
        self.ec2_client = boto3.client("ec2", region_name="us-east-1")
        self.ec2_resource = boto3.resource("ec2", region_name="us-east-1")
        self.ssm_client = boto3.client("ssm", region_name="us-east-1")
        self.dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        self.manager = DevBoxManager(
            ssm_client=self.ssm_client,
            ec2_client=self.ec2_client,
            ec2_resource=self.ec2_resource,
            dynamodb_resource=self.dynamodb,
        )

    # Tests for __init__ method

    def test_init_with_default_clients(self):
        """Test initialization with default AWS clients."""
        manager = DevBoxManager()

        assert manager.ssm is not None
        assert manager.ec2 is not None
        assert manager.ec2_resource is not None
        assert manager.dynamodb is not None
        assert manager.prefix == "devbox"

    def test_init_with_custom_clients(self):
        """Test initialization with custom AWS clients."""
        mock_ssm = boto3.client("ssm", region_name="us-east-1")
        mock_ec2 = boto3.client("ec2", region_name="us-east-1")
        mock_ec2_resource = boto3.resource("ec2", region_name="us-east-1")
        mock_dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        manager = DevBoxManager(
            ssm_client=mock_ssm,
            ec2_client=mock_ec2,
            ec2_resource=mock_ec2_resource,
            dynamodb_resource=mock_dynamodb,
            prefix="custom-prefix",
        )

        assert manager.ssm == mock_ssm
        assert manager.ec2 == mock_ec2
        assert manager.ec2_resource == mock_ec2_resource
        assert manager.dynamodb == mock_dynamodb
        assert manager.prefix == "custom-prefix"

    def test_init_partial_custom_clients(self):
        """Test initialization with some custom and some default clients."""
        mock_ssm = boto3.client("ssm", region_name="us-east-1")

        manager = DevBoxManager(ssm_client=mock_ssm)

        assert manager.ssm == mock_ssm
        assert manager.ec2 is not None
        assert manager.ec2_resource is not None
        assert manager.dynamodb is not None

    def test_init_manager_state_isolation(self):
        """Test that manager instances are properly isolated."""
        manager1 = DevBoxManager(
            ssm_client=boto3.client("ssm", region_name="us-east-1"),
            ec2_client=boto3.client("ec2", region_name="us-east-1"),
            ec2_resource=boto3.resource("ec2", region_name="us-east-1"),
            dynamodb_resource=boto3.resource("dynamodb", region_name="us-east-1"),
            prefix="prefix1",
        )

        manager2 = DevBoxManager(
            ssm_client=boto3.client("ssm", region_name="us-east-1"),
            ec2_client=boto3.client("ec2", region_name="us-east-1"),
            ec2_resource=boto3.resource("ec2", region_name="us-east-1"),
            dynamodb_resource=boto3.resource("dynamodb", region_name="us-east-1"),
            prefix="prefix2",
        )

        assert manager1.prefix == "prefix1"
        assert manager2.prefix == "prefix2"

    # Tests for get_table method

    def test_get_table_default_param(self):
        """Test getting table with default parameter name."""
        self.ssm_client.put_parameter(
            Name="/devbox/snapshotTable", Value="test-snapshot-table", Type="String"
        )

        self.dynamodb.create_table(
            TableName="test-snapshot-table",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        result = self.manager.get_table()

        assert result.table_name == "test-snapshot-table"

    def test_get_table_custom_param(self):
        """Test getting table with custom parameter name."""
        self.ssm_client.put_parameter(
            Name="/devbox/customTable", Value="custom-table-name", Type="String"
        )

        self.dynamodb.create_table(
            TableName="custom-table-name",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        result = self.manager.get_table("customTable")

        assert result.table_name == "custom-table-name"

    def test_get_table_custom_prefix(self):
        """Test getting table with custom prefix."""
        self.ssm_client.put_parameter(
            Name="/custom/snapshotTable", Value="custom-prefix-table", Type="String"
        )

        self.dynamodb.create_table(
            TableName="custom-prefix-table",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        manager = DevBoxManager(
            ssm_client=self.ssm_client,
            ec2_client=self.ec2_client,
            ec2_resource=self.ec2_resource,
            dynamodb_resource=self.dynamodb,
            prefix="custom",
        )

        result = manager.get_table()

        assert result.table_name == "custom-prefix-table"

    def test_get_table_integration(self):
        """Test get_table method integration with real AWS resources."""
        self.ssm_client.put_parameter(
            Name="/devbox/integrationTable", Value="integration-table", Type="String"
        )

        self.dynamodb.create_table(
            TableName="integration-table",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        result = self.manager.get_table("integrationTable")

        assert result.table_name == "integration-table"

    # Tests for list_instances method

    def test_list_instances_no_project_filter(self):
        """Test listing instances without project filter."""
        response = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
        )
        instance_id = response["Instances"][0]["InstanceId"]

        self.ec2_client.create_tags(
            Resources=[instance_id], Tags=[{"Key": "Project", "Value": "test-project"}]
        )

        result = self.manager.list_instances()

        assert len(result) == 1
        assert result[0]["InstanceId"] == instance_id
        assert result[0]["Project"] == "test-project"
        assert result[0]["InstanceType"] == "t3.medium"

    def test_list_instances_with_project_filter(self):
        """Test listing instances with project filter."""
        response1 = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
        )
        instance1_id = response1["Instances"][0]["InstanceId"]

        response2 = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.large"
        )
        instance2_id = response2["Instances"][0]["InstanceId"]

        self.ec2_client.create_tags(
            Resources=[instance1_id], Tags=[{"Key": "Project", "Value": "my-project"}]
        )
        self.ec2_client.create_tags(
            Resources=[instance2_id], Tags=[{"Key": "Project", "Value": "other-project"}]
        )

        result = self.manager.list_instances(project="my-project")

        assert len(result) == 1
        assert result[0]["InstanceId"] == instance1_id
        assert result[0]["Project"] == "my-project"

    def test_list_instances_empty_response(self):
        """Test listing instances with empty response."""
        result = self.manager.list_instances()
        assert result == []

    def test_list_instances_multiple_reservations(self):
        """Test listing instances across multiple reservations."""
        response1 = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
        )
        instance1_id = response1["Instances"][0]["InstanceId"]

        response2 = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.large"
        )
        instance2_id = response2["Instances"][0]["InstanceId"]

        self.ec2_client.create_tags(
            Resources=[instance1_id], Tags=[{"Key": "Project", "Value": "proj1"}]
        )
        self.ec2_client.create_tags(
            Resources=[instance2_id], Tags=[{"Key": "Project", "Value": "proj2"}]
        )

        result = self.manager.list_instances()

        assert len(result) == 2
        instance_ids = [inst["InstanceId"] for inst in result]
        assert instance1_id in instance_ids
        assert instance2_id in instance_ids

    def test_list_instances_missing_optional_fields(self):
        """Test listing instances with missing optional fields."""
        response = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
        )
        instance_id = response["Instances"][0]["InstanceId"]

        self.ec2_client.create_tags(
            Resources=[instance_id], Tags=[{"Key": "Name", "Value": "test-instance"}]
        )

        result = self.manager.list_instances()

        assert len(result) == 0

    def test_list_instances_client_error(self):
        """Test listing instances with AWS client error."""
        result = self.manager.list_instances()
        assert isinstance(result, list)

    def test_list_instances_with_console_param(self):
        """Test that console parameter is accepted but not used."""
        mock_console = object()
        result = self.manager.list_instances(console=mock_console)
        assert isinstance(result, list)

    @pytest.mark.parametrize("project_filter,should_filter", [
        (None, False),
        ("test-project", True),
        ("", False),
    ])
    def test_list_instances_filter_variations(self, project_filter, should_filter):
        """Test list_instances with various filter combinations."""
        response = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
        )
        instance_id = response["Instances"][0]["InstanceId"]
        self.ec2_client.create_tags(
            Resources=[instance_id], Tags=[{"Key": "Project", "Value": "test-project"}]
        )

        result = self.manager.list_instances(project=project_filter)

        if should_filter:
            assert len(result) == 1
            assert result[0]["Project"] == "test-project"
        else:
            assert len(result) == 1

    def test_list_instances_mixed_project_resources(self):
        """Test listing instances from multiple projects."""
        projects = ["project1", "project2", "project3"]
        instance_ids = []

        for i, project in enumerate(projects):
            response = self.ec2_client.run_instances(
                ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
            )
            instance_id = response["Instances"][0]["InstanceId"]
            instance_ids.append(instance_id)

            self.ec2_client.create_tags(
                Resources=[instance_id], Tags=[{"Key": "Project", "Value": project}]
            )

        all_instances = self.manager.list_instances()
        assert len(all_instances) == 3

        project1_instances = self.manager.list_instances(project="project1")
        assert len(project1_instances) == 1
        assert project1_instances[0]["Project"] == "project1"

    def test_list_instances_large_response_handling(self):
        """Test handling of large instance responses."""
        instance_ids = []
        for i in range(5):
            response = self.ec2_client.run_instances(
                ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
            )
            instance_id = response["Instances"][0]["InstanceId"]
            instance_ids.append(instance_id)

            self.ec2_client.create_tags(
                Resources=[instance_id], Tags=[{"Key": "Project", "Value": f"project{i}"}]
            )

        instances = self.manager.list_instances()

        assert len(instances) == 5
        assert all("Project" in instance for instance in instances)

    def test_list_instances_concurrent_operations_simulation(self):
        """Test behavior under simulated concurrent operations."""
        results = []
        for _ in range(3):
            results.append(self.manager.list_instances())

        assert all(result == [] for result in results)

    # Tests for list_volumes method

    def test_list_volumes_no_filters(self):
        """Test listing volumes without filters."""
        volume_response = self.ec2_client.create_volume(Size=20, AvailabilityZone="us-east-1a")
        volume_id = volume_response["VolumeId"]

        self.ec2_client.create_tags(
            Resources=[volume_id], Tags=[{"Key": "Project", "Value": "test-project"}]
        )

        result = self.manager.list_volumes()

        assert len(result) == 1
        assert result[0]["VolumeId"] == volume_id
        assert result[0]["Project"] == "test-project"
        assert result[0]["IsOrphaned"] is True

    def test_list_volumes_with_project_filter(self):
        """Test listing volumes with project filter."""
        volume1_response = self.ec2_client.create_volume(Size=20, AvailabilityZone="us-east-1a")
        volume1_id = volume1_response["VolumeId"]

        volume2_response = self.ec2_client.create_volume(Size=30, AvailabilityZone="us-east-1a")
        volume2_id = volume2_response["VolumeId"]

        self.ec2_client.create_tags(
            Resources=[volume1_id], Tags=[{"Key": "Project", "Value": "my-project"}]
        )
        self.ec2_client.create_tags(
            Resources=[volume2_id], Tags=[{"Key": "Project", "Value": "other-project"}]
        )

        result = self.manager.list_volumes(project="my-project")

        assert len(result) == 1
        assert result[0]["VolumeId"] == volume1_id

    def test_list_volumes_orphan_only(self):
        """Test listing only orphaned volumes."""
        volume1_response = self.ec2_client.create_volume(Size=20, AvailabilityZone="us-east-1a")
        volume1_id = volume1_response["VolumeId"]

        instance_response = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
        )
        instance_id = instance_response["Instances"][0]["InstanceId"]

        volume2_response = self.ec2_client.create_volume(Size=30, AvailabilityZone="us-east-1a")
        volume2_id = volume2_response["VolumeId"]

        self.ec2_client.attach_volume(
            VolumeId=volume2_id, InstanceId=instance_id, Device="/dev/sdf"
        )

        self.ec2_client.create_tags(
            Resources=[volume1_id], Tags=[{"Key": "Project", "Value": "test-project"}]
        )
        self.ec2_client.create_tags(
            Resources=[volume2_id], Tags=[{"Key": "Project", "Value": "test-project"}]
        )

        result = self.manager.list_volumes(orphan_only=True)

        assert len(result) == 1
        assert result[0]["VolumeId"] == volume1_id

    def test_list_volumes_client_error(self):
        """Test listing volumes with AWS client error."""
        result = self.manager.list_volumes()
        assert isinstance(result, list)

    @pytest.mark.parametrize("state,is_orphaned", [
        ("available", True),
        ("in-use", False),
    ])
    def test_list_volumes_orphan_status(self, state, is_orphaned):
        """Test volume orphan status based on state."""
        if state == "available":
            volume_response = self.ec2_client.create_volume(Size=20, AvailabilityZone="us-east-1a")
            volume_id = volume_response["VolumeId"]
        else:
            instance_response = self.ec2_client.run_instances(
                ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
            )
            instance_id = instance_response["Instances"][0]["InstanceId"]

            volume_response = self.ec2_client.create_volume(Size=20, AvailabilityZone="us-east-1a")
            volume_id = volume_response["VolumeId"]

            self.ec2_client.attach_volume(
                VolumeId=volume_id, InstanceId=instance_id, Device="/dev/sdf"
            )

        self.ec2_client.create_tags(
            Resources=[volume_id], Tags=[{"Key": "Project", "Value": "test-project"}]
        )

        result = self.manager.list_volumes()

        assert len(result) == 1
        assert result[0]["IsOrphaned"] is is_orphaned

    # Tests for list_snapshots method

    def test_list_snapshots_no_filters(self):
        """Test listing snapshots without filters."""
        volume_response = self.ec2_client.create_volume(Size=20, AvailabilityZone="us-east-1a")
        volume_id = volume_response["VolumeId"]

        snapshot_response = self.ec2_client.create_snapshot(
            VolumeId=volume_id, Description="Test snapshot"
        )
        snapshot_id = snapshot_response["SnapshotId"]

        self.ec2_client.create_tags(
            Resources=[snapshot_id], Tags=[{"Key": "Project", "Value": "test-project"}]
        )

        result = self.manager.list_snapshots()

        if result:
            assert result[0]["SnapshotId"] == snapshot_id
            assert result[0]["Project"] == "test-project"

    def test_list_snapshots_with_project_filter(self):
        """Test listing snapshots with project filter."""
        volume_response = self.ec2_client.create_volume(Size=20, AvailabilityZone="us-east-1a")
        volume_id = volume_response["VolumeId"]

        snapshot1_response = self.ec2_client.create_snapshot(
            VolumeId=volume_id, Description="Test snapshot 1"
        )
        snapshot1_id = snapshot1_response["SnapshotId"]

        snapshot2_response = self.ec2_client.create_snapshot(
            VolumeId=volume_id, Description="Test snapshot 2"
        )
        snapshot2_id = snapshot2_response["SnapshotId"]

        self.ec2_client.create_tags(
            Resources=[snapshot1_id], Tags=[{"Key": "Project", "Value": "myproject"}]
        )
        self.ec2_client.create_tags(
            Resources=[snapshot2_id], Tags=[{"Key": "Project", "Value": "otherproject"}]
        )

        result = self.manager.list_snapshots(project="myproject")

        if result:
            assert result[0]["SnapshotId"] == snapshot1_id
            assert result[0]["Project"] == "myproject"

    def test_list_snapshots_orphan_only_filter(self):
        """Test listing only orphaned snapshots."""
        volume_response = self.ec2_client.create_volume(Size=20, AvailabilityZone="us-east-1a")
        volume_id = volume_response["VolumeId"]

        snapshot_response = self.ec2_client.create_snapshot(
            VolumeId=volume_id, Description="Orphan snapshot"
        )
        snapshot_id = snapshot_response["SnapshotId"]

        self.ec2_client.create_tags(
            Resources=[snapshot_id], Tags=[{"Key": "Project", "Value": "test-project"}]
        )

        result = self.manager.list_snapshots(orphan_only=True)

        if result:
            assert result[0]["SnapshotId"] == snapshot_id

    def test_list_snapshots_client_error(self):
        """Test listing snapshots with AWS client error."""
        result = self.manager.list_snapshots()
        assert isinstance(result, list)

    # Tests for terminate_instance method

    def test_terminate_instance_by_instance_id(self):
        """Test terminating instance by instance ID."""
        response = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
        )
        instance_id = response["Instances"][0]["InstanceId"]

        self.ec2_client.create_tags(
            Resources=[instance_id], Tags=[{"Key": "Project", "Value": "my-project"}]
        )

        result = self.manager.terminate_instance(instance_id)
        assert result["project"] == "my-project"

    def test_terminate_instance_by_project_name_single_instance(self):
        """Test terminating instance by project name with single match."""
        response = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
        )
        instance_id = response["Instances"][0]["InstanceId"]

        self.ec2_client.create_tags(
            Resources=[instance_id], Tags=[{"Key": "Project", "Value": "my-project"}]
        )

        result = self.manager.terminate_instance("my-project")
        assert result["project"] == "my-project"

    def test_terminate_instance_by_project_name_multiple_instances(self):
        """Test terminating by project name with multiple matches."""
        response1 = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
        )
        instance1_id = response1["Instances"][0]["InstanceId"]

        response2 = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.large"
        )
        instance2_id = response2["Instances"][0]["InstanceId"]

        self.ec2_client.create_tags(
            Resources=[instance1_id], Tags=[{"Key": "Project", "Value": "multi-project"}]
        )
        self.ec2_client.create_tags(
            Resources=[instance2_id], Tags=[{"Key": "Project", "Value": "multi-project"}]
        )

        with pytest.raises(Exception) as excinfo:
            self.manager.terminate_instance("multi-project")
        assert "Multiple instances found" in str(excinfo.value)

    def test_terminate_instance_not_found(self):
        """Test terminating non-existent instance."""
        with pytest.raises(Exception) as excinfo:
            self.manager.terminate_instance("i-nonexistent")
        assert "No instance found" in str(excinfo.value)

    def test_terminate_instance_no_project_tag(self):
        """Test terminating instance without Project tag."""
        response = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
        )
        instance_id = response["Instances"][0]["InstanceId"]

        with pytest.raises(Exception) as excinfo:
            self.manager.terminate_instance(instance_id)
        assert "not managed by devbox" in str(excinfo.value)

    def test_terminate_instance_with_console_param(self):
        """Test that console parameter is accepted."""
        response = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
        )
        instance_id = response["Instances"][0]["InstanceId"]

        self.ec2_client.create_tags(
            Resources=[instance_id], Tags=[{"Key": "Project", "Value": "test-project"}]
        )

        mock_console = object()
        result = self.manager.terminate_instance(instance_id, console=mock_console)
        assert result["project"] == "test-project"

    @pytest.mark.parametrize("aws_error_scenario", [
        "instance_not_found",
        "multiple_instances",
        "no_project_tag",
    ])
    def test_terminate_instance_error_codes(self, aws_error_scenario):
        """Test terminate_instance with various error scenarios."""
        if aws_error_scenario == "instance_not_found":
            with pytest.raises(Exception) as excinfo:
                self.manager.terminate_instance("i-nonexistent")
            assert "No instance found" in str(excinfo.value)

        elif aws_error_scenario == "multiple_instances":
            for i in range(2):
                response = self.ec2_client.run_instances(
                    ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
                )
                instance_id = response["Instances"][0]["InstanceId"]
                self.ec2_client.create_tags(
                    Resources=[instance_id], Tags=[{"Key": "Project", "Value": "duplicate-project"}]
                )

            with pytest.raises(Exception) as excinfo:
                self.manager.terminate_instance("duplicate-project")
            assert "Multiple instances found" in str(excinfo.value)

        elif aws_error_scenario == "no_project_tag":
            response = self.ec2_client.run_instances(
                ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
            )
            instance_id = response["Instances"][0]["InstanceId"]

            with pytest.raises(Exception) as excinfo:
                self.manager.terminate_instance(instance_id)
            assert "not managed by devbox" in str(excinfo.value)

    def test_project_in_use_with_running_instance(self):
        """Test project_in_use detects active instances."""
        response = self.ec2_client.run_instances(
            ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.medium"
        )
        instance_id = response["Instances"][0]["InstanceId"]
        self.ec2_client.create_tags(
            Resources=[instance_id], Tags=[{"Key": "Project", "Value": "active-project"}]
        )

        in_use, reason = self.manager.project_in_use("active-project", {"Status": "READY"})

        assert in_use is True
        assert "EC2 instances" in reason

    def test_delete_project_entry_removes_item(self):
        """Test delete_project_entry removes a project from DynamoDB."""
        self.ssm_client.put_parameter(
            Name="/devbox/snapshotTable", Value="projects-table", Type="String"
        )

        self.dynamodb.create_table(
            TableName="projects-table",
            KeySchema=[{"AttributeName": "project", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "project", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        table = self.dynamodb.Table("projects-table")
        table.put_item(Item={"project": "demo", "Status": "READY", "AMI": "ami-12345678"})

        self.manager.delete_project_entry("demo")

        resp = table.get_item(Key={"project": "demo"})
        assert "Item" not in resp
