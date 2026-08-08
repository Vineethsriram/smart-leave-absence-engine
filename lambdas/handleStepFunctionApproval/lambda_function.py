import json
import boto3
import hmac
import hashlib
import base64
import time

dynamodb = boto3.resource('dynamodb')
secrets_client = boto3.client('secretsmanager')
sfn = boto3.client('stepfunctions')
requests_table = dynamodb.Table('leave_requests')

SECRET = json.loads(
    secrets_client.get_secret_value(SecretId='leave-approval-signing-key')['SecretString']
)['signing_key']


def verify_token(token, secret):
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        request_id, approver_role, action, expiry_ts, signature = decoded.split(':')
        expiry_ts = int(expiry_ts)

        if time.time() > expiry_ts:
            return None, "Link expired. Please contact HR."

        message = f"{request_id}:{approver_role}:{action}:{expiry_ts}".encode()
        expected_sig = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_sig, signature):
            return None, "Invalid or tampered link."

        return {'request_id': request_id, 'approver_role': approver_role, 'action': action}, None
    except Exception as e:
        print(f"DEBUG verify_token exception: {e}")
        return None, "Malformed link."


def lambda_handler(event, context):
    params = event.get('queryStringParameters') or {}
    token = params.get('token')

    print(f"DEBUG raw token received (first 30 chars): {token[:30] if token else None}")

    if not token:
        return html_response(400, "Missing token.")

    result, error = verify_token(token, SECRET)
    print(f"DEBUG verify_token result: {result}, error: {error}")
    if error:
        return html_response(400, error)

    request_id = result['request_id']
    approver_role = result['approver_role']
    action = result['action']

    scan_result = requests_table.scan(
        FilterExpression=boto3.dynamodb.conditions.Attr('request_id').eq(request_id)
    )
    items = scan_result.get('Items', [])
    print(f"DEBUG scan found {len(items)} items for request_id={request_id}")

    if not items:
        return html_response(404, "Request not found.")

    req = items[0]
    print(f"DEBUG req status: {req.get('status')}, stored_role: {req.get('current_approver_role')}, incoming_role: {approver_role}, action: {action}")

    if req.get('status') != 'PENDING':
        return html_response(400, f"This request was already processed (status: {req.get('status')}).")

    stored_token = req.get('task_token')
    stored_role = req.get('current_approver_role')

    if not stored_token:
        return html_response(400, "No pending approval found for this request. It may have already been actioned.")

    if stored_role != approver_role:
        return html_response(400, f"This request is currently awaiting {stored_role} review, not {approver_role} review.")

    output = json.dumps({'action': action})

    print(f"DEBUG calling send_task_success with token: {stored_token[:20]}...")
    sfn.send_task_success(
        taskToken=stored_token,
        output=output
    )
    print("DEBUG send_task_success completed without exception")

    return html_response(200, f"Request {request_id} has been marked as '{action}' by {approver_role}. The system is processing the next step.")


def html_response(status_code, message):
    return {
        'statusCode': status_code,
        'headers': {'Content-Type': 'text/html'},
        'body': f"<html><body><h2>{message}</h2></body></html>"
    }