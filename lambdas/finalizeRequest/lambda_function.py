import json
import boto3

dynamodb = boto3.resource('dynamodb')
ses = boto3.client('ses')

requests_table = dynamodb.Table('leave_requests')
balances_table = dynamodb.Table('leave_balances')

SENDER_EMAIL = "sriramvineeth7@gmail.com"


def lambda_handler(event, context):
    outcome = event['outcome']  # "approved" or "rejected"
    request_data = event['request']

    employee_id = request_data['employee_id']
    request_id = request_data['request_id']
    leave_type = request_data['leave_type']
    start_date = request_data['start_date']
    end_date = request_data['end_date']
    year = start_date[:4]

    if outcome == 'approved':
        final_status = 'HR_APPROVED' if request_data.get('requested_days', 0) > 5 else 'MGR_APPROVED'

        # Decrement balance only now, on final approval
        if leave_type != 'unpaid':
            balance_key = f"{leave_type}#{year}"
            requested_days = request_data['requested_days']
            balances_table.update_item(
                Key={'employee_id': employee_id, 'leave_type_year': balance_key},
                UpdateExpression='SET remaining = remaining - :d, used = used + :d',
                ExpressionAttributeValues={':d': requested_days}
            )

        subject = "Your leave request has been approved"
        body = f"Your {leave_type} leave from {start_date} to {end_date} has been approved."
    else:
        final_status = 'REJECTED'
        subject = "Your leave request has been rejected"
        body = f"Your {leave_type} leave from {start_date} to {end_date} has been rejected."

    requests_table.update_item(
        Key={'employee_id': employee_id, 'request_id': request_id},
        UpdateExpression='SET #s = :s REMOVE task_token, current_approver_role',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': final_status}
    )

    ses.send_email(
        Source=SENDER_EMAIL,
        Destination={'ToAddresses': [SENDER_EMAIL]},  # swap for employee's real email later
        Message={
            'Subject': {'Data': subject},
            'Body': {'Text': {'Data': body}}
        }
    )

    return {'status': 'finalized', 'final_status': final_status}