import requests
r = requests.get("http://127.0.0.1:8189/history", timeout=10)
data = r.json()
for k, v in data.items():
    st = v.get("status", {})
    print(k[:8], st.get("status_str"), "completed=", st.get("completed"))
    msgs = st.get("messages", [])
    if msgs:
        print("   last:", msgs[-1][0], msgs[-1][1] if len(msgs[-1]) > 1 else "")
