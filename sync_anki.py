import json, os, urllib.request, urllib.error, base64, datetime, time
from pathlib import Path

ANKI="http://127.0.0.1:8765"
ROOT="My ANZCA primary"
OUT=Path(__file__).with_name("data")/"stats.json"

def anki(action,params=None):
    p={"action":action,"version":6}
    if params is not None:p["params"]=params
    req=urllib.request.Request(ANKI,data=json.dumps(p).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r:x=json.loads(r.read().decode())
    if x.get("error"):raise RuntimeError(x["error"])
    return x["result"]

def chunks(xs,n=500):
    for i in range(0,len(xs),n):yield xs[i:i+n]

def github_put(path,content,message):
    token=os.environ.get("GITHUB_TOKEN");repo=os.environ.get("GITHUB_REPO")
    if not token or not repo:raise RuntimeError("GITHUB_TOKEN/GITHUB_REPO not set.")
    api=f"https://api.github.com/repos/{repo}/contents/{path}"
    h={"Authorization":f"Bearer {token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","Content-Type":"application/json"}
    try:
        req=urllib.request.Request(api,headers=h)
        with urllib.request.urlopen(req,timeout=20) as r:sha=json.loads(r.read().decode())["sha"]
    except urllib.error.HTTPError as e:
        if e.code==404:sha=None
        else:raise
    b={"message":message,"content":base64.b64encode(content).decode()}
    if sha:b["sha"]=sha
    req=urllib.request.Request(api,data=json.dumps(b).encode(),headers=h,method="PUT")
    with urllib.request.urlopen(req,timeout=30) as r:return json.loads(r.read().decode())

def local_date(ms):
    return datetime.datetime.fromtimestamp(ms/1000,datetime.timezone.utc).astimezone().date().isoformat()

def main():
    names=anki("deckNames")
    wanted=[n for n in names if n==ROOT or n.startswith(ROOT+"::")]
    # Get all cards in the root including all descendants, then group by their actual deck.
    all_ids=anki("findCards",{"query":f'deck:"{ROOT}"'})
    info=[]
    for c in chunks(all_ids):
        info += anki("cardsInfo",{"cards":c,"retrieved_info_mode":"COMPACT"})
    due_flags=[]
    for c in chunks(all_ids):
        due_flags += anki("areDue",{"cards":c})
    due_map=dict(zip(all_ids,due_flags))

    exact={}
    for x in info:
        name=x.get("deckName")
        if name not in wanted:continue
        e=exact.setdefault(name,{"total":0,"unseen":0,"learning":0,"mature":0,"young":0,"suspended":0,"due":0,"lapses":0})
        e["total"]+=1
        if x.get("type")==0:e["unseen"]+=1
        elif x.get("queue")==1:e["learning"]+=1
        elif x.get("queue")==2:
            if x.get("interval",0)>=21:e["mature"]+=1
            else:e["young"]+=1
        if x.get("queue")==-1:e["suspended"]+=1
        if due_map.get(x.get("cardId"),False):e["due"]+=1
        e["lapses"]+=int(x.get("lapses",0) or 0)

    # 30-day review stats, using cardReviews with a real Unix-ms cutoff.
    cutoff=int((time.time()-30*86400)*1000)
    rev_by_deck={}
    for name in wanted:
        try: revs=anki("cardReviews",{"deck":name,"startID":cutoff})
        except Exception: revs=[]
        rev_by_deck[name]=revs

    # Overall activity should come only from Anki's official by-day endpoint.
    byday=anki("getNumCardsReviewedByDay")
    activity=[{"date":str(x[0]),"count":int(x[1])} for x in byday]
    activity.sort(key=lambda x:x["date"])
    today=anki("getNumCardsReviewedToday")
    last7=sum(x["count"] for x in activity[-7:])
    recent30=activity[-30:]
    last30=sum(x["count"] for x in recent30)

    # Overall 30-day review details from root deck. This covers all descendants only if
    # Anki's deck review query includes children; to guarantee exact roll-up, sum the
    # individual deck review logs, deduplicating by review ID.
    revs_all={}
    for name,revs in rev_by_deck.items():
        for r in revs:
            if len(r)>=9:revs_all[int(r[0])]=r
    revs=list(revs_all.values())
    reviews30=len(revs)
    again30=sum(1 for r in revs if int(r[3])==1)
    time_ms=sum(max(0,int(r[7])) for r in revs)
    retention=round((reviews30-again30)/reviews30*100,1) if reviews30 else 0
    active_days=len({local_date(r[0]) for r in revs}) if revs else 0
    avg_active=round(reviews30/active_days,1) if active_days else 0

    # Tree rollups. Exact rows are computed from cards in that exact deck.
    children={n:[] for n in wanted}
    for n in wanted:
        if n!=ROOT:
            parent=n.rsplit("::",1)[0]
            if parent in children:children[parent].append(n)

    def agg(name):
        a=dict(exact.get(name,{"total":0,"unseen":0,"learning":0,"mature":0,"young":0,"suspended":0,"due":0,"lapses":0}))
        for ch in children.get(name,[]):
            b=agg(ch)
            for k in a:a[k]+=b[k]
        return a

    rows=[]
    for name in sorted(wanted,key=lambda x:(x.count("::"),x.lower())):
        a=agg(name)
        own=exact.get(name,{})
        rr=rev_by_deck.get(name,[])
        rr30=len(rr); ag=sum(1 for r in rr if len(r)>=4 and int(r[3])==1)
        rows.append({
            "name":name,"short_name":ROOT if name==ROOT else name[len(ROOT)+2:],
            "parent":None if name==ROOT else name.rsplit("::",1)[0],
            "level":name.count("::")+1,
            "rollup":bool(children.get(name)),
            "total":a["total"],"seen":a["total"]-a["unseen"],"unseen":a["unseen"],
            "progress":round((a["total"]-a["unseen"])/a["total"]*100,1) if a["total"] else 100,
            "mature":a["mature"],"young":a["young"],"learning":a["learning"],
            "suspended":a["suspended"],"due":a["due"],"lapses":a["lapses"],
            "reviews_30d":rr30,"again_rate_30d":round(ag/rr30*100,1) if rr30 else 0
        })

    root=next(x for x in rows if x["name"]==ROOT)
    data={
      "updated_local":datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
      "overall":root,"today":today,"last7":last7,"last30":last30,
      "total_reviews":reviews30,"activity":activity[-30:],"decks":rows,
      "summary":{
        "mature":root["mature"],"mature_pct":round(root["mature"]/root["total"]*100,1) if root["total"] else 0,
        "due":root["due"],"overdue":0,"reviews_30d":reviews30,
        "retention_30d":retention,"again_rate_30d":round(again30/reviews30*100,1) if reviews30 else 0,
        "study_hours_30d":round(time_ms/3600000,1),
        "avg_reviews_active_day":avg_active
      }
    }
    OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(data,indent=2),encoding="utf-8")
    github_put("data/stats.json",OUT.read_bytes(),"Update Anki statistics")
    print(f"Synced {len(all_ids)} cards across {len(wanted)} decks.")

if __name__=="__main__":
    try:main()
    except Exception as e:
        print("ERROR:",e);raise
