"""Stage 26 probe 2: JSON mode + tools in one request; a wrap-up request without tools after tool history."""
import json, os, sys
import httpx
key = os.environ["DEEPSEEK_API_KEY"]
H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
tools = [{"type": "function", "function": {"name": "get_price", "description": "Close price of a ticker",
          "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}}}]
model, thinking = sys.argv[1], sys.argv[2]
sysmsg = {"role": "system", "content": 'Answer with one JSON object {"ticker": str, "price": number}. Use the tool to get data.'}
msgs = [sysmsg, {"role": "user", "content": "What is the close price of AAA?"}]
def post(m, label, with_tools=True):
    body = {"model": model, "messages": m, "max_tokens": 600, "thinking": {"type": thinking},
            "response_format": {"type": "json_object"}}
    if with_tools: body["tools"] = tools
    r = httpx.post("https://api.deepseek.com/chat/completions", json=body, headers=H, timeout=120)
    print(label, r.status_code, r.text.replace(key, "***")[:300] if r.status_code != 200 else "")
    return r
r = post(msgs, "round1-json+tools")
msg = r.json()["choices"][0]["message"]
calls = msg.get("tool_calls") or []
print("tool_calls:", len(calls), "content:", (msg.get("content") or "")[:120])
if not calls: sys.exit(0)
hist = msgs + [{"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}] + [
    {"role": "tool", "tool_call_id": c["id"], "content": "AAA close 101.5"} for c in calls]
hist.append({"role": "user", "content": "No more tools. Give the final JSON now."})
r2 = post(hist, "wrapup-without-tools", with_tools=False)
if r2.status_code == 200: print("answer:", r2.json()["choices"][0]["message"].get("content", "")[:160])
