const $ = s => document.querySelector(s);
const banksEl = $('#banks');
const fmt = (n,d=2) => n == null ? '—' : Number(n).toFixed(d);
const rateKey = id => `tajik-rate-monitor:observed:${id}`;
const statusText = s => ({ok:'LIVE',partial:'PARTIAL',stale:'STALE',error:'ERROR',no_rate:'NO RATE'})[s] || String(s||'').toUpperCase();
const suitabilityText = s => ({direct_candidate:'DIRECT CANDIDATE',verify_app:'VERIFY APP',experimental:'EXPERIMENTAL',wrong_rate_class:'WRONG RATE CLASS',unsupported:'UNSUPPORTED'})[s] || s;
const visibleRateKeys = ['cash','transfer'];
const RESCAN_ENDPOINT = 'https://iufslbdtryxspuwsfbqn.supabase.co/functions/v1/rescan-rates';
const RESCAN_POLL_MS = 8000;
const RESCAN_TIMEOUT_MS = 4 * 60 * 1000;
let data = null;
let historyData = [];
let refreshResetTimer = null;

function sleep(ms){ return new Promise(resolve=>setTimeout(resolve, ms)); }

function primaryRate(bank){
  if(bank.rates?.transfer?.buy_per_1000 != null) return bank.rates.transfer.buy_per_1000;
  if(bank.primary_category && visibleRateKeys.includes(bank.primary_category) && bank.rates?.[bank.primary_category]?.buy_per_1000 != null){
    return bank.rates[bank.primary_category].buy_per_1000;
  }
  return null;
}
function render(){
  if(!data) return;
  const healthy = data.banks.filter(b=>b.status==='ok'||b.status==='partial').length;
  const failed = data.banks.length - healthy;
  $('#generated').textContent = new Date(data.generated_at).toLocaleString();
  $('#healthy').textContent = `${healthy}/${data.banks.length}`;
  $('#failed').textContent = failed;
  banksEl.innerHTML = '';
  const emphasize = $('#onlyPrimary').checked;
  const pct = Number($('#sberPct').value || 1.5);

  for(const bank of data.banks){
    const card = document.createElement('article'); card.className='bank';
    const observed = Number(localStorage.getItem(rateKey(bank.id))) || null;
    const webPrimary = primaryRate(bank);
    const delta = observed && webPrimary ? webPrimary-observed : null;
    const baseForSber = observed || webPrimary;
    const sber = baseForSber ? baseForSber*(1-pct/100) : null;
    const displayRates = visibleRateKeys
      .filter(key => bank.rates?.[key])
      .map(key => [key, bank.rates[key]]);
    const rows = displayRates.map(([key,r])=>`
      <div class="rate-row ${emphasize && key==='transfer'?'primary':''}">
        <div class="label">${key==='cash'?'Cash':'Transfers'}${key==='transfer'?' ★':''}</div>
        <div class="value"><strong>${fmt(r.buy_per_1000)}</strong><span>BUY / 1,000 RUB</span></div>
        <div class="value"><strong>${fmt(r.sell_per_1000)}</strong><span>SELL / 1,000 RUB</span></div>
      </div>`).join('') || `<div class="rate-row"><div class="label">No cash/transfer rate extracted</div></div>`;

    card.innerHTML = `
      <div class="bank-head">
        <div><h2>${bank.name}</h2><a class="source" href="${bank.source}" target="_blank" rel="noopener">Official source ↗</a></div>
        <div><span class="badge ${bank.status}">${statusText(bank.status)}</span></div>
      </div>
      <div class="rates">${rows}</div>
      <div class="meta">
        <p><span class="badge ${bank.suitability}">${suitabilityText(bank.suitability)}</span></p>
        <p>${bank.note||''}</p>
        <p>Bank timestamp: ${bank.source_updated_at ? new Date(bank.source_updated_at).toLocaleString() : 'not exposed / not parsed'} · Last success: ${bank.last_success_at ? new Date(bank.last_success_at).toLocaleString() : '—'}</p>
        ${bank.error?`<p>Collector: ${bank.error}</p>`:''}
        <div class="app-compare">
          <input class="observed" inputmode="decimal" placeholder="Observed app transfer / 1000" value="${observed||''}" data-bank="${bank.id}">
          <div class="delta ${delta==null?'':Math.abs(delta)<=0.05?'good':'bad'}">Transfer web − app<br><strong>${delta==null?'—':(delta>=0?'+':'')+fmt(delta)}</strong></div>
          <div class="sber">Sber est. −${pct}%<br><strong>${fmt(sber)}</strong></div>
        </div>
      </div>`;
    banksEl.appendChild(card);
  }
  document.querySelectorAll('.observed').forEach(input=>input.addEventListener('change',e=>{
    const v = Number(String(e.target.value).replace(',','.'));
    if(v>50 && v<200) localStorage.setItem(rateKey(e.target.dataset.bank), String(v));
    else localStorage.removeItem(rateKey(e.target.dataset.bank));
    render();
  }));
  renderHistory();
}
function renderHistory(){
  const list = $('#history');
  const filtered = historyData.map(h=>({
    ...h,
    rates:Object.fromEntries(Object.entries(h.rates||{}).filter(([k])=>visibleRateKeys.includes(k)))
  })).filter(h=>Object.keys(h.rates).length);
  const items = filtered.slice(-30).reverse();
  $('#historyCount').textContent = `${filtered.length} cash/transfer changes`;
  list.innerHTML = items.map(h=>{
    const vals = visibleRateKeys.filter(k=>h.rates?.[k]).map(k=>`${k==='cash'?'cash':'transfer'}: ${h.rates[k].buy==null?'—':fmt(h.rates[k].buy*1000)}`).join(' · ');
    return `<div class="history-item"><div class="time">${new Date(h.at).toLocaleString()}</div><strong>${h.name}</strong><div class="change">${vals}</div></div>`;
  }).join('') || '<p class="sub">Cash/transfer history begins after the next successful collections.</p>';
}

function setRefreshLabel(text, resetAfterMs=null){
  const button = $('#refresh');
  button.textContent = text;
  if(refreshResetTimer) clearTimeout(refreshResetTimer);
  if(resetAfterMs){
    refreshResetTimer = setTimeout(()=>{
      button.textContent = 'Rescan rates';
      refreshResetTimer = null;
    }, resetAfterMs);
  }
}

async function fetchSnapshot(){
  const t=Date.now();
  const [r,h]=await Promise.all([
    fetch(`results.json?t=${t}`,{cache:'no-store'}),
    fetch(`history.json?t=${t}`,{cache:'no-store'})
  ]);
  if(!r.ok) throw new Error(`results.json HTTP ${r.status}`);
  const nextData=await r.json();
  historyData=h.ok?await h.json():[];
  data=nextData;
  $('#alert').classList.add('hidden');
  render();
  return nextData;
}

async function load(){
  try{
    await fetchSnapshot();
  }catch(e){
    $('#alert').textContent=`Could not load collector results: ${e.message}`;
    $('#alert').classList.remove('hidden');
  }
}

async function waitForNewSnapshot(previousGeneratedAt){
  const started=Date.now();
  while(Date.now()-started < RESCAN_TIMEOUT_MS){
    await sleep(RESCAN_POLL_MS);
    try{
      const next=await fetchSnapshot();
      if(next?.generated_at && next.generated_at !== previousGeneratedAt) return true;
    }catch(e){
      console.warn('Snapshot polling failed', e);
    }
  }
  return false;
}

async function rescanRates(){
  const button=$('#refresh');
  const previousGeneratedAt=data?.generated_at || null;
  button.disabled=true;
  button.setAttribute('aria-busy','true');
  setRefreshLabel('Starting scan…');
  $('#alert').classList.add('hidden');

  try{
    const response=await fetch(RESCAN_ENDPOINT,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:'{}',
      cache:'no-store'
    });
    let payload={};
    try{ payload=await response.json(); }catch{}

    if(response.status===429){
      setRefreshLabel('Scan already running…');
    }else if(!response.ok){
      const message=payload?.message || payload?.error || `HTTP ${response.status}`;
      throw new Error(message);
    }else{
      setRefreshLabel('Scanning banks…');
    }

    const updated=await waitForNewSnapshot(previousGeneratedAt);
    if(updated){
      setRefreshLabel('Scan complete ✓',2200);
    }else{
      setRefreshLabel('Scan still pending',2600);
      $('#alert').textContent='The scan was triggered, but a newer deployed snapshot did not appear within 4 minutes. Check GitHub Actions for the workflow status.';
      $('#alert').classList.remove('hidden');
    }
  }catch(e){
    setRefreshLabel('Rescan failed',2400);
    $('#alert').textContent=`Could not start a rate rescan: ${e.message}`;
    $('#alert').classList.remove('hidden');
  }finally{
    button.disabled=false;
    button.removeAttribute('aria-busy');
  }
}

$('#refresh').addEventListener('click',rescanRates);
$('#onlyPrimary').addEventListener('change',render);
$('#sberPct').addEventListener('change',render);
load();
