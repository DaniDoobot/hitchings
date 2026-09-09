import urllib.request
import json
import sys

base_url = 'http://127.0.0.1:8000/api/v1/observatory'

def get_json(path):
    req = urllib.request.Request(f'{base_url}{path}', headers={'Origin': 'http://localhost:5173'})
    with urllib.request.urlopen(req) as resp:
        return resp.status, dict(resp.headers), json.loads(resp.read().decode())

def run_tests():
    print('=== 1. DASHBOARD REAL ===')
    status, headers, dash = get_json('/dashboard')
    print('HTTP Status:', status)
    print('CORS header:', headers.get('access-control-allow-origin'))
    print('total_publications:', dash['total_publications'])
    print('relevant_count:', dash['relevant_count'])
    print('uncertain_count:', dash['uncertain_count'])
    print('not_relevant_count:', dash['not_relevant_count'])
    print('publications_last_7_days:', dash['publications_last_7_days'])
    print('publications_last_30_days:', dash['publications_last_30_days'])
    print('relevant_last_30_days:', dash['relevant_last_30_days'])
    print('top_topics count:', len(dash['top_topics']))
    print('top_sources count:', len(dash['top_sources']))
    print('latest_relevant_entries count:', len(dash['latest_relevant_entries']))

    print('\n=== 2. OBSERVATORIO TOTAL REAL ===')
    status, _, entries = get_json('/entries?limit=20&offset=0')
    print('total:', entries['total'], 'limit:', entries['limit'], 'offset:', entries['offset'], 'items count:', len(entries['items']))

    print('\n=== 3. FILTER RELEVANT ===')
    status, _, rel_entries = get_json('/entries?relevance_status=relevant')
    print('relevant filter total:', rel_entries['total'])

    print('\n=== 4. FILTER PRIVATE_ENFORCEMENT (HIERARCHICAL) ===')
    status, _, priv_entries = get_json('/entries?topic_code=private_enforcement')
    print('private_enforcement filter total:', priv_entries['total'])

    print('\n=== 5. FILTER SOURCE (CNMC) ===')
    status, _, sources = get_json('/sources')
    cnmc = next(s for s in sources if 'CNMC' in s['name'])
    cnmc_id = cnmc['id']
    status, _, cnmc_entries = get_json(f'/entries?source_id={cnmc_id}')
    print(f'CNMC ID: {cnmc_id}, total entries: {cnmc_entries["total"]}')

    print('\n=== 6. FILTER DATE RANGE ===')
    status, _, date_entries = get_json('/entries?date_from=2026-08-01&date_to=2026-08-31')
    print('August 2026 entries total:', date_entries['total'])

    print('\n=== 7. SORT RELEVANCE DESC (Top 5 scores) ===')
    status, _, sorted_entries = get_json('/entries?sort_by=relevance_score&sort_order=desc&limit=5')
    for item in sorted_entries['items']:
        score = item['relevance']['score']
        st = item['relevance']['status']
        title = item['title'][:55] if item['title'] else ''
        print(f'Score: {score:3d} | Status: {st:12s} | {title}...')

    print('\n=== 8. PAGINATION OFFSETS ===')
    status, _, p0 = get_json('/entries?limit=20&offset=0')
    status, _, p20 = get_json('/entries?limit=20&offset=20')
    status, _, p40 = get_json('/entries?limit=20&offset=40')
    status, _, p60 = get_json('/entries?limit=20&offset=60')
    print('Offset  0 first entry ID:', p0['items'][0]['entry_id'])
    print('Offset 20 first entry ID:', p20['items'][0]['entry_id'])
    print('Offset 40 first entry ID:', p40['items'][0]['entry_id'])
    print('Offset 60 first entry ID:', p60['items'][0]['entry_id'])

    print('\n=== 9. DETAIL LIVRONSA ===')
    livronsa_id = '27c1a107-ebfe-40d0-ba9e-d7d399c3c565'
    status, _, liv = get_json(f'/entries/{livronsa_id}')
    print('Title:', liv['title'])
    print('Score:', liv['relevance']['score'])
    print('Status:', liv['relevance']['status'])
    print('Summary length:', len(liv['summary']))
    print('Key points count:', len(liv['key_points']))
    print('Canonical topics:', [t['name'] for t in liv['canonical_topics']])
    print('Summary evidence quotes count:', len(liv['evidence']['summary_quotes']))
    print('Key points evidence groups count:', len(liv['evidence']['key_points']))
    print('Original URL:', liv['url'])

    print('\n=== 10. DETAIL GORMSEN ===')
    gormsen_id = 'ada5d125-a861-4ff8-bcf6-2b2607afd834'
    status, _, gorm = get_json(f'/entries/{gormsen_id}')
    print('Title:', gorm['title'])
    print('Score:', gorm['relevance']['score'])
    print('Status:', gorm['relevance']['status'])
    print('Summary evidence quotes count:', len(gorm['evidence']['summary_quotes']))
    print('Key points evidence groups count:', len(gorm['evidence']['key_points']))
    raw_payload = json.dumps(gorm).lower()
    print('Contains "v6" string anywhere in customer payload?:', 'v6' in raw_payload)
    print('Contains "groundingvalidator"?:', 'groundingvalidator' in raw_payload)
    print('Contains "analysiscall"?:', 'analysiscall' in raw_payload)

    print('\n=== 11. 404 NOT FOUND TEST ===')
    try:
        get_json('/entries/00000000-0000-0000-0000-000000000000')
    except urllib.error.HTTPError as e:
        print(f'Expected HTTP Error 404: status={e.code}, body={e.read().decode()}')

    print('\n=== ALL SMOKE INTEGRATION TESTS COMPLETED SUCCESSFULLY ===')

if __name__ == '__main__':
    run_tests()
