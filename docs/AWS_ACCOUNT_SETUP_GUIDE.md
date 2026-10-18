# AWS Free Tier Account Setup Guide for R.I.F.T.

This guide walks you through setting up your AWS account from scratch, securing it, and enabling safety controls so you stay 100% within AWS Free Tier limits.

---

## 1. Create Your Free AWS Account

1. Navigate to: **[https://aws.amazon.com/free/](https://aws.amazon.com/free/)**
2. Click **Create a Free Account**.
3. **Step 1 - Account credentials:**
   - Enter your email address (this becomes your *Root User*).
   - Enter an Account Name (e.g. `rift-cluster`).
   - Verify your email with the confirmation code sent to your inbox.
   - Set a strong password for the root user.
4. **Step 2 - Contact Information:**
   - Choose **Personal** (for your own projects).
   - Fill in your name, phone number, and address.
5. **Step 3 - Payment Information:**
   - Enter credit or debit card details.
   - *Note:* AWS places a temporary verification hold (typically $1 USD or ₹2 INR depending on country) which is immediately reversed. You will **not** be charged as long as you remain within the Free Tier.
6. **Step 4 - Identity Verification:**
   - Verify your identity via SMS or automated phone call.
7. **Step 5 - Support Plan:**
   - Select **Basic Support - Free** (Default).
8. Click **Complete sign up**. Wait 2-5 minutes for AWS account activation confirmation.

---

## 2. Hard Cost Protection: Set a $0.01 Budget Alert

Do this immediately upon logging in to ensure you are notified if any usage ever exceeds free tier limits.

1. Go to the [AWS Management Console](https://console.aws.amazon.com/).
2. Log in using your **Root user** email and password.
3. In the top search bar, type **Billing and Cost Management** and press Enter.
4. In the left navigation sidebar, select **Budgets** (under Cost Management).
5. Click the orange **Create budget** button.
6. Choose **Zero spend budget** template:
   - This sets a $0.01 limit.
   - Under **Email recipients**, enter your email address.
7. Click **Create budget**.
   - *Result:* AWS will automatically email you if any service generates even 1 cent of usage.

---

## 3. Create an IAM Developer User (Never use Root for CLI/API)

Using the Root account for API keys is dangerous. We create a dedicated developer user with programmatic access:

1. In the console search bar, type **IAM** and select **IAM (Identity and Access Management)**.
2. In the left menu, click **Users** > **Create user**.
3. **Step 1: Specify user details:**
   - User name: `rift-admin`
   - Select the checkbox: **Provide user access to the AWS Management Console** (optional, recommended).
   - Set a console password.
4. **Step 2: Set permissions:**
   - Select **Attach policies directly**.
   - In the search filter, type `AdministratorAccess`.
   - Check the box next to `AdministratorAccess`.
5. **Step 3: Review and create:**
   - Click **Create user**.
6. **Step 4: Generate Access Keys for CLI:**
   - Click on the newly created user `rift-admin`.
   - Select the **Security credentials** tab.
   - Scroll down to **Access keys** and click **Create access key**.
   - Choose **Command Line Interface (CLI)**.
   - Check the confirmation box and click **Next**.
   - Click **Create access key**.
   - **Download the .csv file** or copy:
     - `Access key ID` (e.g. `AKIA...`)
     - `Secret access key` (e.g. `wJalrXUtn...`)
   - *Keep these secret! Do not commit them to Git.*

---

## 4. Configure Your PC

Once your access key is ready:

1. Open PowerShell on this machine.
2. Run:
   ```powershell
   aws configure
   ```
3. Enter the prompts:
   - **AWS Access Key ID**: Paste your `AKIA...` key
   - **AWS Secret Access Key**: Paste your secret key
   - **Default region name**: `us-east-1` (or your nearest region, e.g. `ap-south-1`)
   - **Default output format**: `json`

4. Test your connection:
   ```powershell
   aws sts get-caller-identity
   ```
   You should see your Account ID and ARN: `"arn:aws:iam::...:user/rift-admin"`.

---

## 5. What's Next?
Once this is completed, we will deploy **Phase 0** (IoT Core, DynamoDB, S3, SQS) via AWS CDK with one simple command!
