const $ = s => document.querySelector(s);
const banksEl = $('#banks');
const fmt = (n,d=2) => n == null ? '—' : Number(n).toFixed(d);
const rateKey = id => `tajik-rate-monitor:observed:${id}`;
const statusText = s => ({ok:'LIVE',partial:'PARTIAL',stale:'STALE',error:'ERROR',no_rate:'NO RATE'})[s] || String(s||'').toUpperCase();
const suitabilityText = s => ({direct_candidate:'DIRECT CANDIDATE',verify_app:'VERIFY APP',experimental:'EXPERIMENTAL',wrong_rate_class:'WRONG RATE CLASS',unsupported:'UNSUPPORTED'})[s] || s;
let data = null;
let historyData = [];

function primaryRate(bank){
  const key = bank.primary_category;
  if(key && bank.rates?.[key]?.buy_per_1000 != null) return bank.rates[key].buy_per_1000;
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
    const rows = Object.entries(bank.rates||{}).map(([key,r])=>`
      <div class="rate-row ${emphasize && key===bank.primary_category?'primary':''}">
        <div class="label">${r.label || key}${key===bank.primary_category?' ★':''}</div>
        <div class="value"><strong>${fmt(r.buy_per_1000)}</strong><span>BUY / 1,000 RUB</span></div>
        <div class="value"><strong>${fmt(r.sell_per_1000)}</strong><span>SELL / 1,000 RUB</span></div>
      </div>`).join('') || `<div class="rate-row"><div class="label">No safe rate extracted</div></div>`;

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
          <input class="observed" inputmode="decimal" placeholder="Observed app rate / 1000" value="${observed||''}" data-bank="${bank.id}">
          <div class="delta ${delta==null?'':Math.abs(delta)<=0.05?'good':'bad'}">Web − app<br><strong>${delta==null?'—':(delta>=0?'+':'')+fmt(delta)}</strong></div>
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
  const items = historyData.slice(-30).reverse();
  $('#historyCount').textContent = `${historyData.length} stored changes`;
  list.innerHTML = items.map(h=>{
    const vals = Object.entries(h.rates||{}).map(([k,v])=>`${k}: ${v.buy==null?'—':fmt(v.buy*1000)}`).join(' · ');
    return `<div class="history-item"><div class="time">${new Date(h.at).toLocaleString()}</div><strong>${h.name}</strong><div class="change">${vals}</div></div>`;
  }).join('') || '<p class="sub">History begins after the first successful deployed collection.</p>';
}
async function load(){
  $('#refresh').disabled=true;
  try{
    const t=Date.now();
    const [r,h]=await Promise.all([fetch(`results.json?t=${t}`,{cache:'no-store'}),fetch(`history.json?t=${t}`,{cache:'no-store'})]);
    if(!r.ok) throw new Error(`results.json HTTP ${r.status}`);
    data=await r.json(); historyData=h.ok?await h.json():[];
    $('#alert').classList.add('hidden'); render();
  }catch(e){ $('#alert').textContent=`Could not load collector results: ${e.message}`; $('#alert').classList.remove('hidden'); }
  finally{$('#refresh').disabled=false;}
}
$('#refresh').addEventListener('click',load);
$('#onlyPrimary').addEventListener('change',render);
$('#sberPct').addEventListener('change',render);
load();
