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
 # Use Anki search semantics for due/overdue. The raw `due` field has
 # different meanings for new, learning, and review cards, so comparing it
 # directly to a Unix-day ordinal can falsely mark recently studied cards overdue.
 # Use Anki search semantics for due/overdue. The raw `due` field has
 # different meanings for new, learning, and review cards. Query every
 # configured root so the combined dashboard includes both main decks.
 due_ids=set(); overdue_ids=set()
 for r in ROOTS:
  due_ids.update(anki("findCards",{"query":f'deck:"{r}" is:due -is:new'}) or[])
  overdue_ids.update(anki("findCards",{"query":f'deck:"{r}" is:review prop:due<=-1'}) or[])
 due=sum(1 for cid in ids if cid in due_ids)
 overdue=sum(1 for cid in ids if cid in overdue_ids)
 return{"total":len(cards),"seen":seen,"unseen":len(cards)-seen,"new":st["new"],"learning":st["learning"],"relearning":st["relearning"],"young":st["young"],"mature":st["mature"],"suspended":st["suspended"],"buried":st["buried"],"due":due,"overdue":overdue,"reviews_30d":reviews,"again_rate":again/reviews*100 if reviews else None,"time_ms":tm}
overall=metrics([c for r in ROOTS for c in grouped[r]]);decks=[{"name":r,**metrics(grouped[r])} for r in ROOTS]
rows=anki("getNumCardsReviewedByDay") or[]
# AnkiConnect returns [[dateString, count], ...]. Be tolerant of dict-shaped
# responses from older/alternate implementations as well.
lookup={}
if isinstance(rows,dict):
 for k,v in rows.items():
  try:lookup[str(k)[:10]]=int(v or 0)
  except (TypeError,ValueError):pass
elif isinstance(rows,list):
 for x in rows:
  if isinstance(x,(list,tuple)) and len(x)>=2:
   try:lookup[str(x[0])[:10]]=int(x[1] or 0)
   except (TypeError,ValueError):pass
  elif isinstance(x,dict):
   try:lookup[str(x.get("date")or x.get("day")or "")[:10]]=int(x.get("count",x.get("reviews",0))or 0)
   except (TypeError,ValueError):pass
today=datetime.now().astimezone().date()
activity=[{"date":(today-timedelta(days=i)).isoformat(),"count":lookup.get((today-timedelta(days=i)).isoformat(),0)} for i in range(13,-1,-1)]
# Use the full returned history for 7/30-day review totals and active days.
# The visible chart remains 14 days.
reviews_7d=sum(lookup.get((today-timedelta(days=i)).isoformat(),0) for i in range(7))
reviews_30d_byday=sum(lookup.get((today-timedelta(days=i)).isoformat(),0) for i in range(30))
active_days_30d=sum(lookup.get((today-timedelta(days=i)).isoformat(),0)>0 for i in range(30))
def introduced(days):
 s=set()
 for r in ROOTS:s.update(anki("findCards",{"query":f'deck:"{r}" introduced:{days}'})or[])
 return len(s)
new7,new30=introduced(7),introduced(30)
# Study pace is based on new cards introduced in the last 7 days, which
# better reflects current study behaviour than averaging over 30 days.
pace=new7/7 if new7 else 0
stats={"generated_at":datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),"overall":{**overall,"reviews_today":int(anki("getNumCardsReviewedToday")or 0),"reviews_7d":reviews_7d,"reviews_30d":reviews_30d_byday,"active_days_30d":active_days_30d,"new_7d":new7,"new_30d":new30,"new_per_day_7d":pace if new7 else None,"new_per_day_30d":new30/30 if new30 else None,"eta_days":overall["unseen"]/pace if pace else None},"activity":activity,"decks":decks,"roots":ROOTS}
github_put("data/stats.json",json.dumps(stats,indent=2))
assigned=sum(len(v) for v in grouped.values());print(f"Synced {assigned} cards across {len(ROOTS)} main decks.");print(f"Progress: {overall['seen']/overall['total']*100 if overall['total'] else 0:.1f}% | unseen: {overall['unseen']} | reviews 30d: {overall['reviews_30d']}")
