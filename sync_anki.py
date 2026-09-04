import os,json,base64,urllib.request,urllib.error
from datetime import datetime,timezone,timedelta
from collections import defaultdict
ANKI_URL="http://127.0.0.1:8765";REPO=os.environ.get("GITHUB_REPO");TOKEN=os.environ.get("GITHUB_TOKEN")
ROOTS=[x.strip() for x in os.environ.get("ANKI_ROOT_DECKS","ANZCA Primary;Pharmacology").split(";") if x.strip()]
if not REPO or not TOKEN: raise SystemExit("Set GITHUB_REPO and GITHUB_TOKEN first.")
def anki(action,params=None):
 req=urllib.request.Request(ANKI_URL,data=json.dumps({"action":action,"version":6,"params":params or {}}).encode(),headers={"Content-Type":"application/json"})
 with urllib.request.urlopen(req,timeout=60) as r:o=json.loads(r.read().decode())
 if o.get("error"):raise RuntimeError(f"AnkiConnect {action}: {o['error']}")
 return o["result"]
def batches(xs,n=400):
 for i in range(0,len(xs),n):yield xs[i:i+n]
def github_put(path,content):
 url=f"https://api.github.com/repos/{REPO}/contents/{path}";h={"Authorization":f"Bearer {TOKEN}","Accept":"application/vnd.github+json","User-Agent":"anki-dashboard"};sha=None
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers=h),timeout=30) as r:sha=json.loads(r.read().decode()).get("sha")
 except urllib.error.HTTPError as e:
  if e.code!=404:raise
 b={"message":"Update Anki dashboard stats","content":base64.b64encode(content.encode()).decode()}
 if sha:b["sha"]=sha
 req=urllib.request.Request(url,data=json.dumps(b).encode(),headers={**h,"Content-Type":"application/json"},method="PUT")
 with urllib.request.urlopen(req,timeout=30):pass
available=anki("deckNames") or []
missing=[r for r in ROOTS if not any(n.casefold()==r.casefold() or n.casefold().startswith((r+"::").casefold()) for n in available)]
if missing: raise SystemExit("Could not find configured deck(s): "+", ".join(missing))
all_ids=set()
for root in ROOTS:all_ids.update(anki("findCards",{"query":f'deck:"{root}"'}) or [])
infos=[]
for b in batches(sorted(all_ids)):infos.extend(anki("cardsInfo",{"cards":b}) or [])
infos=list({int(c["cardId"]):c for c in infos}.values())
def root_for(c):
 n=str(c.get("deckName",""));nl=n.casefold()
 hits=[r for r in ROOTS if nl==r.casefold() or nl.startswith((r+"::").casefold())]
 return max(hits,key=len) if hits else None
grouped={r:[] for r in ROOTS}
for c in infos:
 r=root_for(c)
 if r:grouped[r].append(c)
def classify(c):
 q=int(c.get("queue",0)or 0);t=int(c.get("type",0)or 0);iv=int(c.get("interval",0)or 0)
 if q==-1:return"suspended"
 if q==-2:return"buried"
 if t==0 and q==0:return"new"
 if q==1:return"learning"
 if q==3:return"relearning"
 if q==2:return"mature" if iv>=21 else"young"
 return"new" if int(c.get("reps",0)or 0)==0 else"young"
def review_stats(ids):
 cut=int((datetime.now(timezone.utc)-timedelta(days=30)).timestamp()*1000);tot=again=tm=0;days=defaultdict(int)
 for b in batches(ids):
  res=anki("getReviewsOfCards",{"cards":b}) or {}
  for vals in res.values():
   for x in(vals or []):
    rid=int(x.get("id",0)or 0)
    if rid<cut:continue
    tot+=1;again+=int(x.get("ease",0)or 0)==1;tm+=int(x.get("time",0)or 0);dt=datetime.fromtimestamp(rid/1000,timezone.utc).astimezone();days[dt.date().isoformat()]+=1
 return tot,again,tm,days
def metrics(cards):
 ids=[int(c["cardId"]) for c in cards];st=defaultdict(int)
 for c in cards:st[classify(c)]+=1
 seen=sum(int(c.get("reps",0)or 0)>0 for c in cards);reviews,again,tm,_=review_stats(ids) if ids else(0,0,0,{})
 due_ids=set(anki("areDue",{"cards":ids}) or[]) if ids else set();due=sum(1 for c in cards if int(c["cardId"]) in due_ids and classify(c)!="new")
 epoch=datetime(1970,1,1).date();ordtoday=(datetime.now().astimezone().date()-epoch).days
 overdue=sum(1 for c in cards if int(c.get("type",0)or 0)==2 and int(c.get("due",0)or 0)<ordtoday)
 return{"total":len(cards),"seen":seen,"unseen":len(cards)-seen,"new":st["new"],"learning":st["learning"],"relearning":st["relearning"],"young":st["young"],"mature":st["mature"],"suspended":st["suspended"],"buried":st["buried"],"due":due,"overdue":overdue,"reviews_30d":reviews,"again_rate":again/reviews*100 if reviews else None,"time_ms":tm}
overall=metrics([c for r in ROOTS for c in grouped[r]]);decks=[{"name":r,**metrics(grouped[r])} for r in ROOTS]
rows=anki("getNumCardsReviewedByDay") or[];lookup={}
for x in rows:
 if isinstance(x,dict):lookup[str(x.get("date")or x.get("day")or"")[:10]]=int(x.get("count",x.get("reviews",0))or 0)
today=datetime.now().astimezone().date();activity=[{"date":(today-timedelta(days=i)).isoformat(),"count":lookup.get((today-timedelta(days=i)).isoformat(),0)} for i in range(13,-1,-1)]
def introduced(days):
 s=set()
 for r in ROOTS:s.update(anki("findCards",{"query":f'deck:"{r}" introduced:{days}'})or[])
 return len(s)
new7,new30=introduced(7),introduced(30);pace=new30/30 if new30 else 0
stats={"generated_at":datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),"overall":{**overall,"reviews_today":int(anki("getNumCardsReviewedToday")or 0),"reviews_7d":sum(x["count"] for x in activity[-7:]),"active_days_30d":sum(x["count"]>0 for x in activity),"new_7d":new7,"new_30d":new30,"new_per_day_30d":pace if new30 else None,"eta_days":overall["unseen"]/pace if pace else None},"activity":activity,"decks":decks,"roots":ROOTS}
github_put("data/stats.json",json.dumps(stats,indent=2))
assigned=sum(len(v) for v in grouped.values());print(f"Synced {assigned} cards across {len(ROOTS)} main decks.");print(f"Progress: {overall['seen']/overall['total']*100 if overall['total'] else 0:.1f}% | unseen: {overall['unseen']} | reviews 30d: {overall['reviews_30d']}")
