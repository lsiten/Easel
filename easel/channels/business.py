"""Google Business account and location selection from authenticated resources."""
from urllib.parse import quote

from easel.channels.models import AccountProfile, ChannelError, Credential, JsonDocument
from easel.channels.reads import ChannelReads, _rows


def business_accounts(reader: ChannelReads, credential: Credential) -> list[JsonDocument]:
    accounts, cursor, seen = [], '', set()
    while True:
        result = reader.call('GET', '/accounts', credential, params={'pageToken': cursor} if cursor else {}).data
        accounts.extend(_rows(result.get('accounts')))
        after = result.get('nextPageToken')
        if not isinstance(after, str) or not after: return accounts
        if after in seen: raise ChannelError('google-business', 'account_pagination_cycle', status=502)
        cursor = after
        seen.add(after)


def business_locations(reader: ChannelReads, credential: Credential, account_name: str) -> list[JsonDocument]:
    locations, cursor, seen = [], '', set()
    while True:
        result = reader.http.send('GET', 'https://mybusinessbusinessinformation.googleapis.com/v1/' + quote(account_name, safe='/') + '/locations',
            token=credential.access_token.get_secret_value(), params={'readMask': 'name,title,websiteUri,storefrontAddress',
                **({'pageToken': cursor} if cursor else {})}).data
        locations.extend(_rows(result.get('locations')))
        after = result.get('nextPageToken')
        if not isinstance(after, str) or not after: return locations
        if after in seen: raise ChannelError('google-business', 'location_pagination_cycle', status=502)
        cursor = after
        seen.add(after)


def business_profiles(reader: ChannelReads, credential: Credential) -> tuple[AccountProfile, ...]:
    profiles = []
    for account in business_accounts(reader, credential):
        name = account.get('name')
        if not isinstance(name, str) or not name.startswith('accounts/'): raise ChannelError('google-business', 'account_identity_invalid', status=502)
        for location in business_locations(reader, credential, name):
            resource = location.get('name')
            if not isinstance(resource, str) or not resource: raise ChannelError('google-business', 'location_identity_invalid', status=502)
            uid = resource if resource.startswith('accounts/') else name + '/' + resource
            profiles.append(AccountProfile(account_uid=uid, display_name=str(location.get('title') or ''),
                parent_uid=credential.parent_uid or credential.account_uid, extra={'account_name': name,
                    'account_type': account.get('type'), 'location_name': resource, 'website_uri': location.get('websiteUri'),
                    'address': location.get('storefrontAddress')}))
    return tuple(profiles)
