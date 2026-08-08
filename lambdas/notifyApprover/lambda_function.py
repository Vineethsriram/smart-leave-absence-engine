import json
import boto3
import hmac
import hashlib
import base64
import time

dynamodb = boto3.resource('dynamodb')
sns = boto3.client('sns')
secrets_client = boto3.client('secretsmanager')
requests_table = dynamodb.Table('leave_requests')

SECRET = json.loads(
    secrets_client.get_secret_value(SecretId='leave-approval-signing-key')['SecretString']
)['signing_key']

MANAGER_TOPIC_ARN = "arn:aws:sns:ap-south-1:984285320119:leave-manager-notifications"
HR_TOPIC_ARN = "arn:aws:sns:ap-south-1:984285320119:leave-manager-notifications"
API_INVOKE_URL = "https://l2ntfvrtk6.execute-api.ap-south-1.amazonaws.com/dev"


def generate_token(request_id, approver_role, action, expiry_ts, secret):
    message = f"{request_id}:{approver_role}:{action}:{expiry_ts}".encode()
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    payload = f"{request_id}:{approver_role}:{action}:{expiry_ts}:{signature}"
    return base64.urlsafe_b64encode(payload.encode()).decode()


def lambda_handler(event, context):
    task_token = event['taskToken']
    approver_role = event['approverRole']  # "manager" or "hr"
    request_data = event['request']

    employee_id = request_data['employee_id']
    request_id = request_data['request_id']
    leave_type = request_data['leave_type']
    start_date = request_data['start_date']
    end_date = request_data['end_date']
    reason = request_data.get('reason', '')

    print(f"DEBUG notifyApprover called with approverRole={approver_role}, request_id={request_id}")

    # Store this stage's task token on the request item so the click-handler can retrieve it
    requests_table.update_item(
        Key={'employee_id': employee_id, 'request_id': request_id},
        UpdateExpression='SET task_token = :t, current_approver_role = :r',
        ExpressionAttributeValues={':t': task_token, ':r': approver_role}
    )

    expiry_ts = int(time.time()) + (60 * 60 * 48)
    approve_token = generate_token(request_id, approver_role, 'approve', expiry_ts, SECRET)
    reject_token = generate_token(request_id, approver_role, 'reject', expiry_ts, SECRET)

    approve_url = f"{API_INVOKE_URL}/leave/sfn-action?token={approve_token}"
    reject_url = f"{API_INVOKE_URL}/leave/sfn-action?token={reject_token}"

    topic_arn = HR_TOPIC_ARN if approver_role == 'hr' else MANAGER_TOPIC_ARN
    approver_label = "HR" if approver_role == 'hr' else "Manager"

    print(f"DEBUG publishing to topic_arn={topic_arn}")

    sns.publish(
        TopicArn=topic_arn,
        Subject=f"[{approver_label} Review] Leave request from {employee_id}",
        Message=f"{employee_id} requested {leave_type} leave from {start_date} to {end_date}.\n"
                f"Reason: {reason}\n\n"
                f"Approve: {approve_url}\n"
                f"Reject: {reject_url}"
    )

    print("DEBUG sns.publish completed without exception")

    return {'status': 'notification_sent'}