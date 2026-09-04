import os, json, base64, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from collections import defaultdict

ANKI_URL = "http://127.0.0.1:8765"
REPO = os.environ.get("GITHUB_REPO")
TOKEN = os.environ.get("GITHUB_TOKEN")
ROOT = os.environ.get("ANKI_ROOT_DECK", "My ANZCA primary")

if not REPO or not TOKEN:
    raise SystemExit("Set GITHUB_REPO and GITHUB_TOKEN first.")

def anki(action, params=None):
    payload = json.dumps({"action": action, "version": 6, "params": params or {}}).encode()
    req = urllib.request.Request(ANKI_URL, data=payload, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read().decode())
    if out.get("error"):
        raise RuntimeError(f"AnkiConnect {action}: {out['error']}")
    return out["result"]

def github_put(path, content):
    url=f"https://api.github.com/repos/{REPO}/contents/{path}"
    headers={"Authorization":f"Bearer {TOKEN}","Accept":"application/vnd.github+json","User-Agent":"anki-dashboard"}
    # Get current SHA if the file already exists.
    sha=None
    req=urllib.request.Request(url,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            sha=json.loads(r.read().decode()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404: raise
    body={"message":"Update Anki dashboard stats","content":base64.b64encode(content.encode()).decode()}
    if sha: body["sha"]=sha
    req=urllib.request.Request(url,data=json.dumps(body).encode(),headers={**headers,"Content-Type":"application/json"},method="PUT")
    with urllib.request.urlopen(req,timeout=30) as r: return json.loads(r.read().decode())

def batches(xs,n=400):
    for i in range(0,len(xs),n): yield xs[i:i+n]

def classify(info):
    q=info.get("queue",0)
    typ=info.get("type",0)
    interval=info.get("interval",0) or 0
    # Anki queues: 0=new, 1=learning, 2=review, 3=relearning.
    if info.get("queue") == -1: return "suspended"
    if q == -2: return "buried"
    if typ == 0 and q == 0: return "new"
    if q == 1: return "learning"
    if q == 3: return "relearning"
    if q == 2: return "mature" if interval >= 21 else "young"
    return "new" if info.get("reps",0)==0 else "young"

def review_stats(card_ids):
    cutoff=int((datetime.now(timezone.utc)-timedelta(days=30)).timestamp()*1000)
    total=again=0; ms=0
    by_day=defaultdict(int)
    # getReviewsOfCards returns a dict keyed by card id in current AnkiConnect.
    for batch in batches(card_ids):
        res=anki("getReviewsOfCards",{"cards":batch})
        if isinstance(res,dict):
            rows=[r for vals in res.values() for r in (vals or [])]
        else:
            rows=res or []
        for r in rows:
            rid=int(r.get("id",0))
            if rid < cutoff: continue
            total += 1
            if int(r.get("ease",0)) == 1: again += 1
            ms += int(r.get("time",0) or 0)
            dt=datetime.fromtimestamp(rid/1000,timezone.utc).astimezone()
            by_day[dt.date().isoformat()] += 1
    return {"reviews_30d":total,"again":again,"time_ms":ms,"by_day":dict(by_day)}

def scope_ids(deck_name):
    # deck:ROOT includes descendants; quote the name for spaces/punctuation.
    return anki("findCards",{"query":f'deck:"{deck_name}"'})

# Get all cards below the root, then use each card's actual deckName.
card_ids=scope_ids(ROOT)
infos=[]
for batch in batches(card_ids):
    infos.extend(anki("cardsInfo",{"cards":batch}) or [])

# De-duplicate cards defensively.
by_id={int(x["cardId"]):x for x in infos}
infos=list(by_id.values())

# Actual decks represented by cards, plus all known nested decks.
names=anki("deckNames") or []
actual_names={x.get("deckName") for x in infos if x.get("deckName")}
known={n for n in names if n==ROOT or n.startswith(ROOT+"::")}
all_decks=sorted(known|actual_names, key=lambda n:(n.count("::"),n.lower()))

def descendants(name):
    prefix=name+"::"
    return [i for i in infos if i.get("deckName")==name or i.get("deckName","").startswith(prefix)]

def metrics(cards):
    total=len(cards)
    state=defaultdict(int)
    seen=0
    for c in cards:
        if int(c.get("reps",0) or 0)>0: seen+=1
        state[classify(c)]+=1
    ids=[int(c["cardId"]) for c in cards]
    rs=review_stats(ids) if ids else {"reviews_30d":0,"again":0,"time_ms":0,"by_day":{}}
    due=0
    if ids:
        due_ids=set(anki("areDue",{"cards":ids}) or [])
        due=len(due_ids)
    # "overdue" means review cards whose due date is before today.
    today_start=int(datetime.now().astimezone().replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
    overdue=sum(1 for c in cards if c.get("type")==2 and (c.get("due",0) or 0) < today_start/86400)
    return {
        "total":total,"seen":seen,"unseen":total-seen,
        "new":state["new"],"learning":state["learning"],"relearning":state["relearning"],
        "young":state["young"],"mature":state["mature"],"suspended":state["suspended"],
        "buried":state["buried"],"due":due,"overdue":overdue,
        "reviews_30d":rs["reviews_30d"],
        "again_rate":(rs["again"]/rs["reviews_30d"]*100) if rs["reviews_30d"] else None,
        "time_ms":rs["time_ms"]
    }

deck_rows=[]
for name in all_decks:
    cards=descendants(name)
    m=metrics(cards)
    # Display relative name for nested decks.
    display=name[len(ROOT+"::"):] if name.startswith(ROOT+"::") else name
    depth=name.count("::")-ROOT.count("::")
    deck_rows.append({"name":display,"full_name":name,"depth":max(0,depth),**m})

# Root overall metrics is exactly the root roll-up.
overall=metrics(infos)

# Activity: Anki's own day-level review endpoint is the source of truth for calendar days.
day_rows=anki("getNumCardsReviewedByDay") or []
today=datetime.now().astimezone().date()
activity=[]
for i in range(13,-1,-1):
    day=today-timedelta(days=i)
    ds=day.isoformat()
    count=0
    for row in day_rows:
        if isinstance(row,dict):
            k=row.get("date") or row.get("day")
            v=row.get("count",row.get("reviews",0))
            if k and str(k)[:10]==ds: count=int(v or 0)
    activity.append({"date":ds,"count":count})

# New cards introduced: use Anki search, which is based on introduction history.
def introduced(days):
    ids=anki("findCards",{"query":f'deck:"{ROOT}" introduced:{days}'}) or []
    return len(set(ids))

new7=introduced(7)
new30=introduced(30)

reviews_today=anki("getNumCardsReviewedToday") or 0
active_days=sum(1 for x in activity if x["count"]>0)
pace=new30/30 if new30 else 0
eta=(overall["unseen"]/pace) if pace>0 else None

# Use actual historical review activity to avoid mixing incompatible timestamp formats.
stats={
    "generated_at":datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
    "overall":{
        **overall,
        "reviews_today":int(reviews_today),
        "reviews_7d":sum(x["count"] for x in activity[-7:]),
        "active_days_30d":active_days,
        "new_7d":new7,
        "new_30d":new30,
        "new_per_day_30d":pace if new30 else None,
        "eta_days":eta
    },
    "activity":activity,
    "decks":deck_rows
}

content=json.dumps(stats,indent=2)
github_put("data/stats.json",content)
print(f"Synced {len(infos)} cards across {len(all_decks)} decks.")
print(f"Progress: {overall['seen']/overall['total']*100 if overall['total'] else 0:.1f}% | unseen: {overall['unseen']} | reviews 30d: {overall['reviews_30d']}")
