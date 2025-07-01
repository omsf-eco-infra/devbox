

## AWS Account Setup

This is a once-per-account process. Individual users will not need to do this.

Create `main.tf` with contents similar to the following:

```hcl
provider "aws" {
  region = "us-east-1"
}
```

## Individual User Setup

**Prerequisite:** You must have an AWS account and the AWS CLI installed and
configured.

Each user will have to complete these steps once, but this will be reused
between different DevBox projects.

1. Set up your SSH keys.
   - Generate a new SSH key pair if you don't have one.
   - Add the public key to your AWS account's EC2 key pairs.


## DevBox Setup

This is for an individual DevBox project. 

## Troubleshooting

### connect to host `<ip>` port 22: Connection refused

Sometimes you'll get this error if you try to SSH into a DevBox immediately
after it starts running. This is because sometimes the networking is not fully
set up yet. Wait a few seconds and try again.


