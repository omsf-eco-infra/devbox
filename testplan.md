# Test plan for DevBox

## t3.micro testing

First, ensure that the dwhs-test-devbox key pair has been created. See the notes for details.

Launch the t3.micro instance using:

```bash
python launch.py --instance-type t3.micro --key-pair dwhs-test-devbox --base-ami ami-0953476d60561c955 --project test-devbox
```

Next, ssh into the instance, using

```bash
ssh -o StrictHostKeyChecking=no -i /path/to/private/key ec2-user@$INSTANCE_IP
```

Run `ls`: nothing should be there.
Run `echo hello > hello.txt`: this should succeed.
Run `ls` again; you should see `hello.txt`.
`exit` the instance.

Go to the AWS console and terminate the instance.

Check for the following:

* logs for the create_snapshots lambda
* existing snapshot in EC2
* logs for the create_image lambda
* existing AMI in EC2
* logs for the mark_ready lambda
* status marked READY in DynamoDB main table
* DynamoDB meta table is empty
* logs for the delete_volume instance (may not exist)
* volume marked deleted/deleting in EC2

Next, relaunch the instance using the AMI created in the previous step; this is
the same command used before:

```bash
python launch.py --instance-type t3.micro --key-pair dwhs-test-devbox --base-ami ami-0953476d60561c955 --project test-devbox
```

Note that you can also do that without the `--base-ami` option. If you include
`--base-ami`, you will get a warning that the base AMI is not being used.

Next, ssh into the instance again, using the same command as before. Check that
`hello.txt` is still there.  At this point, we're satisfied that this work.

## g4dn.xlarge testing

Start with `--base-ami ami-0eb94e3d16a6eea5f`.
