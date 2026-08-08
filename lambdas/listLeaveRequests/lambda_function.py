import json
import boto3
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')
requests_table = dynamodb.Table('leave_requests')
balances_table = dynamodb.Table('leave_balances')


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError


def lambda_handler(event, context):
    params = event.get('queryStringParameters') or {}
    mode = params.get('mode', 'my_requests')
    employee_id = params.get('employee_id')

    if mode == 'my_requests':
        if not employee_id:
            return response(400, {'error': 'employee_id is required for my_requests'})

        items = requests_table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key('employee_id').eq(employee_id)
        )['Items']

        balances = balances_table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key('employee_id').eq(employee_id)
        )['Items']

        return response(200, {'requests': items, 'balances': balances})

    elif mode == 'pending_approvals':
        items = requests_table.query(
            IndexName='status-created_at-index',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('status').eq('PENDING')
        )['Items']
        return response(200, {'requests': items})

    elif mode == 'all_approved':
        mgr_items = requests_table.query(
            IndexName='status-created_at-index',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('status').eq('MGR_APPROVED')
        )['Items']
        hr_items = requests_table.query(
            IndexName='status-created_at-index',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('status').eq('HR_APPROVED')
        )['Items']
        return response(200, {'requests': mgr_items + hr_items})

    else:
        return response(400, {'error': f'Unknown mode: {mode}'})


def response(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {'Access-Control-Allow-Origin': '*'},
        'body': json.dumps(body, default=decimal_default)
    }