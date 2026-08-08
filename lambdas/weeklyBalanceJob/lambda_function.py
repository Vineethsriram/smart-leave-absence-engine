import json
import boto3
from decimal import Decimal
from collections import defaultdict

dynamodb = boto3.resource('dynamodb')
ses = boto3.client('ses')

balances_table = dynamodb.Table('leave_balances')
config_table = dynamodb.Table('leave_config')

SENDER_EMAIL = "sriramvineeth7@gmail.com"
# In a real system, employee_id -> email would come from a directory/Cognito.
# For this project, we route all summary emails to the verified test address.
EMPLOYEE_EMAIL_MAP = {
    "EMP001": "sriramvineeth7@gmail.com"
}


def lambda_handler(event, context):
    # Load config once
    config_items = config_table.scan()['Items']
    config_by_type = {item['leave_type']: item for item in config_items}

    # Scan all balances (fine at this scale; a real system would paginate/use a GSI on a large table)
    balance_items = balances_table.scan()['Items']

    # Group by employee
    by_employee = defaultdict(list)
    for item in balance_items:
        by_employee[item['employee_id']].append(item)

    summary_report = []

    for employee_id, balances in by_employee.items():
        employee_summary_lines = []

        for bal in balances:
            leave_type = bal['leave_type_year'].split('#')[0]
            config = config_by_type.get(leave_type, {})

            if config.get('carry_forward_allowed'):
                max_carry = int(config.get('max_carry_forward', 0))
                remaining = int(bal['remaining'])
                # Simple rule: top up remaining toward allocation, capped by max_carry_forward
                # (This is a simplified/demo carry-forward rule, not a real fiscal-year rollover.)
                carry_amount = min(max_carry, remaining)
                if carry_amount > 0:
                    balances_table.update_item(
                        Key={'employee_id': employee_id, 'leave_type_year': bal['leave_type_year']},
                        UpdateExpression='SET remaining = remaining + :c',
                        ExpressionAttributeValues={':c': carry_amount}
                    )
                    employee_summary_lines.append(
                        f"{leave_type}: +{carry_amount} carried forward (new remaining: {remaining + carry_amount})"
                    )
                else:
                    employee_summary_lines.append(f"{leave_type}: {remaining} remaining (no carry-forward applied)")
            else:
                employee_summary_lines.append(f"{leave_type}: {bal['remaining']} remaining")

        summary_report.append(f"{employee_id}:\n  " + "\n  ".join(employee_summary_lines))

        # Send summary email
        recipient = EMPLOYEE_EMAIL_MAP.get(employee_id)
        if recipient:
            ses.send_email(
                Source=SENDER_EMAIL,
                Destination={'ToAddresses': [recipient]},
                Message={
                    'Subject': {'Data': f'Weekly Leave Balance Summary - {employee_id}'},
                    'Body': {'Text': {'Data': "Your current leave balances:\n\n" + "\n  ".join(employee_summary_lines)}}
                }
            )

    print(f"DEBUG weekly job processed {len(by_employee)} employees")
    return {'status': 'completed', 'employees_processed': len(by_employee), 'report': summary_report}