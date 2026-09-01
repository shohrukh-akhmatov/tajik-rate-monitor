from __future__ import annotations
import json
from pathlib import Path
import requests

RESULTS=Path('site/results.json')
URLS = ('https://alif.tj/api/rates',)
CURR_MAP = {'RUB': 'RUB', 'USD': 'USD', 'EUR': 'EUR', '810': 'RUB', '840': 'USD', '978': 'EUR'}

def num(v):
    try: return float(str(v).replace(',','.').replace(' ',''))
    except (TypeError,ValueError): return None

def scan(v,out):
    if isinstance(v,dict):
        code=None
        for k in ('name','currency','currencyCode','currency_code','code','ccy','symbol','ticker'):
            x=v.get(k)
            if isinstance(x,str) and str(x).upper() in CURR_MAP:
                code=CURR_MAP[str(x).upper()]; break
        if code:
            buy=sell=None
            preferred={
                'buy': ('moneyTransferBuyValue','money_transfer_buy_value','buyValue','buy_value','transferBuy','transfer_buy'),
                'sell': ('moneyTransferTradeValue','money_transfer_trade_value','sellValue','sell_value','transferSell','transfer_sell')}
            for typ,keys in preferred.items():
                for k in keys:
                    if k in v:
                        n=num(v[k])
                        if n is not None:
                            if typ=='buy': buy=n
                            else: sell=n
                            break
            if buy is None or sell is None:
                for k,x in v.items():
                    n=num(x)
                    if n is None: continue
                    kl=str(k).lower()
                    if buy is None and any(w in kl for w in ('buy','purchase','bid','pokup','buying')): buy=n
                    if sell is None and any(w in kl for w in ('sell','sale','ask','prodaj','selling')): sell=n
            if buy is not None or sell is not None: out[code]={'buy':buy,'sell':sell}
        for x in v.values(): scan(x,out)
    elif isinstance(v,list):
        for x in v: scan(x,out)

def main():
    payload=json.loads(RESULTS.read_text(encoding='utf-8'))
    errors=[]
    for url in URLS:
        try:
            r=requests.get(url,timeout=20,headers={'User-Agent':'TajikRateMonitor/2.0','Accept':'application/json'}); r.raise_for_status()
            found={}; scan(r.json(),found)
            if found.get('RUB',{}).get('buy') is not None:
                payload.setdefault('reference_rates',{})['alif_api']={'source':url,'status':'ok','rates':found,'rate_type':'money_transfer_buy'}
                RESULTS.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'status':'ok','source':url,'rates':found},ensure_ascii=False)); return
            errors.append(f'{url}: no RUB transfer buy quote recognized')
        except Exception as e: errors.append(f'{url}: {type(e).__name__}: {e}')
    payload.setdefault('reference_rates',{})['alif_api']={'source':URLS[0],'status':'error','rates':{},'error':'; '.join(errors)}
    RESULTS.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'status':'error','errors':errors},ensure_ascii=False))

if __name__=='__main__': main()
