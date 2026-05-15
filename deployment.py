import boto3
import time
from base64 import b64encode

def get_latest_ami(ssm_client):
    try:
        # Fetch the latest Amazon Linux 2 AMI from SSM Parameter Store
        response = ssm_client.get_parameter(Name='/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2')
        return response['Parameter']['Value']
    except Exception as e:
        print(f"Error fetching AMI: {e}")
        return None

def deploy_nlb_architecture():
    ec2 = boto3.client('ec2', region_name='ap-south-1')
    elbv2 = boto3.client('elbv2', region_name='ap-south-1')
    autoscaling = boto3.client('autoscaling', region_name='ap-south-1')
    ssm = boto3.client('ssm', region_name='ap-south-1')

    print("Starting High-Performance NLB Web App Deployment...")

    # 1. Get Default VPC and Subnets
    vpcs = ec2.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    vpc_id = vpcs['Vpcs'][0]['VpcId']
    
    subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
    subnet_ids = [subnet['SubnetId'] for subnet in subnets['Subnets']][:2] # Ensure we have 2 for HA

    # 2. Create Security Group for EC2 instances
    # Note: NLB passes client IPs by default for instance targets. We must allow HTTP from everywhere.
    try:
        asg_sg = ec2.create_security_group(
            GroupName='NLB-EC2-SG',
            Description='Allow HTTP traffic for NLB targets',
            VpcId=vpc_id
        )
        asg_sg_id = asg_sg['GroupId']
        ec2.authorize_security_group_ingress(
            GroupId=asg_sg_id,
            IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 80, 'ToPort': 80, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}]
        )
        print("[OK] EC2 Security Group created.")
    except Exception as e:
        if 'InvalidGroup.Duplicate' in str(e):
            print("[INFO] EC2 Security Group already exists. Fetching its ID...")
            sgs = ec2.describe_security_groups(GroupNames=['NLB-EC2-SG'])
            asg_sg_id = sgs['SecurityGroups'][0]['GroupId']
        else:
            raise e

    # 3. Create Target Group for NLB (Layer 4 - TCP)
    try:
        target_group = elbv2.create_target_group(
            Name='NLB-Web-TG',
            Protocol='TCP', # Difference from ALB: We use TCP instead of HTTP
            Port=80,
            VpcId=vpc_id,
            TargetType='instance'
        )
        tg_arn = target_group['TargetGroups'][0]['TargetGroupArn']
        print("[OK] Target Group created for TCP.")
    except Exception as e:
        if 'DuplicateTargetGroupName' in str(e):
            print("[INFO] Target Group already exists.")
            tgs = elbv2.describe_target_groups(Names=['NLB-Web-TG'])
            tg_arn = tgs['TargetGroups'][0]['TargetGroupArn']
        else:
            raise e

    # 4. Create Network Load Balancer (NLB)
    nlb_arn = None
    nlb_dns = None
    try:
        nlb = elbv2.create_load_balancer(
            Name='HighPerf-Web-NLB',
            Subnets=subnet_ids,
            Scheme='internet-facing',
            Type='network', # Difference from ALB
            IpAddressType='ipv4'
        )
        nlb_arn = nlb['LoadBalancers'][0]['LoadBalancerArn']
        nlb_dns = nlb['LoadBalancers'][0]['DNSName']
        print("[OK] Network Load Balancer created.")
        
        # Create Listener for NLB -> Target Group (Protocol TCP)
        elbv2.create_listener(
            LoadBalancerArn=nlb_arn,
            Protocol='TCP',
            Port=80,
            DefaultActions=[{'Type': 'forward', 'TargetGroupArn': tg_arn}]
        )
        print("[OK] NLB Listener created.")

    except Exception as e:
        if 'DuplicateLoadBalancerName' in str(e):
            print("[INFO] Network Load Balancer already exists.")
            nlbs = elbv2.describe_load_balancers(Names=['HighPerf-Web-NLB'])
            nlb_arn = nlbs['LoadBalancers'][0]['LoadBalancerArn']
            nlb_dns = nlbs['LoadBalancers'][0]['DNSName']
        else:
            raise e

    # 5. Create Launch Template with Apache Web Server
    user_data = """#!/bin/bash
yum install -y httpd
systemctl start httpd
systemctl enable httpd
TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
INSTANCE_ID=$(curl -H "X-aws-ec2-metadata-token: $TOKEN" -v http://169.254.169.254/latest/meta-data/instance-id)
echo "<h1>High-Performance Web App! (Layer 4 - NLB)</h1><p>Served by EC2 Instance: <b>$INSTANCE_ID</b></p><p>Ultra-low latency delivery via AWS Network Load Balancer!</p>" > /var/www/html/index.html
"""
    try:
        encoded_user_data = b64encode(user_data.encode()).decode()
        ami_id = get_latest_ami(ssm)
        
        response = ec2.create_launch_template(
            LaunchTemplateName='NLB-App-LT',
            LaunchTemplateData={
                'ImageId': ami_id,
                'InstanceType': 't3.micro',
                'SecurityGroupIds': [asg_sg_id],
                'UserData': encoded_user_data
            }
        )
        print("[OK] Launch Template created.")
    except Exception as e:
        if 'InvalidLaunchTemplateName.AlreadyExistsException' in str(e):
            print("[INFO] Launch Template already exists.")
        else:
            raise e

    # 6. Create Auto Scaling Group
    try:
        autoscaling.create_auto_scaling_group(
            AutoScalingGroupName='NLB-App-ASG',
            LaunchTemplate={
                'LaunchTemplateName': 'NLB-App-LT',
                'Version': '$Latest'
            },
            MinSize=2,
            MaxSize=4,
            DesiredCapacity=2,
            VPCZoneIdentifier=",".join(subnet_ids),
            TargetGroupARNs=[tg_arn]
        )
        print("[OK] Auto Scaling Group created. (Spinning up 2 EC2 instances automatically!)")
        
        # 7. Create Target Tracking Scaling Policy (CPU Utilization > 40%)
        autoscaling.put_scaling_policy(
            AutoScalingGroupName='NLB-App-ASG',
            PolicyName='CPU-Target-Tracking-Policy-NLB',
            PolicyType='TargetTrackingScaling',
            TargetTrackingConfiguration={
                'PredefinedMetricSpecification': {
                    'PredefinedMetricType': 'ASGAverageCPUUtilization'
                },
                'TargetValue': 40.0,
                'DisableScaleIn': False
            }
        )
        print("[OK] Target Tracking Scaling Policy applied (Scale out when CPU > 40%).")
        
    except Exception as e:
        if 'AlreadyExists' in str(e):
            print("[INFO] Auto Scaling Group already exists.")
        else:
            raise e

    print("\nDeployment Triggered Successfully!")
    print("="*60)
    print("It will take about 3-5 minutes for the EC2 instances to boot and pass NLB health checks.")
    print(f"Access your ultra-low latency app here: http://{nlb_dns}")
    print("="*60)

if __name__ == "__main__":
    deploy_nlb_architecture()
