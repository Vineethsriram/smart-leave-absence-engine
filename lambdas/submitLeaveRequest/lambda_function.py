import json
import boto3
import uuid
from datetime import datetime, date

dynamodb = boto3.resource('dynamodb')
sfn = boto3.client('stepfunctions')

requests_table = dynamodb.Table('leave_requests')
balances_table = dynamodb.Table('leave_balances')

STATE_MACHINE_ARN = "arn:aws:states:ap-south-1:984285320119:stateMachine:StateMachine"


def lambda_handler(event, context):
    body = json.loads(event['body']) if 'body' in event else event
    employee_id = body['employee_id']
    leave_type = body['leave_type']
    start_date = body['start_date']
    end_date = body['end_date']
    reason = body.get('reason', '')

    year = start_date[:4]
    requested_days = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
    request_id = f"req_{uuid.uuid4().hex[:10]}"
    created_at = datetime.utcnow().isoformat()

    if leave_type != 'unpaid':
        balance_key = f"{leave_type}#{year}"
        balance_item = balances_table.get_item(
            Key={'employee_id': employee_id, 'leave_type_year': balance_key}
        ).get('Item')

        if not balance_item or balance_item['remaining'] < requested_days:
            remaining = balance_item['remaining'] if balance_item else 0
            requests_table.put_item(Item={
                'employee_id': employee_id, 'request_id': request_id, 'leave_type': leave_type,
                'start_date': start_date, 'end_date': end_date, 'reason': reason,
                'status': 'AUTO_REJECTED',
                'rejection_reason': f"Insufficient balance: {remaining} remaining, {requested_days} requested",
                'created_at': created_at
            })
            return response(400, {'status': 'AUTO_REJECTED', 'reason': f"Insufficient balance: {remaining} remaining, {requested_days} requested"})

    existing = requests_table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key('employee_id').eq(employee_id)
    )['Items']

    for item in existing:
        if item['status'] in ('MGR_APPROVED', 'HR_APPROVED'):
            if not (end_date < item['start_date'] or start_date > item['end_date']):
                requests_table.put_item(Item={
                    'employee_id': employee_id, 'request_id': request_id, 'leave_type': leave_type,
                    'start_date': start_date, 'end_date': end_date, 'reason': reason,
                    'status': 'AUTO_REJECTED',
                    'rejection_reason': f"Overlaps with existing approved leave from {item['start_date']} to {item['end_date']}",
                    'created_at': created_at
                })
                return response(400, {'status': 'AUTO_REJECTED', 'reason': 'Overlapping leave dates'})

    requests_table.put_item(Item={
        'employee_id': employee_id, 'request_id': request_id, 'leave_type': leave_type,
        'start_date': start_date, 'end_date': end_date, 'reason': reason,
        'status': 'PENDING', 'requested_days': requested_days, 'created_at': created_at
    })

    sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        name=request_id,
        input=json.dumps({
            'employee_id': employee_id,
            'request_id': request_id,
            'leave_type': leave_type,
            'start_date': start_date,
            'end_date': end_date,
            'reason': reason,
            'requested_days': requested_days
        })
    )

    return response(200, {'status': 'PENDING', 'request_id': request_id})


def response(status_code, body):
    return {'statusCode': status_code, 'headers': {'Access-Control-Allow-Origin': '*'}, 'body': json.dumps(body)}
