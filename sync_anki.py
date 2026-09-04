import os, json, base64, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from collections import defaultdict

ANKI_URL = "http://127.0.0.1:8765"
REPO = os.environ.get("GITHUB_REPO")
TOKEN = os.environ.get("GITHUB_TOKEN")
ROOTS = [x.strip() for x in os.environ.get("ANKI_ROOT_DECKS", "My ANZCA primary;Pharmacology").split(";") if x.strip()]

if not REPO or not TOKEN:
    raise SystemExit("Set GITHUB_REPO and GITHUB_TOKEN first.")

def anki(action, params=None):
    payload=json.dumps({"action":action,"version":6,"params":params or {}}).encode()
    req=urllib.request.Request(ANKI_URL,data=payload,headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=60) as r: out=json.loads(r.read().decode())
    if out.get("error"): raise RuntimeError(f"AnkiConnect {action}: {out['error']}")
    return out["result"]

def batches(xs,n=400):
    for i in range(0,len(xs),n): yield xs[i:i+n]

def github_put(path, content):
    url=f"https://api.github.com/repos/{REPO}/contents/{path}"
    headers={"Authorization":f"Bearer {TOKEN}","Accept":"application/vnd.github+json","User-Agent":"anki-dashboard"}
    sha=None
    try:
        req=urllib.request.Request(url,headers=headers)
        with urllib.request.urlopen(req,timeout=30) as r: sha=json.loads(r.read().decode()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404: raise
    body={"message":"Update Anki dashboard stats","content":base64.b64encode(content.encode()).decode()}
    if sha: body["sha"]=sha
    req=urllib.request.Request(url,data=json.dumps(body).encode(),headers={**headers,"Content-Type":"application/json"},method="PUT")
    with urllib.request.urlopen(req,timeout=30) as r: return json.loads(r.read().decode())

def card_ids_for_deck(deck):
    # Anki's deck:NAME search includes descendants.
    return anki("findCards",{"query":f'deck:"{deck}"'}) or []

def classify(c):
    q=int(c.get("queue",0) or 0); typ=int(c.get("type",0) or 0); interval=int(c.get("interval",0) or 0)
    if q == -1: return "suspended"
    if q == -2: return "buried"
    if typ == 0 and q == 0: return "new"
    if q == 1: return "learning"
    if q == 3: return "relearning"
    if q == 2: return "mature" if interval >= 21 else "young"
    return "new" if int(c.get("reps",0) or 0)==0 else "young"

def review_stats(ids):
    cutoff=int((datetime.now(timezone.utc)-timedelta(days=30)).timestamp()*1000)
    total=again=time_ms=0; days=defaultdict(int)
    for batch in batches(ids):
        result=anki("getReviewsOfCards",{"cards":batch}) or {}
        rows=[row for vals in result.values() for row in (vals or [])] if isinstance(result,dict) else []
        for row in rows:
            rid=int(row.get("id",0) or 0)
            if rid < cutoff: continue
            total+=1; again += int(row.get("ease",0) or 0)==1; time_ms += int(row.get("time",0) or 0)
            local=datetime.fromtimestamp(rid/1000,timezone.utc).astimezone()
            days[local.date().isoformat()]+=1
    return total,again,time_ms,days

all_ids=set()
for root in ROOTS:
    all_ids.update(card_ids_for_deck(root))

infos=[]
for batch in batches(sorted(all_ids)):
    infos.extend(anki("cardsInfo",{"cards":batch}) or [])
infos=list({int(c["cardId"]):c for c in infos}.values())

# Map cards to their top-level root. A card belongs to whichever configured root
# is an ancestor of its actual deckName. Cards not under a configured root are ignored.
def root_for(card):
    name=card.get("deckName","")
    matches=[r for r in ROOTS if name==r or name.startswith(r+"::")]
    return max(matches,key=len) if matches else None

grouped={r:[] for r in ROOTS}
for c in infos:
    r=root_for(c)
    if r: grouped[r].append(c)

def metrics(cards):
    ids=[int(c["cardId"]) for c in cards]; total=len(cards); seen=sum(int(c.get("reps",0) or 0)>0 for c in cards)
    states=defaultdict(int)
    for c in cards: states[classify(c)]+=1
    rs=review_stats(ids) if ids else (0,0,0,{})
    reviews,again,time_ms,_=rs
    due=len(set(anki("areDue",{"cards":ids}) or [])) if ids else 0
    # Review-card due values are in days since Anki's epoch. Today is the local
    # calendar day represented by today's day-start ordinal.
    today_ordinal=(datetime.now().astimezone().date()-datetime(1970,1,1).date()).days
    overdue=sum(1 for c in cards if int(c.get("type",0) or 0)==2 and int(c.get("due",0) or 0)<today_ordinal)
    return {
      "total":total,"seen":seen,"unseen":total-seen,"new":states["new"],"learning":states["learning"],
      "relearning":states["relearning"],"young":states["young"],"mature":states["mature"],
      "suspended":states["suspended"],"buried":states["buried"],"due":due,"overdue":overdue,
      "reviews_30d":reviews,"again_rate":again/reviews*100 if reviews else None,"time_ms":time_ms
    }

overall_cards=[c for root in ROOTS for c in grouped[root]]
overall=metrics(overall_cards)

# Deck table: one row per configured MAIN deck, with all of its subdecks included.
deck_rows=[]
known=anki("deckNames") or []
for root in ROOTS:
    names={root}
    names.update(n for n in known if n.startswith(root+"::"))
    # Only display main deck rows; subdeck information is intentionally collapsed.
    m=metrics(grouped[root])
    deck_rows.append({"name":root,**m})

# Anki's own day-level endpoint is the source for the activity chart.
day_rows=anki("getNumCardsReviewedByDay") or []
today=datetime.now().astimezone().date()
activity=[]
lookup={}
for row in day_rows:
    if isinstance(row,dict):
        key=str(row.get("date") or row.get("day") or "")[:10]
        lookup[key]=int(row.get("count",row.get("reviews",0)) or 0)
for i in range(13,-1,-1):
    d=today-timedelta(days=i); activity.append({"date":d.isoformat(),"count":lookup.get(d.isoformat(),0)})

reviews_today=int(anki("getNumCardsReviewedToday") or 0)
reviews_7d=sum(x["count"] for x in activity[-7:])
active_days=sum(x["count"]>0 for x in activity)

def introduced(days):
    found=set()
    for root in ROOTS:
        found.update(anki("findCards",{"query":f'deck:"{root}" introduced:{days}'}) or [])
    return len(found)

new7=introduced(7); new30=introduced(30)
pace=new30/30 if new30 else 0
eta=overall["unseen"]/pace if pace else None

stats={
 "generated_at":datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
 "overall":{**overall,"reviews_today":reviews_today,"reviews_7d":reviews_7d,
            "active_days_30d":active_days,"new_7d":new7,"new_30d":new30,
            "new_per_day_30d":pace if new30 else None,"eta_days":eta},
 "activity":activity,
 "decks":deck_rows,
 "roots":ROOTS
}
github_put("data/stats.json",json.dumps(stats,indent=2))
print(f"Synced {len(infos)} cards across {len(ROOTS)} main decks.")
print(f"Progress: {overall['seen']/overall['total']*100 if overall['total'] else 0:.1f}% | unseen: {overall['unseen']} | reviews 30d: {overall['reviews_30d']}")
