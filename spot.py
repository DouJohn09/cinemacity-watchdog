import collections, json, watch, seats
events = watch.collect()
up = sorted(events.values(), key=lambda x: x["datetime"])
layout = None
for e in up[:6]:
    if layout is None:
        layout = seats.fetch_layout(e["pres"])
        print("plán sálu: sedadel celkem", len(layout), "| řady", sorted({r for r,_,_ in layout.values()}))
    free = seats.fetch_free(e["pres"])
    known = [layout[k] for k in free if k in layout]
    byrow = collections.Counter(r for r,_,_ in known)
    reg = collections.Counter(r for r,n,_ in known if seats.is_regular(n))
    print(f"{e['datetime']}  volných klíčů z API={len(free)}  z toho v plánu={len(known)}  po řadách={dict(sorted(byrow.items()))}  jen běžná={dict(sorted(reg.items()))}")
