// Examples use the existing secret-authenticated effectiveness webhook.
export function measurementExamples({origin, key, framework, unit='', value=0}) {
  const endpoint=origin+'/api/v1/webhooks/isms/effectiveness-metrics/'+encodeURIComponent(key);
  const payload={framework, value:Number(value)||0, unit, source_type:'other', source_title:'Scheduled measurement'};
  const json=JSON.stringify(payload,null,2);
  const quote=value=>"'"+String(value).replaceAll("'", "'\\''")+"'";
  const curl='# Set KEEN_METRICS_SECRET securely in the reporting environment.\n# Replace value with the observed result, not the target threshold.\n'
    +'curl --fail-with-body --show-error '+quote(endpoint)+' '+String.fromCharCode(92,10)
    +'  -H '+quote('Content-Type: application/json')+' '+String.fromCharCode(92,10)
    +'  -H "${KEEN_METRICS_HEADER:-X-KEEN-Metrics-Secret}: ${KEEN_METRICS_SECRET:?Set KEEN_METRICS_SECRET}" '+String.fromCharCode(92,10)
    +"  --data-binary @- <<'KEEN_METRIC_JSON'\n"+json+'\nKEEN_METRIC_JSON';
  const python='import json\nimport os\nfrom urllib.request import Request, urlopen\n\n'
    +'# Replace value with the actual observed result.\n'
    +'payload = json.loads('+JSON.stringify(json)+')\n'
    +'request = Request('+JSON.stringify(endpoint)+', data=json.dumps(payload).encode(),\n'
    +'    headers={"Content-Type": "application/json",\n'
    +'             os.environ.get("KEEN_METRICS_HEADER", "X-KEEN-Metrics-Secret"): os.environ["KEEN_METRICS_SECRET"]},\n'
    +'    method="POST")\nwith urlopen(request, timeout=30) as response:\n    print(response.read().decode())';
  return {curl,python};
}
export function mountMeasurementExamples(container, read) {
  const details=document.createElement('details'); details.className='border rounded p-3 my-3';
  const title=document.createElement('summary');title.className='fw-semibold';title.textContent='Send measurements from another system';details.append(title);
  const help=document.createElement('p');help.className='small-muted mt-2';help.textContent='Save the measure with a metric key first. Replace the example value with your observed measurement. Each successful POST creates a metric entry; repeat submissions create additional entries. Optional period_start and period_end accept YYYY-MM-DD. Configure the isms_metrics webhook provider and its secret on the KEEN server. Set the same secret as KEEN_METRICS_SECRET on the reporting host; set KEEN_METRICS_HEADER if your provider uses a different header.';details.append(help);
  const setup=document.createElement('pre');setup.className='small';setup.textContent='Webhooks configuration (UI or config-managed YAML):\nproviders:\n  isms_metrics:\n    secret_header: X-KEEN-Metrics-Secret\n    secret_env: KEEN_METRICS_SECRET\n\nSet KEEN_METRICS_SECRET in the KEEN API container environment.';details.append(setup);
  const warning=document.createElement('p');warning.setAttribute('role','status');details.append(warning);
  const panes=[];
  for(const [kind,label] of [['curl','curl'],['python','Python (standard library)']]) {
    const heading=document.createElement('h4');heading.className='h6';heading.textContent=label;
    const pre=document.createElement('pre');pre.className='bg-body-tertiary border rounded p-3';pre.style.maxHeight='22rem';
    const code=document.createElement('code');pre.append(code);
    const copy=document.createElement('button');copy.type='button';copy.className='btn btn-sm btn-outline-secondary mb-3';copy.textContent='Copy '+label;
    copy.onclick=async()=>{try{await navigator.clipboard.writeText(code.textContent);warning.textContent='Copied '+label+'.';}catch{warning.textContent='Select and copy the example text below.';}};
    details.append(heading,copy,pre);panes.push({kind,code,copy});
  }
  function update(){const data=read(), valid=!!data.key?.trim();warning.textContent=valid?'Example value is 0. Supply the actual measured value before sending.':'Enter and save a metric key to enable these examples.';const snippets=measurementExamples({origin:location.origin,...data});for(const pane of panes){pane.code.textContent=valid?snippets[pane.kind]:'';pane.copy.disabled=!valid;}}
  details.addEventListener('toggle',()=>{if(details.open)update();});container.append(details);return update;
}
