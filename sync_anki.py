import os, json, base64, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from collections import defaultdict

ANKI_URL="http://127.0.0.1:8765"
REPO=os.environ.get("GITHUB_REPO")
TOKEN=os.environ.get("GITHUB_TOKEN")
ROOTS=[x.strip() for x in os.environ.get("ANKI_ROOT_DECKS","My ANZCA primary;Pharmacology").split(";") if x.strip()]
if not REPO or not TOKEN: raise SystemExit("Set GITHUB_REPO and GITHUB_TOKEN first.")

def anki(action,params=None):
    req=urllib.request.Request(ANKI_URL,data=json.dumps({"action":action,"version":6,"params":params or {}}).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=60) as r: out=json.loads(r.read().decode())
    if out.get("error"): raise RuntimeError(f"AnkiConnect {action}: {out['error']}")
    return out["result"]

def batches(xs,n=400):
    for i in range(0,len(xs),n): yield xs[i:i+n]

def github_put(path,content):
    url=f"https://api.github.com/repos/{REPO}/contents/{path}"
    headers={"Authorization":f"Bearer {TOKEN}","Accept":"application/vnd.github+json","User-Agent":"anki-dashboard"}
    sha=None
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=30) as r: sha=json.loads(r.read().decode()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code!=404: raise
    body={"message":"Update Anki dashboard stats","content":base64.b64encode(content.encode()).decode()}
    if sha: body["sha"]=sha
    req=urllib.request.Request(url,data=json.dumps(body).encode(),headers={**headers,"Content-Type":"application/json"},method="PUT")
    with urllib.request.urlopen(req,timeout=30) as r: return json.loads(r.read().decode())

def ids_for(root): return anki("findCards",{"query":f'deck:"{root}"'}) or []

all_ids=set()
for root in ROOTS: all_ids.update(ids_for(root))
infos=[]
for b in batches(sorted(all_ids)): infos.extend(anki("cardsInfo",{"cards":b}) or [])
infos=list({int(c["cardId"]):c for c in infos}.values())

def root_for(c):
    n=c.get("deckName","")
    hits=[r for r in ROOTS if n==r or n.startswith(r+"::")]
    return max(hits,key=len) if hits else None

grouped={r:[] for r in ROOTS}
for c in infos:
    r=root_for(c)
    if r: grouped[r].append(c)

def classify(c):
    q=int(c.get("queue",0) or 0); typ=int(c.get("type",0) or 0); interval=int(c.get("interval",0) or 0)
    if q==-1:return "suspended"
    if q==-2:return "buried"
    if typ==0 and q==0:return "new"
    if q==1:return "learning"
    if q==3:return "relearning"
    if q==2:return "mature" if interval>=21 else "young"
    return "new" if int(c.get("reps",0) or 0)==0 else "young"

def review_stats(ids):
    cutoff=int((datetime.now(timezone.utc)-timedelta(days=30)).timestamp()*1000)
    total=again=time_ms=0;days=defaultdict(int)
    for b in batches(ids):
        res=anki("getReviewsOfCards",{"cards":b}) or {}
        rows=[x for vals in res.values() for x in (vals or [])] if isinstance(res,dict) else []
        for x in rows:
            rid=int(x.get("id",0) or 0)
            if rid<cutoff:continue
            total+=1;again+=int(x.get("ease",0) or 0)==1;time_ms+=int(x.get("time",0) or 0)
            dt=datetime.fromtimestamp(rid/1000,timezone.utc).astimezone()
            days[dt.date().isoformat()]+=1
    return total,again,time_ms,days

def metrics(cards):
    ids=[int(c["cardId"]) for c in cards]; states=defaultdict(int)
    for c in cards: states[classify(c)]+=1
    seen=sum(int(c.get("reps",0) or 0)>0 for c in cards)
    reviews,again,time_ms,_=review_stats(ids) if ids else (0,0,0,{})
    # Due now deliberately excludes untouched new cards. It counts only learning/relearning
    # and review cards that Anki currently considers due.
    due_ids=set(anki("areDue",{"cards":ids}) or []) if ids else set()
    due=sum(1 for c in cards if int(c["cardId"]) in due_ids and classify(c)!="new")
    epoch=datetime(1970,1,1).date()
    today_ord=(datetime.now().astimezone().date()-epoch).days
    overdue=sum(1 for c in cards if int(c.get("type",0) or 0)==2 and int(c.get("due",0) or 0)<today_ord)
    return {"total":len(cards),"seen":seen,"unseen":len(cards)-seen,"new":states["new"],"learning":states["learning"],
      "relearning":states["relearning"],"young":states["young"],"mature":states["mature"],"suspended":states["suspended"],
      "buried":states["buried"],"due":due,"overdue":overdue,"reviews_30d":reviews,
      "again_rate":again/reviews*100 if reviews else None,"time_ms":time_ms}

overall=metrics([c for r in ROOTS for c in grouped[r]])
deck_rows=[{"name":r,**metrics(grouped[r])} for r in ROOTS]

day_rows=anki("getNumCardsReviewedByDay") or []; lookup={}
for x in day_rows:
    if isinstance(x,dict):
        k=str(x.get("date") or x.get("day") or "")[:10]
        lookup[k]=int(x.get("count",x.get("reviews",0)) or 0)
today=datetime.now().astimezone().date()
activity=[{"date":(today-timedelta(days=i)).isoformat(),"count":lookup.get((today-timedelta(days=i)).isoformat(),0)} for i in range(13,-1,-1)]

def introduced(days):
    found=set()
    for root in ROOTS: found.update(anki("findCards",{"query":f'deck:"{root}" introduced:{days}'}) or [])
    return len(found)

new7,new30=introduced(7),introduced(30)
pace=new30/30 if new30 else 0
stats={"generated_at":datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
 "overall":{**overall,"reviews_today":int(anki("getNumCardsReviewedToday") or 0),
 "reviews_7d":sum(x["count"] for x in activity[-7:]),"active_days_30d":sum(x["count"]>0 for x in activity),
 "new_7d":new7,"new_30d":new30,"new_per_day_30d":pace if new30 else None,
 "eta_days":overall["unseen"]/pace if pace else None},
 "activity":activity,"decks":deck_rows,"roots":ROOTS}
github_put("data/stats.json",json.dumps(stats,indent=2))
print(f"Synced {len(infos)} cards across {len(ROOTS)} main decks.")
print(f"Progress: {overall['seen']/overall['total']*100 if overall['total'] else 0:.1f}% | unseen: {overall['unseen']} | reviews 30d: {overall['reviews_30d']}")
