import json, os, sys, urllib.request, base64, datetime, time
from pathlib import Path

ANKI = "http://127.0.0.1:8765"
ROOT = "My ANZCA primary"
OUT = Path(__file__).with_name("data") / "stats.json"

def anki(action, params=None):
    payload={"action":action,"version":6}
    if params is not None: payload["params"]=params
    req=urllib.request.Request(ANKI,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=10) as r: x=json.loads(r.read().decode())
    if x.get("error"): raise RuntimeError(x["error"])
    return x["result"]

def github_put(path, content, message):
    token=os.environ.get("GITHUB_TOKEN")
    repo=os.environ.get("GITHUB_REPO")
    if not token or not repo: raise RuntimeError("Set GITHUB_TOKEN and GITHUB_REPO environment variables.")
    api=f"https://api.github.com/repos/{repo}/contents/{path}"
    headers={"Authorization":f"Bearer {token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","Content-Type":"application/json"}
    req=urllib.request.Request(api,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=15) as r: old=json.loads(r.read().decode())
        sha=old["sha"]
    except urllib.error.HTTPError as e:
        if e.code==404: sha=None
        else: raise
    body={"message":message,"content":base64.b64encode(content).decode()}
    if sha: body["sha"]=sha
    req=urllib.request.Request(api,data=json.dumps(body).encode(),headers=headers,method="PUT")
    with urllib.request.urlopen(req,timeout=20) as r: return json.loads(r.read().decode())

def main():
    names=anki("deckNames")
    decks=[n for n in names if n==ROOT or n.startswith(ROOT+"::")]
    stats=anki("getDeckStats",{"decks":decks})
    rows=[]
    now_ms=int(time.time()*1000)
    cutoff=now_ms-30*86400000
    activity={}
    total_reviews=0
    today=anki("getNumCardsReviewedToday")
    byday=anki("getNumCardsReviewedByDay")
    for date,count in byday:
        activity[date]=count
    for name in decks:
        s=next((v for v in stats.values() if v["name"]==name),None)
        if not s: continue
        # Search the individual deck. Anki's deck search syntax gives the deck's cards.
        unseen=len(anki("findCards",{"query":f'deck:"{name}" is:new'}))
        total=s["total_in_deck"]; seen=max(0,total-unseen)
        # Recent review history for richer activity. cardReviews is read-only.
        try:
            revs=anki("cardReviews",{"deck":name,"startID":cutoff})
        except Exception:
            revs=[]
        reviews_30d=len(revs)
        again_30d=sum(1 for r in revs if len(r)>=4 and r[3]==1)
        total_reviews += reviews_30d
        for r in revs:
            if r and r[0]:
                dt=datetime.datetime.fromtimestamp(r[0]/1000,datetime.timezone.utc).astimezone()
                key=dt.date().isoformat()
                activity[key]=activity.get(key,0)+1
        rows.append({
            "name":name,
            "short_name":ROOT if name==ROOT else name[len(ROOT)+2:],
            "parent": None if name==ROOT or name.count("::")==1 else name.rsplit("::",1)[0],
            "total":total,"seen":seen,"unseen":unseen,
            "progress":round(seen/total*100,1) if total else 100,
            "due":s["new_count"]+s["learn_count"]+s["review_count"],
            "new_due":s["new_count"],"learning_due":s["learn_count"],"review_due":s["review_count"],
            "reviews_30d":reviews_30d,"again_30d":again_30d
        })
    dates=sorted(activity)
    recent=[{"date":d,"count":activity[d]} for d in dates[-30:]]
    last7=sum(x["count"] for x in recent[-7:])
    last30=sum(x["count"] for x in recent[-30:])
    root=next((x for x in rows if x["name"]==ROOT),None)
    if not root:
        root={"total":sum(x["total"] for x in rows),"seen":sum(x["seen"] for x in rows)}
        root["unseen"]=root["total"]-root["seen"]; root["progress"]=round(root["seen"]/root["total"]*100,1) if root["total"] else 100
    data={"updated_local":datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
          "overall":root,"today":today,"last7":last7,"last30":last30,
          "total_reviews":total_reviews,"activity":recent,"decks":rows}
    OUT.parent.mkdir(exist_ok=True); OUT.write_text(json.dumps(data,indent=2),encoding="utf-8")
    github_put("data/stats.json",OUT.read_bytes(),"Update Anki statistics")
    print("Synced Anki statistics to GitHub.")

if __name__=="__main__":
    try: main()
    except Exception as e:
        print("ERROR:",e); sys.exit(1)
