from .errors import IntegrationError
"""Bounded extraction; identical normalization is used by preview and collection."""
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone, timedelta, date
from urllib.parse import urljoin, urlsplit, quote
from .schema import Definition, pointer
from .transport import request


def timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc)
    result = datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if result.tzinfo is None:
        raise IntegrationError('Timestamp must include a timezone (or use Unix seconds)')
    return result.astimezone(timezone.utc)


def normalize(record, definition):
    if not isinstance(record,dict):
        raise IntegrationError('Each record must be a JSON object')
    fields = {}
    for name, mapping in definition.fields.items():
        value = mapping.value if mapping.value is not None else pointer(record, mapping.path)
        if value is None:
            if name in {'id','timestamp','summary','value','period_start','period_end'}:
                raise IntegrationError(f'Required field {name} is missing at {mapping.path}')
            continue
        value = mapping.values.get(str(value), value)
        if mapping.transform == 'unix_ms':
            value = float(value) / 1000
        elif mapping.transform == 'number':
            value = float(value)
            if not math.isfinite(value):
                raise IntegrationError('Numeric fields must be finite')
        elif mapping.transform == 'boolean':
            if str(value).lower() not in ('true','false','0','1'):
                raise IntegrationError(f'{name} is not a boolean')
            value = str(value).lower() in ('true','1')
        elif mapping.transform == 'json':
            value = json.dumps(value, sort_keys=True)
        else:
            value = str(value)
            if mapping.transform in ('lower','upper'):
                value = getattr(value,mapping.transform)()
        fields[name] = value
    if not fields['id'] or not fields['summary']:
        raise IntegrationError('Record identifier and summary must not be empty')
    fields['timestamp'] = timestamp(fields['timestamp']).isoformat()
    if 'severity' in fields:
        fields['severity'] = int(fields['severity'])
        if not 0 <= fields['severity'] <= 10:
            raise IntegrationError('Severity must be between 0 and 10')
    if definition.output == 'measurement':
        fields['value'] = float(fields['value'])
        if not math.isfinite(fields['value']):
            raise IntegrationError('Measurement must be finite')
        for name in ('period_start','period_end'):
            fields[name] = date.fromisoformat(fields[name]).isoformat()
        if fields['period_start'] > fields['period_end']:
            raise IntegrationError('Measurement period ends before it starts')
    if fields.get('url') and urlsplit(fields['url']).scheme not in ('http','https'):
        raise IntegrationError('Source link must be HTTP or HTTPS')
    return fields


def collect(definition, base_url, auth, cursor=None, preview=False, fetch=request):
    d = definition if isinstance(definition, Definition) else Definition.model_validate(definition)
    start = time.monotonic()
    url = base_url.rstrip('/') + d.path
    initial = timestamp(cursor) if cursor else datetime.now(timezone.utc) - timedelta(days=d.initial_days)
    since = (initial - timedelta(seconds=d.overlap_seconds)).isoformat()
    query, body = dict(d.query), dict(d.body) if d.body is not None else None
    if d.since_parameter:
        query[d.since_parameter] = since
    p = d.pagination
    target = query if p.location == 'query' else body
    if p.location == 'body' and (d.method != 'POST' or body is None):
        raise IntegrationError('Body pagination requires POST and a JSON body')
    number, seen = p.start, set()
    records, pages, watermark = [], 0, initial
    total_bytes = 0
    requests = 0
    complete = False
    while pages < (1 if preview else d.max_pages):
        if requests >= 100:
            raise IntegrationError('Run exceeded 100 requests; narrow the query')
        if time.monotonic() - start > 110:
            raise IntegrationError('Run exceeded its time budget; narrow the query')
        if p.mode in ('page','offset'):
            target[p.parameter] = number
            if p.size_parameter:
                target[p.size_parameter] = p.size
        signature = json.dumps([url,query,body],sort_keys=True)
        if signature in seen:
            raise IntegrationError('Pagination repeated a request; cursor was not advanced')
        seen.add(signature)
        payload, headers = fetch(url, method=d.method, query=query, body=body, headers=d.headers, auth=auth, timeout=20)
        requests += 1
        pages += 1
        total_bytes += len(json.dumps(payload).encode())
        if total_bytes > 16 * 1024 * 1024:
            raise IntegrationError("Run responses exceeded 16 MiB; narrow the query")
        items = pointer(payload, d.records_path)
        if not isinstance(items,list):
            raise IntegrationError('Records path must select a JSON array')
        for item in items:
            if len(records) >= (10 if preview else d.max_records):
                if preview:
                    break
                raise IntegrationError('Record limit reached; narrow the query or raise the bounded limit')
            if d.enrichments:
                if not isinstance(item,dict) or '_related' in item:
                    raise IntegrationError('Detail enrichment requires objects without a reserved _related key')
                item = dict(item)
                item['_related'] = {}
                for detail in d.enrichments:
                    if requests >= 100 or time.monotonic()-start > 110:
                        raise IntegrationError('Detail requests exceeded the request/time budget')
                    def substitute(match):
                        value = pointer(item,match.group(1))
                        if value is None or isinstance(value,(dict,list)):
                            raise IntegrationError('Detail path references a missing/non-scalar field')
                        return quote(str(value),safe='')
                    path = re.sub(r'\{([^}]+)\}',substitute,detail.path)
                    related, _ = fetch(base_url.rstrip('/')+path,auth=auth,timeout=20)
                    requests += 1
                    total_bytes += len(json.dumps(related).encode())
                    if total_bytes > 16 * 1024 * 1024:
                        raise IntegrationError('Run responses exceeded 16 MiB')
                    item['_related'][detail.name] = related
            try:
                fields = normalize(item,d)
            except IntegrationError:
                raise
            except (ValueError,TypeError,OverflowError):
                raise IntegrationError('A record has an invalid timestamp or field value. Check mappings with a sample response.')
            ts = timestamp(fields['timestamp'])
            if ts > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise IntegrationError('Record timestamp is in the future')
            watermark = max(watermark,ts)
            records.append({'fields':fields,'record':item})
        if preview or p.mode == 'none' or not items:
            complete = True
            break
        if p.mode in ('page','offset'):
            if len(items) < p.size:
                complete = True
                break
            number += 1 if p.mode == 'page' else p.size
        else:
            nxt = pointer(payload,p.next_path)
            if p.mode == 'link':
                link = next((v for k,v in headers.items() if k.lower()=='link'),'')
                match = re.search(r'<([^>]+)>\s*;\s*rel="?next"?',link)
                nxt = match.group(1) if match else None
            if nxt is None or nxt == '':
                complete = True
                break
            if p.mode == 'cursor':
                target[p.parameter] = str(nxt)
            else:
                new_url = urljoin(url,str(nxt))
                before, after = urlsplit(base_url), urlsplit(new_url)
                if (before.scheme,before.hostname,before.port or 443) != (after.scheme,after.hostname,after.port or 443):
                    raise IntegrationError('Pagination cannot leave the connection origin')
                url, query = new_url, {}
    if not complete:
        raise IntegrationError('Page limit reached; cursor was not advanced. Narrow the query or raise the bounded limit')
    return records, watermark.isoformat(), pages


def external_id(collector_id, record_id):
    return hashlib.sha256((collector_id+'\0'+str(record_id)).encode()).hexdigest()
