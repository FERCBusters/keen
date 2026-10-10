"""Adapter workflows with actual HTTP parsing and SQL cursors; no live providers."""
from datetime import datetime
from unittest.mock import Mock

import httpx
import pytest
from app.ingest import github, rss
from app.db.models import IngestionCursor
from app.core.config import settings


@pytest.mark.parametrize('kind,payload,expected', [
    ('PushEvent', {'size':0,'ref':'refs/heads/main'}, '0 commit(s) to team/repo (main)'),
    ('PullRequestEvent', {'action':'opened','number':7,'pull_request':{'title':'Fix bug'}}, 'PR opened team/repo#7 "Fix bug"'),
    ('IssuesEvent', {'action':'closed','issue':{'number':3,'title':'Issue'}}, 'issue closed team/repo#3'),
    ('IssueCommentEvent', {'action':'created','issue':{'number':3}}, 'comment created team/repo#3'),
    ('ReleaseEvent', {'release':{'tag_name':'v1','name':'First'}}, 'release published team/repo v1 "First"'),
    ('DeleteEvent', {'ref_type':'branch','ref':'feature/test'}, 'branch deletion team/repo feature/test'),
    ('DeleteEvent', {'ref_type':'tag','ref':'v1'}, 'tag deletion team/repo v1'),
    ('UnknownEvent', {}, 'UnknownEvent team/repo'),
])
def test_github_event_summaries(kind,payload,expected):
    result = github._summarize_event({'type':kind,'actor':{'login':'alice'},'repo':{'name':'team/repo'},'payload':payload})
    assert expected in result
    assert result.endswith('by alice')


@pytest.mark.parametrize('pages', [1,2,3])
def test_github_pagination_stops_at_limit(pages):
    requests=[]
    def handle(req):
        requests.append(req)
        return httpx.Response(200,json=[{'id':str(len(requests))}])
    with httpx.Client(base_url='https://api.example',transport=httpx.MockTransport(handle)) as client:
        rows=github._fetch_pages(client,'/events',per_page=1,max_pages=pages)
    assert len(rows)==pages
    assert [r.url.params['page'] for r in requests]==[str(i) for i in range(1,pages+1)]


@pytest.mark.parametrize('status', [401,403,429,500])
def test_github_http_errors_never_advance_cursor(contract_db,monkeypatch,status):
    cursor=IngestionCursor(name='github:team/repo',last_ts=datetime(2026,1,1),meta={})
    contract_db.add(cursor);contract_db.commit()
    monkeypatch.setattr(github,'_client',lambda:httpx.Client(base_url='https://api.example',transport=httpx.MockTransport(lambda req:httpx.Response(status,json={'message':'Failed'}))))
    store=Mock();monkeypatch.setattr(github,'store_event_with_artifact',store)
    with pytest.raises(httpx.HTTPStatusError):github.ingest_github_repo(contract_db,'team','repo')
    assert cursor.last_ts==datetime(2026,1,1)
    store.assert_not_called()


def test_github_repo_cursor_overlap_order_dedup_and_provenance(contract_db,monkeypatch):
    cursor=IngestionCursor(name='github:team/repo',last_ts=datetime(2026,1,2),meta={})
    contract_db.add(cursor);contract_db.commit()
    events=[{'id':str(i),'created_at':stamp,'type':'PushEvent','actor':{'login':'alice'},'payload':{'size':1}}
            for i,stamp in enumerate(['2026-01-03T00:00:00Z','2026-01-02T00:00:00Z','2026-01-01T00:00:00Z','bad'])]
    monkeypatch.setattr(github,'_client',lambda:httpx.Client(base_url='https://api.example',transport=httpx.MockTransport(lambda req:httpx.Response(200,json=events))))
    store=Mock(side_effect=[{'deduped':True},{'deduped':False}]);monkeypatch.setattr(github,'store_event_with_artifact',store)
    result=github.ingest_github_repo(contract_db,'team','repo',label='Repository',collecting_org='team')
    assert result['created_events']==1
    assert [c.kwargs['external_id'] for c in store.call_args_list]==['1','0']
    assert cursor.last_ts==datetime(2026,1,3)
    assert store.call_args.kwargs['raw_pointer']['github']['org']=='team'
    assert store.call_args.kwargs['summary'].startswith('[Repository]')


RSS=b'''<rss version="2.0"><channel><title>Security</title><link>https://example.org</link>
<item><guid>1</guid><title>Old item</title><link>https://example.org/1</link><pubDate>Thu, 01 Jan 2026 12:00:00 GMT</pubDate></item>
<item><guid>2</guid><title>New item</title><link>https://example.org/2</link><pubDate>Fri, 02 Jan 2026 12:00:00 GMT</pubDate><author>alice</author></item>
</channel></rss>'''
ATOM=b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Updates</title><entry><id>urn:item:1</id><title>Changed</title><updated>2026-01-02T12:00:00Z</updated><author><name>alice</name></author><link href="https://example.org/item"/></entry></feed>'''


@pytest.mark.parametrize('body,count,author', [(RSS,2,'alice'),(ATOM,1,'alice')])
def test_rss_atom_parse_and_store_in_chronological_order(contract_db,monkeypatch,body,count,author):
    monkeypatch.setattr(settings,'demo_mode',False)
    monkeypatch.setattr(rss,'is_safe_url',lambda url:True)
    client_cls=httpx.Client
    monkeypatch.setattr(rss.httpx,'Client',lambda **kw:client_cls(**kw,transport=httpx.MockTransport(lambda req:httpx.Response(200,content=body))))
    store=Mock(return_value={'deduped':False});monkeypatch.setattr(rss,'store_event_with_artifact',store)
    result=rss.ingest_rss_feed(contract_db,{'url':'https://example.org/feed','label':'Security'})
    assert result['created_events']==count
    assert store.call_args.kwargs['actor']==author
    timestamps=[call.kwargs['timestamp'] for call in store.call_args_list]
    assert timestamps==sorted(timestamps)
    assert all(call.kwargs['source']=='rss' for call in store.call_args_list)


@pytest.mark.parametrize('headers',[{'etag':'"version-1"'},{'last-modified':'Fri, 02 Jan 2026 12:00:00 GMT'},{'etag':'"v1"','last-modified':'Fri, 02 Jan 2026 12:00:00 GMT'}])
def test_rss_conditional_headers_survive_to_next_fetch(contract_db,monkeypatch,headers):
    monkeypatch.setattr(settings,'demo_mode',False)
    monkeypatch.setattr(rss,'is_safe_url',lambda url:True)
    requests=[]
    def handle(req):
        requests.append(req)
        return httpx.Response(200 if len(requests)==1 else 304,content=RSS if len(requests)==1 else b'',headers=headers)
    client_cls=httpx.Client
    monkeypatch.setattr(rss.httpx,'Client',lambda **kw:client_cls(**kw,transport=httpx.MockTransport(handle)))
    store=Mock(return_value={'deduped':False});monkeypatch.setattr(rss,'store_event_with_artifact',store)
    cfg={'url':'https://example.org/feed','max_items':1}
    assert rss.ingest_rss_feed(contract_db,cfg)['created_events']==1
    assert rss.ingest_rss_feed(contract_db,cfg)['not_modified'] is True
    if 'etag' in headers:assert requests[1].headers['if-none-match']==headers['etag']
    if 'last-modified' in headers:assert requests[1].headers['if-modified-since']==headers['last-modified']
    assert store.call_count==1


def test_unsafe_rss_url_is_rejected_before_http(contract_db,monkeypatch):
    monkeypatch.setattr(settings,'demo_mode',False)
    monkeypatch.setattr(rss,'is_safe_url',lambda url:False)
    client=Mock();monkeypatch.setattr(rss.httpx,'Client',client)
    assert rss.ingest_rss_feed(contract_db,{'url':'http://127.0.0.1'})['ok'] is False
    client.assert_not_called()


@pytest.mark.parametrize('mode', ['preview','full'])
def test_bookstack_capture_preserves_scope_and_respects_content_mode(contract_db,monkeypatch,mode):
    import json
    from app.ingest import bookstack
    monkeypatch.setattr(settings,'bookstack_enabled',True)
    monkeypatch.setattr(settings,'bookstack_base_url','https://books.example.org')
    config={'label':'Policies','book':{'id':7,'slug':'isms'},'capture':{'mode':mode,'preview_chars':8},
            'selected_pages':[{'id':3,'book_id':7,'book_slug':'isms'}]}
    monkeypatch.setattr(bookstack,'load_bookstack_config',lambda path:config)
    monkeypatch.setattr('app.ingest.connections.deployment_document',lambda *args, **kwargs:config)
    fetched=[]
    def handle(req):
        fetched.append(req)
        if req.url.path=='/api/system':return httpx.Response(200,json={'app_name':'Policies','base_url':'https://books.example.org'})
        if req.url.path=='/api/pages':return httpx.Response(200,json={'data':[{'id':2,'slug':'security'}]})
        if req.url.path in ['/api/pages/2','/api/pages/3']:
            return httpx.Response(200,json={'id':int(req.url.path.rsplit('/',1)[1]),'slug':'security','name':'Security',
                'created_at':'2026-01-01T00:00:00Z','updated_at':'2026-01-02T00:00:00Z',
                'html':'<p>Long policy text</p>','updated_by':{'name':'Alice'}})
        raise AssertionError(req.url)
    monkeypatch.setattr(bookstack,'_client',lambda:httpx.Client(base_url='https://books.example.org',transport=httpx.MockTransport(handle)))
    store=Mock(return_value={'deduped':False,'event_id':None});monkeypatch.setattr(bookstack,'store_event_with_artifact',store)
    result=bookstack.ingest_bookstack_all(contract_db)
    assert result[0]['created']==2 and result[0]['errors']==[]
    assert len(store.call_args_list)==2
    for call in store.call_args_list:
        row=call.kwargs
        assert row['actor']=='Alice'
        assert row['normalized_payload']['bookstack']['book_slug']=='isms'
        payload=json.loads(row['artifact_bytes'])['page']
        assert payload['preview']=='Long pol'
        assert ('html' in payload)==(mode=='full')
    listing=next(req for req in fetched if req.url.path=='/api/pages')
    assert listing.url.params['filter[book_id]']=='7'
    assert listing.url.params['filter[draft]']=='false'
    assert contract_db.query(IngestionCursor).filter(IngestionCursor.name.like('env:bookstack:%')).one().last_ts is not None


@pytest.mark.parametrize('match,page,expected', [
    ({'id':3},{'id':3},True),({'id':3},{'id':4},False),
    ({'slug':'security'},{'slug':'SECURITY'},True),({'slug':'security'},{'slug':'other'},False),
    ({'slug_regex':'^sec-'},{'slug':'sec-policy'},True),({'slug_regex':'^sec-'},{'slug':'other'},False),
    ({'title':'Policy'},{'name':'POLICY'},True),({'title_regex':'^Policy'},{'name':'Guide'},False),
    ({'book':{'id':7},'slug':'policy'},{'book_id':8,'slug':'policy'},False),
    ({'book':{'slug':'isms'},'slug':'policy'},{'book_slug':'isms','slug':'policy'},True),
])
def test_bookstack_page_mapping_selectors(match,page,expected):
    from app.ingest import bookstack
    mappings=bookstack._parse_page_mappings({'page_mappings':[{'match':match,'map_to':['A1']}]})
    assert len(mappings)==1
    assert mappings[0].matches(page,page) is expected


@pytest.mark.parametrize('value,expected', [
    ('2026-01-01T11:00:00+11:00',datetime(2026,1,1)),('2026-01-01T00:00:00Z',datetime(2026,1,1)),
    ('2026-01-01T00:00:00',datetime(2026,1,1)),('invalid',None),('',None),(None,None),
])
def test_bookstack_timestamps_are_normalized_to_naive_utc(value,expected):
    from app.ingest import bookstack
    assert bookstack._parse_bookstack_ts(value)==expected


def test_bookstack_recovers_legacy_page_identity_from_stored_events():
    from app.ingest import bookstack
    from app.db.models import Event
    event=Event(source='bookstack',external_id='page:7:2026-01-01',system='Policies:isms:security',
                summary="bookstack: updated page 'Security' (isms-security)",normalized_payload={},raw_pointer={})
    full,listing=bookstack._event_bookstack_page_payloads(event)
    assert full==listing
    assert full['id']==7 and full['slug']=='security' and full['book_slug']=='isms' and full['name']=='Security'


RDF_RSS = b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
 <channel rdf:about="https://example.org/dsa"><title>Security advisories</title></channel>
 <item rdf:about="urn:advisory:1"><title>Package security update</title>
 <link>https://example.org/advisory/1</link><dc:date>2026-01-02T12:00:00Z</dc:date>
 <dc:creator>Security team</dc:creator><description>Security fixes</description></item>
 </rdf:RDF>'''


def test_rdf_feed_recovers_an_empty_cursor_with_cached_headers(contract_db, monkeypatch):
    url = 'https://example.org/dsa'
    cursor = IngestionCursor(name=rss._feed_cursor_name(url, name_hint='Advisories'),
                             last_ts=None, meta={'etag':'"old"','last_modified':'old'})
    contract_db.add(cursor);contract_db.commit()
    requests=[]
    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=RDF_RSS, headers={'etag':'"new"'})
    client = httpx.Client
    monkeypatch.setattr(rss, 'is_safe_url', lambda url: True)
    monkeypatch.setattr(rss.httpx, 'Client', lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handle)))
    store = Mock(return_value={'deduped':False})
    monkeypatch.setattr(rss, 'store_event_with_artifact', store)
    result = rss.ingest_rss_feed(contract_db, {'url':url,'label':'Advisories'})
    assert result['created_events'] == 1 and result['feed_type'] == 'rss1'
    assert 'if-none-match' not in requests[0].headers
    assert 'if-modified-since' not in requests[0].headers
    assert cursor.last_ts == datetime(2026,1,2,12)
    assert store.call_args.kwargs['actor'] == 'Security team'
    assert store.call_args.kwargs['external_id'] == rss._external_id(url, 'urn:advisory:1')


@pytest.mark.parametrize('body', [b'<html><body>Not a feed</body></html>', b'<rss/>'])
def test_unsupported_or_malformed_feed_does_not_update_cache(contract_db, monkeypatch, body):
    url='https://example.org/feed'
    cursor=IngestionCursor(name=rss._feed_cursor_name(url,name_hint='Security'),last_ts=None,meta={})
    contract_db.add(cursor);contract_db.commit()
    client=httpx.Client
    monkeypatch.setattr(rss,'is_safe_url',lambda url:True)
    monkeypatch.setattr(rss.httpx,'Client',lambda **kwargs:client(**kwargs,transport=httpx.MockTransport(lambda request:httpx.Response(200,content=body,headers={'etag':'"bad"'}))))
    with pytest.raises(ValueError):rss.ingest_rss_feed(contract_db,{'url':url,'label':'Security'})
    assert cursor.meta == {} and cursor.last_ts is None
