# AWS-Project2
Scalable Web App with NLB and Auto Scaling
This project automatically deploys a high-performance, low-latency web application using AWS Boto3. Unlike an Application Load Balancer (ALB) which operates at Layer 7, this project uses a Network Load Balancer (NLB) which operates at Layer 4.

Architecture
Security Groups:
NLB-EC2-SG: Network Load Balancers don't strictly require Security Groups in this implementation. Instead, we configure our EC2 instances to allow HTTP traffic from anywhere on port 80, as the NLB will preserve the client's IP address.
Launch Template (NLB-App-LT): Defines exactly what an EC2 instance should look like, leveraging a dynamically fetched Amazon Linux 2 AMI and a User Data script that installs an Apache HTTP server.
Network Load Balancer (HighPerf-Web-NLB): Operates at Layer 4 (TCP), ensuring ultra-low latency. It spans across two subnets.
Auto Scaling Group (NLB-App-ASG): Dictates a minimum of 2 instances and a max of 4. Automatically places the spun-up instances into the Network Load Balancer's Target Group.
Target Tracking Policy: Automatically provisions more instances when CPU load exceeds 40%.
Start the Project
No need to manually install dependencies or configure networking across menus. By running the python file, the infrastructure creates itself gracefully.

Run:

python deploy_infrastructure.py
Wait 3-5 minutes for instances to initialize. You will be provided an NLB DNS URL at the end. Open it in your browser to test the ultra-low latency load balancing.
