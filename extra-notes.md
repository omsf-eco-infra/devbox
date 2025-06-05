
## Importing key pair

```bash
aws ec2 import-key-pair \
--region us-east-1 \
--key-name dwhs-test-devbox \
--public-key-material fileb:///Users/dwhs/.ssh/devbox-testing.pub
```

## Getting AMI for deep learning:

```bash
aws ssm get-parameter \
--name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-24.04/latest/ami-id \
--region us-east-1 \
--query "Parameter.Value" \
--output text
```
