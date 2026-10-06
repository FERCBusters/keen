"""Starter recipes use the same engine as custom definitions; no service secrets."""
from .schema import Definition


def templates():
    common = Definition().model_dump()
    def recipe(key,name,description,**changes):
        return {'id':key,'name':name,'description':description,'definition':{**common,**changes}}
    return [
        recipe('custom','Custom JSON API','Start with a JSON array and map your own fields.'),
        recipe('github','GitHub repository events','GET /repos/OWNER/REPO/events; configure bearer authentication. Does not replace specialised GitHub audit collectors.',
            path='/repos/OWNER/REPO/events',fields={'id':{'path':'/id'},'timestamp':{'path':'/created_at'},'summary':{'path':'/type'},'actor':{'path':'/actor/login'},'action':{'path':'/type'}},
            pagination={'mode':'link','size':100},query={'per_page':100}),
        recipe('gitlab','GitLab project events','GET /api/v4/projects/PROJECT_ID/events; use a PRIVATE-TOKEN header connection.',
            path='/api/v4/projects/PROJECT_ID/events',fields={'id':{'path':'/id'},'timestamp':{'path':'/created_at'},'summary':{'path':'/action_name'},'actor':{'path':'/author_username'}},
            pagination={'mode':'page','parameter':'page','size_parameter':'per_page','size':100}),
        recipe('jenkins','Jenkins build history','Set path to /job/JOB/api/json. Basic authentication uses your username and API token. Fetches a bounded recent build list.',
            path='/job/JOB/api/json',query={'tree':'builds[id,timestamp,displayName,result,url]{0,100}'},records_path='/builds',
            fields={'id':{'path':'/id'},'timestamp':{'path':'/timestamp','transform':'unix_ms'},'summary':{'path':'/displayName'},'outcome':{'path':'/result','transform':'lower'},'url':{'path':'/url'}}),
        recipe('bookstack','BookStack page inventory','Use a header connection: Authorization with secret “Token ID:SECRET”. Collects page metadata; specialised BookStack capture retains document snapshots.',
            path='/api/pages',records_path='/data',fields={'id':{'path':'/id'},'timestamp':{'path':'/updated_at'},'summary':{'path':'/name'}},
            pagination={'mode':'offset','parameter':'offset','start':0,'size_parameter':'count','size':100}),
    ]
