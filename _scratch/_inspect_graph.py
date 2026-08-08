import json
import sys

path = sys.argv[1]
d = json.load(open(path, encoding="utf-8"))
g = d["graph"]
print("geometry:", d["geometry"])
for nid in sorted(g, key=int):
    node = g[nid]
    print(f"  {nid:>3} {node['class_type']:<28} {node['inputs']}")
