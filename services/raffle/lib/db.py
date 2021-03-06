from botocore.exceptions import ClientError
import datetime
import os
import random

import boto3
import dateutil.parser

from .auth import is_raffle_admin
from .utils import generate_shortcode

RAFFLE_TABLE_NAME = os.environ.get('RAFFLE_TABLE')
ENTRY_TABLE_NAME = os.environ.get('ENTRY_TABLE')
CLIENT = boto3.client('dynamodb')


class RaffleDoesNotExist(Exception):
    pass

class UserAlreadyRegistered(Exception):
    pass

class NoEntriesForRaffle(Exception):
    pass


def create_raffle(name, description, admins, client=CLIENT):
    shortcode = generate_shortcode()
    dt = datetime.datetime.now().isoformat()

    resp = client.put_item(
        TableName=RAFFLE_TABLE_NAME,
        Item={
            'shortcode': {'S': shortcode},
            'name': {'S': name},
            'description': {'S': description},
            'created_at': {'S': dt},
            'admins': {'SS': admins}
        },
        # Don't want to overwrite an existing raffle if same shortcode is generated.
        ConditionExpression="attribute_not_exists(shortcode)",
    )

    return shortcode


def clean_item(item):
    return {
      'createdAt': item.get('created_at').get('S'),
      'name': item.get('name').get('S'),
      'description': item.get('description', {}).get('S'),
      'shortcode': item.get('shortcode').get('S'),
      'winner': item.get('winner', {}).get('S'),
    }


def get_timestamp(item):
    return dateutil.parser.parse(item.get('createdAt'))


def get_raffles(limit=10, client=CLIENT):
    resp = client.scan(
        TableName=RAFFLE_TABLE_NAME
    )
    items = resp.get('Items')
    cleaned = [clean_item(item) for item in items]
    sorted_items = sorted(cleaned, reverse=True, key=get_timestamp)

    return sorted_items[:limit]


def get_raffle(shortcode, email, client=CLIENT):
    raffle = {
        'shortcode': shortcode,
        'admin': False,
    }
    resp = client.get_item(
        TableName=RAFFLE_TABLE_NAME,
        Key={
            'shortcode': {'S': shortcode}
        }
    )
    item = resp.get('Item')

    if not item:
        raise RaffleDoesNotExist()

    admins = item.get('admins', {}).get('SS')

    if is_raffle_admin(email, admins):
        raffle['admin'] = True

    raffle['name'] = item.get('name').get('S')
    raffle['description'] = item.get('description', {}).get('S')
    raffle['createdAt'] = item.get('created_at').get('S')
    raffle['winner'] = item.get('winner', {}).get('S')
    raffle['isRegistered'] = False

    if email:
        resp = client.get_item(
            TableName=ENTRY_TABLE_NAME,
            Key={
                'shortcode': {'S': shortcode},
                'email': {'S': email},
            }
        )
        if resp.get('Item'):
            raffle['isRegistered'] = True

    return raffle


def register_for_raffle(shortcode, email, client=CLIENT):
    dt = datetime.datetime.now().isoformat()

    resp = client.get_item(
        TableName=RAFFLE_TABLE_NAME,
        Key={
            'shortcode': {'S': shortcode}
        }
    )

    if not resp.get('Item'):
        raise RaffleDoesNotExist()

    try:
        resp = client.put_item(
            TableName=ENTRY_TABLE_NAME,
            Item={
                'shortcode': {'S': shortcode},
                'email': {'S': email},
                'registered_at': {'S': dt},
            },
            # Don't want to overwrite an existing raffle if same shortcode is generated.
            ConditionExpression="attribute_not_exists(email)",
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise UserAlreadyRegistered()
        raise e


def _get_all_emails(shortcode, last_key=None, client=CLIENT):
    emails = []
    params = {
        "TableName": ENTRY_TABLE_NAME,
        "ProjectionExpression": "email",
        "KeyConditionExpression": "shortcode = :shortcode",
        "ExpressionAttributeValues": {
            ":shortcode": {"S": shortcode},
        },
    }
    if last_key:
        params['ExclusiveStartKey'] = last_key
    resp = client.query(**params)
    print(resp)
    if 'LastEvaluatedKey' in resp:
        emails += _get_all_emails(shortcode, resp['LastEvaluatedKey'])

    for item in resp['Items']:
        emails.append(item.get('email').get('S'))

    return emails


def set_winner_for_raffle(shortcode, email, client=CLIENT):
    body = {
        'shortcode': shortcode,
    }

    resp = client.get_item(
        TableName=RAFFLE_TABLE_NAME,
        Key={
            'shortcode': {'S': shortcode}
        }
    )
    item = resp.get('Item')

    if not item:
        raise RaffleDoesNotExist()

    admins = item.get('admins', {}).get('SS')

    if not is_raffle_admin(email, admins):
        raise auth.InvalidAuthentication('User is not an admin for this raffle.')

    body['entries'] = _get_all_emails(shortcode)
    if not body['entries']:
        raise NoEntriesForRaffle()

    body['winner'] = random.choice(body['entries'])

    resp = client.update_item(
        TableName=RAFFLE_TABLE_NAME,
        Key={
            'shortcode': {'S': shortcode},
        },
        UpdateExpression="SET winner = :winner",
        ExpressionAttributeValues={
            ":winner": {'S': body['winner']},
        },
    )

    return body
