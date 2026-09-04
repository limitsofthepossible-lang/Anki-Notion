import base64, datetime, json, os, time, urllib.error, urllib.request
from collections import defaultdict
from pathlib import Path

ANKI_URL = "http://127.0.0.1:8765"
ROOT = "My ANZCA primary"
OUT = Path(__file__).with_name("data") / "stats.json"
BATCH = 400

def anki(action, params=None):
    payload={"action":action,"version":6}
    if params is not None: payload["params"]=params
    req=urllib.request.Request(ANKI_URL,data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=30) as r:
        obj=json.loads(r.read().decode("utf-8"))
    if obj.get("error"):
        raise RuntimeError(f"AnkiConnect {action}: {obj['error']}")
    return obj["result"]

def chunks(seq,n=BATCH):
    for i in range(0,len(seq),n):
        yield seq[i:i+n]

def github_put(path,raw,message):
    token=os.environ.get("GITHUB_TOKEN")
    repo=os.environ.get("GITHUB_REPO")
    if not token or not repo:
        raise RuntimeError("GITHUB_TOKEN or GITHUB_REPO is not set.")
    url=f"https://api.github.com/repos/{repo}/contents/{path}"
    headers={"Authorization":f"Bearer {token}","Accept":"application/vnd.github+json",
             "X-GitHub-Api-Version":"2022-11-28","Content-Type":"application/json"}
    sha=None
    try:
        req=urllib.request.Request(url,headers=headers)
        with urllib.request.urlopen(req,timeout=20) as r: sha=json.loads(r.read().decode())["sha"]
    except urllib.error.HTTPError as e:
        if e.code!=404: raise
    body={"message":message,"content":base64.b64encode(raw).decode()}
    if sha: body["sha"]=sha
    req=urllib.request.Request(url,data=json.dumps(body).encode(),headers=headers,method="PUT")
    with urllib.request.urlopen(req,timeout=30): pass

def state(c):
    q=int(c.get("queue",0)); typ=int(c.get("type",0)); ivl=int(c.get("interval",0) or 0)
    if q==-1: return "suspended"
    if q==-2: return "buried"
    if typ==0 and q==0: return "new"
    if q==1: return "learning"
    if q==3: return "relearning"
    if q==2: return "mature" if ivl>=21 else "young"
    if typ==2: return "young"
    return "new"

def aggregate(cards):
    s=defaultdict(int)
    for c in cards:
        s["total"]+=1
        st=state(c); s[st]+=1
        if int(c.get("reps",0) or 0)>0: s["seen"]+=1
        if int(c.get("lapses",0) or 0)>0: s["lapsed"]+=1
    return s

def main():
    names=anki("deckNames")
    decks=sorted([n for n in names if n==ROOT or n.startswith(ROOT+"::")],
                 key=lambda x:(x.count("::"),x.lower()))
    if ROOT not in decks: raise RuntimeError(f'Could not find "{ROOT}".')

    # Anki search deck:ROOT includes all descendants.
    ids=anki("findCards",{"query":f'deck:"{ROOT}"'})
    cards=[]
    for b in chunks(ids): cards.extend(anki("cardsInfo",{"cards":b}))
    cards=[c for c in cards if c]
    own=defaultdict(list)
    for c in cards: own[c.get("deckName",ROOT)].append(c)

    # Fetch review history once. getReviewsOfCards is read-only and returns the revlog
    # records for each card; we then aggregate by the card's current deck.
    histories={}
    for b in chunks([c["cardId"] for c in cards]):
        res=anki("getReviewsOfCards",{"cards":b})
        histories.update({str(k):v for k,v in res.items()})

    now=datetime.datetime.now().astimezone()
    cutoff30=now-datetime.timedelta(days=30)

    # Authoritative Anki-local calendar history. Do NOT mix these dates with UTC dates.
    activity30={}
    for ds,count in anki("getNumCardsReviewedByDay"):
        try:
            d=datetime.date.fromisoformat(ds)
            if now.date()-datetime.timedelta(days=29) <= d <= now.date():
                activity30[ds]=int(count)
        except Exception:
            continue

    def scope_cards(deck):
        prefix=deck+"::"
        return [c for actual,cs in own.items() if actual==deck or actual.startswith(prefix) for c in cs]

    def review_metrics(deck):
        reviews=again=time_ms=0
        for c in scope_cards(deck):
            for r in histories.get(str(c["cardId"]),[]):
                rid=int(r.get("id",0))
                dt=datetime.datetime.fromtimestamp(rid/1000,datetime.timezone.utc).astimezone()
                if dt>=cutoff30:
                    reviews+=1
                    again+=1 if int(r.get("ease",0))==1 else 0
                    time_ms+=int(r.get("time",0) or 0)
        return reviews,again,time_ms

    rows=[]
    for d in decks:
        s=aggregate(scope_cards(d))
        reviews,again,time_ms=review_metrics(d)
        due=len(anki("findCards",{"query":f'deck:"{d}" is:due'}))
        overdue=len(anki("findCards",{"query":f'deck:"{d}" is:review prop:due<=-1'}))
        total=s["total"]; seen=s["seen"]
        rows.append({
            "name":d,
            "display_name":ROOT if d==ROOT else d[len(ROOT)+2:],
            "level":d.count("::"),
            "parent":None if d==ROOT else d.rsplit("::",1)[0],
            "total":total,"seen":seen,"unseen":max(0,total-seen),
            "progress":round(seen/total*100,1) if total else 100,
            "new":s["new"],"learning":s["learning"],"relearning":s["relearning"],
            "young":s["young"],"mature":s["mature"],"suspended":s["suspended"],"buried":s["buried"],
            "lapsed":s["lapsed"],"due":due,"overdue":overdue,
            "reviews_30d":reviews,"again_30d":again,
            "again_rate_30d":round(again/reviews*100,1) if reviews else None,
            "time_min_30d":round(time_ms/60000,1)
        })

    root=next(r for r in rows if r["name"]==ROOT)
    activity=[{"date":d,"count":activity30[d]} for d in sorted(activity30)]
    today=int(anki("getNumCardsReviewedToday"))
    last7=sum(x["count"] for x in activity[-7:])
    last30=sum(x["count"] for x in activity)
    active_days=sum(x["count"]>0 for x in activity)
    avg_reviews=round(last30/active_days,1) if active_days else 0

    # "introduced" is Anki's first-answer search, not a count of current new cards.
    introduced7=len(anki("findCards",{"query":f'deck:"{ROOT}" introduced:7'}))
    introduced30=len(anki("findCards",{"query":f'deck:"{ROOT}" introduced:30'}))
    pace=introduced30/30
    eta=round(root["unseen"]/pace,1) if pace>0 else None

    data={
        "updated_local":now.strftime("%Y-%m-%d %H:%M"),
        "root":ROOT,"overall":root,"today":today,"last7":last7,"last30":last30,
        "active_days_30":active_days,"avg_reviews_active_day_30":avg_reviews,
        "introduced7":introduced7,"introduced30":introduced30,
        "new_cards_per_day_30":round(pace,1),"eta_days_unseen":eta,
        "activity":activity,"decks":rows
    }
    raw=json.dumps(data,indent=2).encode("utf-8")
    OUT.parent.mkdir(exist_ok=True); OUT.write_bytes(raw)
    github_put("data/stats.json",raw,"Update Anki statistics")
    print(f"Synced {len(cards)} cards across {len(decks)} decks.")
    print(f"Progress: {root['progress']}% | unseen: {root['unseen']} | reviews 30d: {last30}")

if __name__=="__main__":
    try: main()
    except Exception as e:
        print("ERROR:",e); raise
