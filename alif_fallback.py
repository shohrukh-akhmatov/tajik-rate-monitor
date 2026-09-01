from __future__ import annotations
import json
from pathlib import Path
import requests

RESULTS=Path('site/results.json')
URLS=('https://api.alif.tj/api/rates','https://alif.tj/api/rates')
BOUNDS={'RUB':(0.05,0.20),'USD':(5.0,20.0),'EUR':(7.0,20.0)}

def scan(v,out):
    if isinstance(v,dict):
        code=None
        for k in ('currency','currencyCode','currency_code','code','ccy','symbol','ticker'):
            x=v.get(k)
            if isinstance(x,str) and x.upper() in BOUNDS: code=x.upper(); break
        if code:
            buy=sell=None
            for k,x in v.items():
                if not isinstance(x,(int,float,str)): continue
                try: n=float(str(x).replace(',','.'))
                except ValueError: continue
                lo,hi=BOUNDS[code]
                if not lo<=n<=hi: continue
                k=k.lower()
                if any(w in k for w in ('buy','purchase','bid','pokup','buying')): buy=n
                if any(w in k for w in ('sell','sale','ask','prodaj','selling')): sell=n
            if buy is not None or sell is not None: out[code]={'buy':buy,'sell':sell}
        for x in v.values(): scan(x,out)
    elif isinstance(v,list):
        for x in v: scan(x,out)

def main():
    payload=json.loads(RESULTS.read_text(encoding='utf-8'))
    errors=[]
    for url in URLS:
        try:
            r=requests.get(url,timeout=20,headers={'User-Agent':'TajikRateMonitor/1.5','Accept':'application/json'}); r.raise_for_status()
            found={}; scan(r.json(),found)
            if found.get('RUB',{}).get('buy') is not None:
                payload.setdefault('reference_rates',{})['alif_api']={'source':url,'status':'ok','rates':found}
                RESULTS.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({'status':'ok','source':url,'rates':found},ensure_ascii=False)); return
            errors.append(f'{url}: no RUB buy quote recognized')
        except Exception as e: errors.append(f'{url}: {type(e).__name__}: {e}')
    payload.setdefault('reference_rates',{})['alif_api']={'source':URLS[0],'status':'error','rates':{},'error':'; '.join(errors)}
    RESULTS.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'error','errors':errors},ensure_ascii=False))

if __name__=='__main__': main()
