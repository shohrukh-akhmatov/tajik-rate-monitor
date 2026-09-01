from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
TZ=ZoneInfo('Asia/Dushanbe'); RESULTS=Path('site/results.json'); RULES=Path('config/rate_rules.json'); PUBLIC_RESULTS='https://shohrukh-akhmatov.github.io/tajik-rate-monitor/results.json'; PUBLIC_CALCULATED='https://shohrukh-akhmatov.github.io/tajik-rate-monitor/calculated_rates.json'
def now_iso(): return datetime.now(TZ).isoformat(timespec='seconds')
def pct_change(old,new):
    if old in (None,0) or new is None:return None
    return abs(float(new)-float(old))/abs(float(old))*100
def fetch_json(url):
    try:
        r=requests.get(url,params={'t':int(datetime.now().timestamp())},timeout=12,headers={'Cache-Control':'no-cache','User-Agent':'TajikRateMonitor/2.0'}); return r.json() if r.ok else None
    except Exception:return None
def valid_rub(v):
    try:return .05<=float(v)<=.20
    except:return False
def main():
    payload=json.loads(RESULTS.read_text(encoding='utf-8')); rules=json.loads(RULES.read_text(encoding='utf-8')); banks={b['id']:b for b in payload.get('banks',[])}; previous=fetch_json(PUBLIC_RESULTS) or {}; previous_calculated=fetch_json(PUBLIC_CALCULATED) or {}; reference=payload.get('reference_rates') or {}; previous_ref=previous.get('reference_rates',{}); old_banks={b.get('id'):b for b in previous.get('banks',[])}
    alif=((reference.get('alif_api') or {}).get('rates') or {}).get('RUB') or {}; api_base=alif.get('buy'); alif_bank=banks.get('alif',{}); tr=((alif_bank.get('rates') or {}).get('transfer') or {}); obs=tr.get('buy_per_1000'); obs_base=float(obs)/1000 if obs is not None else None
    old_rates=previous_calculated.get('rates',[]); last_valid=None
    for row in old_rates:
        if row.get('currency_code')=='RUB' and row.get('bank_code') in {'alif','ibt','spitamen','vasl'} and valid_rub(row.get('base_rate')): last_valid=float(row['base_rate']); break
    if valid_rub(api_base): alif_base=float(api_base); alif_source='alif_api'
    elif valid_rub(obs_base): alif_base=obs_base; alif_source='alif_transfer_observation'
    elif valid_rub(last_valid): alif_base=last_valid; alif_source='last_valid_rub_base'
    else: alif_base=None; alif_source='missing'
    rows=[]; anomalies=[]; generated=payload.get('generated_at') or now_iso(); fallback=set(rules['base_rate_policy']['fallback_for']); ar=rules['anomaly_rules']
    for service_slug,service in rules['services'].items():
        for bank_code,coef in service.get('coefficients',{}).items():
            bank=banks.get(bank_code,{}); transfer=(bank.get('rates') or {}).get('transfer') or {}; direct=transfer.get('buy_per_1000'); use_alif=bank_code in fallback
            if use_alif: base=alif_base; src_bank='alif'; src_kind=alif_source
            elif direct is not None and valid_rub(float(direct)/1000): base=float(direct)/1000; src_bank=bank_code; src_kind='bank_transfer_observation'
            else:
                old=old_banks.get(bank_code,{}); ob=((old.get('rates') or {}).get('transfer') or {}).get('buy_per_1000'); base=float(ob)/1000 if ob is not None and valid_rub(float(ob)/1000) else None; src_bank=bank_code; src_kind='last_valid_bank_transfer' if base is not None else 'missing'
            if base is None:
                anomalies.append({'service_slug':service_slug,'bank_code':bank_code,'code':'MISSING_BASE','message':'No usable base after direct, Alif API, Alif observation and last-valid fallbacks.'}); continue
            old_base=None
            if use_alif: old_base=((previous_ref.get('alif_api') or {}).get('rates') or {}).get('RUB',{}).get('buy'); old_base=old_base if valid_rub(old_base) else last_valid
            change=pct_change(old_base,base); code=None; msg=None
            if change is not None and change>ar['max_rub_base_change_pct'] and src_kind not in {'last_valid_bank_transfer','last_valid_rub_base'}: code='BASE_JUMP'; msg=f'Base rate changed by {change:.2f}%'; anomalies.append({'service_slug':service_slug,'bank_code':bank_code,'code':code,'message':msg})
            raw=base*float(coef); stale=src_kind in {'last_valid_bank_transfer','last_valid_rub_base'}
            rows.append({'service_slug':service_slug,'bank_code':bank_code,'bank_name':bank.get('name',bank_code),'currency_code':'RUB','base_rate':base,'base_source_bank_code':src_bank,'base_source_kind':src_kind,'coefficient':float(coef),'raw_calculated_rate':raw,'final_rate':round(raw,rules['rounding']['published_rate_decimals']),'sample_source_amount':rules['rounding']['sample_source_amount'],'sample_target_amount':round(raw*rules['rounding']['sample_source_amount'],4),'status':'stale' if stale else ('anomaly' if code else 'ok'),'anomaly_code':code,'anomaly_message':msg,'source_observed_at':generated})
    nbt=reference.get('nbt') or {}
    for currency in ('RUB','USD','EUR'):
        item=(nbt.get('rates') or {}).get(currency)
        if not item: continue
        value=float(item['rate']); bounds=(ar['min_nbt_rub'],ar['max_nbt_rub']) if currency=='RUB' else ((ar['min_nbt_usd'],ar['max_nbt_usd']) if currency=='USD' else (ar['min_nbt_eur'],ar['max_nbt_eur'])); bad=not bounds[0]<=value<=bounds[1]
        if bad: anomalies.append({'service_slug':'nbt-reference','bank_code':'nbt','code':'NBT_OUTLIER','message':f'{currency} NBT value {value} outside configured bounds'})
        rows.append({'service_slug':'nbt-reference','bank_code':'nbt','bank_name':'National Bank of Tajikistan','currency_code':currency,'base_rate':value,'base_source_bank_code':'nbt','base_source_kind':'official_nbt','coefficient':1.0,'raw_calculated_rate':value,'final_rate':value,'sample_source_amount':item.get('nominal') or 1,'sample_target_amount':value,'status':'anomaly' if bad else 'ok','anomaly_code':'NBT_OUTLIER' if bad else None,'anomaly_message':None,'source_observed_at':item.get('date')})
    commercial=(nbt.get('commercial_banks') or {})
    for bank_code,bd in commercial.items():
        for currency in ('USD','EUR'):
            q=(bd.get(currency) or {}).get('card_buy')
            if q is None: continue
            value=float(q); rows.append({'service_slug':'bank-card','bank_code':bank_code,'bank_name':bd.get('name',bank_code),'currency_code':currency,'base_rate':value,'base_source_bank_code':'nbt','base_source_kind':'nbt_commercial_bank_card_buy','coefficient':1.0,'raw_calculated_rate':value,'final_rate':value,'sample_source_amount':1,'sample_target_amount':value,'status':'ok','anomaly_code':None,'anomaly_message':None,'source_observed_at':bd.get('date') or nbt.get('updated_at')})
    out={'generated_at':generated,'rules_version':rules['version'],'anomaly_count':len(anomalies),'anomalies':anomalies,'rates':rows,'nbt_status':nbt.get('status'),'nbt_stale':bool(nbt.get('stale')),'nbt_stale_age_days':nbt.get('stale_age_days'),'alif_fallback_rub':alif_base,'alif_fallback_source':alif_source}; Path('site/calculated_rates.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); Path('site/anomalies.json').write_text(json.dumps({'generated_at':generated,'anomalies':anomalies},ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'rows':len(rows),'anomalies':len(anomalies),'alif_fallback_rub':alif_base,'alif_fallback_source':alif_source},ensure_ascii=False))
if __name__=='__main__': main()
