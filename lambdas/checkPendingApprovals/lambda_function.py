import json
import boto3
from datetime import datetime, timezone, timedelta

dynamodb = boto3.resource('dynamodb')
ses = boto3.client('ses')

requests_table = dynamodb.Table('leave_requests')

SENDER_EMAIL = "sriramvineeth7@gmail.com"
MANAGER_EMAIL = "sriramvineeth7@gmail.com"  # in a real system, look this up per-employee's manager
HR_EMAIL = "sriramvineeth7@gmail.com"       # escalation recipient after repeated inaction

ESCALATION_HOURS = 48


def lambda_handler(event, context):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=ESCALATION_HOURS)
    cutoff_iso = cutoff.isoformat()

    # Query the GSI for all PENDING requests, filter by created_at older than cutoff
    response = requests_table.query(
        IndexName='status-created_at-index',
        KeyConditionExpression=boto3.dynamodb.conditions.Key('status').eq('PENDING')
                              & boto3.dynamodb.conditions.Key('created_at').lt(cutoff_iso)
    )
    stale_requests = response.get('Items', [])

    print(f"DEBUG found {len(stale_requests)} stale PENDING requests older than {ESCALATION_HOURS}h")

    notified = []
    for req in stale_requests:
        employee_id = req['employee_id']
        request_id = req['request_id']
        leave_type = req['leave_type']
        start_date = req['start_date']
        end_date = req['end_date']
        created_at = req['created_at']
        current_role = req.get('current_approver_role', 'manager')

        hours_pending = (now - datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc)).total_seconds() / 3600

        # Reminder to the current approver (manager or HR, whoever is holding it up)
        recipient = HR_EMAIL if current_role == 'hr' else MANAGER_EMAIL
        ses.send_email(
            Source=SENDER_EMAIL,
            Destination={'ToAddresses': [recipient]},
            Message={
                'Subject': {'Data': f'[REMINDER] Leave request {request_id} awaiting your review'},
                'Body': {'Text': {'Data': (
                    f"This request has been pending {current_role} review for {hours_pending:.1f} hours "
                    f"(threshold: {ESCALATION_HOURS}h).\n\n"
                    f"Employee: {employee_id}\n"
                    f"Leave: {leave_type}, {start_date} to {end_date}\n"
                    f"Request ID: {request_id}\n\n"
                    f"Please check your earlier notification email for the approve/reject links."
                )}}
            }
        )
        notified.append(request_id)

    return {'status': 'completed', 'stale_requests_found': len(stale_requests), 'reminders_sent': notified}


def html_response(status_code, message):
    return {'statusCode': status_code, 'body': json.dumps({'message': message})}
