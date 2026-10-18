"""Congress.gov API client for fetching federal bill data.

API docs: https://api.congress.gov/
Base URL: https://api.congress.gov/v3
Auth: API key via query param ?api_key=...
Rate limit: 5,000 requests/hour
"""

import time
import httpx
import config

BASE_URL = 'https://api.congress.gov/v3'
REQUEST_DELAY = 0.5  # seconds between requests to be polite


def _get(path, params=None):
    """Make an authenticated GET request to the Congress.gov API."""
    if not config.CONGRESS_API_KEY:
        raise RuntimeError('CONGRESS_API_KEY not configured. Add it to your .env file.')

    if params is None:
        params = {}
    params['api_key'] = config.CONGRESS_API_KEY
    params['format'] = 'json'

    resp = httpx.get(f'{BASE_URL}{path}', params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_bill(congress, bill_type, number):
    """Fetch a single bill's details.

    Args:
        congress: Congress number (e.g. 118)
        bill_type: 'hr', 's', 'hjres', 'sjres'
        number: Bill number (e.g. 6834)

    Returns:
        dict with bill data, or None on error
    """
    try:
        data = _get(f'/bill/{congress}/{bill_type}/{number}')
        return data.get('bill')
    except Exception as e:
        print(f'  Error fetching {bill_type}{number}: {e}')
        return None


def fetch_bill_summaries(congress, bill_type, number):
    """Fetch summaries for a bill (may have multiple versions)."""
    try:
        data = _get(f'/bill/{congress}/{bill_type}/{number}/summaries')
        summaries = data.get('summaries', [])
        if summaries:
            # Return the most recent summary
            return summaries[-1].get('text', '')
        return ''
    except Exception:
        return ''


def fetch_bill_subjects(congress, bill_type, number):
    """Fetch subject terms for a bill."""
    try:
        data = _get(f'/bill/{congress}/{bill_type}/{number}/subjects')
        subjects = data.get('subjects', {})
        terms = subjects.get('legislativeSubjects', [])
        return [t.get('name', '') for t in terms]
    except Exception:
        return []


def fetch_all_bills(bill_list):
    """Fetch full details for a list of bills.

    Args:
        bill_list: List of dicts with 'congress', 'type', 'number', 'note' keys

    Returns:
        List of normalized bill dicts ready for database insertion
    """
    results = []

    for entry in bill_list:
        congress = entry['congress']
        bill_type = entry['type']
        number = entry['number']

        print(f'Fetching {bill_type.upper()} {number} ({entry.get("note", "")})...')
        bill = fetch_bill(congress, bill_type, number)
        if not bill:
            continue

        time.sleep(REQUEST_DELAY)

        # Fetch summary
        summary = fetch_bill_summaries(congress, bill_type, number)
        time.sleep(REQUEST_DELAY)

        # Determine status
        latest_action = bill.get('latestAction', {}).get('text', '')
        status = _parse_status(bill, latest_action)

        # Build bill number display string
        type_prefix = {'hr': 'H.R.', 's': 'S.', 'hjres': 'H.J.Res.', 'sjres': 'S.J.Res.'}.get(bill_type, bill_type.upper())
        bill_number_str = f'{type_prefix} {number}'

        # Sponsor
        sponsors = bill.get('sponsors', [])
        sponsor = sponsors[0].get('fullName', '') if sponsors else None

        # Dates
        introduced = bill.get('introducedDate', None)
        enacted_date = None
        if status == 'enacted':
            for action in bill.get('actions', {}).get('items', []):
                if 'enacted' in action.get('text', '').lower() or 'became public law' in action.get('text', '').lower():
                    enacted_date = action.get('actionDate')
                    break

        # Clean HTML from summary
        if summary:
            import re
            summary = re.sub(r'<[^>]+>', '', summary).strip()

        # Congress.gov URL
        url = bill.get('url', f'https://www.congress.gov/bill/{congress}th-congress/{_type_slug(bill_type)}/{number}')
        # The API url is the API endpoint; build the public URL instead
        public_url = f'https://www.congress.gov/bill/{_ordinal(congress)}-congress/{_type_slug(bill_type)}/{number}'

        results.append({
            'title': bill.get('title', entry.get('note', f'{bill_number_str}')),
            'description': bill.get('title', ''),
            'policy_type': 'bill',
            'level': 'federal',
            'status': status,
            'date_introduced': introduced,
            'date_enacted': enacted_date,
            'bill_number': bill_number_str,
            'sponsor': sponsor,
            'summary_text': summary[:2000] if summary else None,
            'source_url': public_url,
            'congress': congress,
            'subjects': fetch_bill_subjects(congress, bill_type, number),
        })
        time.sleep(REQUEST_DELAY)

    return results


def _parse_status(bill, latest_action):
    """Determine bill status from API data."""
    latest_lower = latest_action.lower()
    if 'became public law' in latest_lower or 'signed by president' in latest_lower:
        return 'enacted'
    if 'passed' in latest_lower and 'vetoed' not in latest_lower:
        return 'introduced'  # Passed one chamber but not enacted
    if 'vetoed' in latest_lower:
        return 'failed'
    return 'introduced'


def _type_slug(bill_type):
    """Convert bill type to URL slug."""
    return {
        'hr': 'house-bill',
        's': 'senate-bill',
        'hjres': 'house-joint-resolution',
        'sjres': 'senate-joint-resolution',
    }.get(bill_type, bill_type)


def _ordinal(n):
    """Convert number to ordinal string (e.g. 118 -> '118th')."""
    if 11 <= (n % 100) <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suffix}'
