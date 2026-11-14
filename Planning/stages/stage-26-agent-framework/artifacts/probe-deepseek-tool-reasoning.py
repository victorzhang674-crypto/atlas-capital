"""Stage 26 probe: does DeepSeek thinking mode need reasoning_content back in a tool round?"""
import json, os, sys
import httpx
key = os.environ["DEEPSEEK_API_KEY"]
H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
tools = [{"type": "function", "function": {"name": "get_price", "description": "Close price of a ticker",
          "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}}}]
model = sys.argv[1]; thinking = sys.argv[2]
msgs = [{"role": "user", "content": "What is the close price of AAA? Use the tool, then answer in one sentence."}]
def post(m, label):
    body = {"model": model, "messages": m, "tools": tools, "max_tokens": 600, "thinking": {"type": thinking}}
    r = httpx.post("https://api.deepseek.com/chat/completions", json=body, headers=H, timeout=120)
    print(label, r.status_code, r.text.replace(key, "***")[:400] if r.status_code != 200 else "")
    return r
r = post(msgs, "round1")
msg = r.json()["choices"][0]["message"]
print("has reasoning_content:", bool(msg.get("reasoning_content")), "tool_calls:", bool(msg.get("tool_calls")))
calls = msg.get("tool_calls") or []
if not calls: sys.exit(0)
tool_msgs = [{"role": "tool", "tool_call_id": c["id"], "content": "AAA close 101.5"} for c in calls]
without = {"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}
r2 = post(msgs + [without] + tool_msgs, "round2-without-reasoning")
if r2.status_code == 200: print("answer:", r2.json()["choices"][0]["message"].get("content","")[:200])
withr = dict(without, reasoning_content=msg.get("reasoning_content", ""))
r3 = post(msgs + [withr] + tool_msgs, "round2-with-reasoning")
if r3.status_code == 200: print("answer:", r3.json()["choices"][0]["message"].get("content","")[:200])
