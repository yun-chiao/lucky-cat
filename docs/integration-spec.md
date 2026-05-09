# On-chain Lucky Cat Integration Spec

## Frontend Expected Payload

```json
{
  "eventId": "mantle-20260506-0001",
  "chain": "mantle",
  "token": "USDY",
  "widgetState": "alpha",
  "severityScore": 82,
  "title": "發現可疑大額買入",
  "badge": "USDY +$480K in 40s",
  "message": "哼，這筆大單有點香，快看。",
  "updatedAt": "2026-05-06T09:38:11Z",
  "tx": {
    "hash": "0x123abc...",
    "from": "0xaaaa...",
    "to": "0xbbbb...",
    "valueUsd": 480000,
    "direction": "in"
  },
  "meta": {
    "source": "geckoterminal",
    "windowSec": 40,
    "tags": ["whale", "sudden-volume"]
  }
}
```

## State Mapping Recommendation

- `idle`: `severityScore < 40`
- `alpha`: `40 <= severityScore < 75`
- `risk`: `severityScore >= 75`

## n8n LLM System Prompt

```text
你是「On-chain Lucky Cat」的傲嬌貓咪播報員。
任務：根據輸入事件，輸出 1 句繁體中文短語。
規則：
1) 僅輸出一句，最多 20 字。
2) 語氣：傲嬌、機靈、略帶催促。
3) 不要解釋、不要條列、不要 emoji。
4) 必須提到 token 或風險感（二擇一）。
5) 禁止投資保證與誇大承諾。
```
