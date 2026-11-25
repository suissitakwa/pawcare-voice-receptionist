# Phone calls through Vapi

[Vapi](https://vapi.ai) can answer the phone line instead of OpenAI Realtime. It chains speech recognition (Deepgram), a language model and a text-to-speech voice (ElevenLabs by default), with its own turn-taking, so you can choose from many more natural-sounding voices.

The backend stays in charge either way: the same instructions, tools, limits, spoken confirmation, change and cancel rules, and Google Calendar. Vapi never books anything itself; every tool runs on this backend.

## How a call flows

```
Caller ──► Twilio number ──► Vapi ──assistant-request──► backend
                              ▲  ◄──── assistant ──────────┘
                              │  ──tool-calls──────────► backend (runs the tools)
                              └─ ──end-of-call-report──► backend (records the end and cost)
```

1. A call reaches the number, which is imported into Vapi.
2. Vapi asks `POST /api/v1/vapi/events` for an assistant. The backend checks the phone limits and answers with the instructions (built from the database, with today's date and the caller's number), the phone tools, the voice and the transcriber. The assistant greets first.
3. When the model uses a tool, Vapi sends the call to the same endpoint and speaks the result.
4. At the end, Vapi sends a report; the session is ended with Vapi's reason and cost.

Calls are voice sessions with channel `phone` and provider `vapi`, so they count towards the same phone limits and dashboard figures as OpenAI calls.

## Setup

### 1. A shared secret

Make a long random secret, for example:

```bash
openssl rand -hex 32
```

### 2. Railway variables

On the backend service:

| Variable | Value |
| --- | --- |
| `VAPI_SECRET` | the secret |
| `VAPI_ENABLED` | `true` |
| `VAPI_VOICE_ID` | optional; default is ElevenLabs "Rachel" (`21m00Tcm4TlvDq8ikWAM`) |
| `VAPI_MALE_VOICE_ID` | optional; for businesses whose owner chose the male voice, with `VAPI_VOICE_PROVIDER=11labs`. Default ElevenLabs "Adam" (`pNInz6obpgDQGcFmaJgB`) |
| `VAPI_VOICE_PROVIDER` | optional; default `11labs` (for example `cartesia`) |
| `VAPI_MODEL` | optional; default `gpt-4.1-mini` |
| `VAPI_TRANSCRIBER_PROVIDER` | optional; `deepgram` (default) or `assembly-ai` |
| `VAPI_TRANSCRIBER_MODEL` | optional; empty uses the provider's default: Deepgram `nova-3`, AssemblyAI `universal-streaming-english`. For AssemblyAI it is sent as `speechModel`. Clear it when you switch provider |
| `VAPI_ASSISTANT_OVERRIDES` | optional JSON merged into the assistant, for example `{"startSpeakingPlan": {"waitSeconds": 0.6}}` |

Redeploy. Until `VAPI_SECRET` is set, the endpoint returns 404.

`PHONE_NUMBER` keeps showing the call button on the website while `VAPI_ENABLED` is on. `PHONE_ENABLED` only concerns OpenAI calls and can be set back to `false`.

### 3. Move the number from the OpenAI trunk to Vapi

1. In Twilio, open **Elastic SIP Trunking → Trunks →** your trunk **→ Numbers** and remove the number. (Twilio routes a number either to the trunk or to Vapi, not both.)
2. In the Vapi dashboard, **Phone Numbers → Import → Twilio**: your Twilio Account SID, Auth Token, and the number.

To switch back to OpenAI later, remove the number from Vapi and attach it to the trunk again.

### 4. Point the number at the backend

1. In Vapi, create a credential: **Settings → Credentials → Custom Credential → Bearer Token**, with the secret as the token, header `Authorization`, Bearer prefix on.
2. Open the imported phone number:
   - **Assistant**: leave empty, so Vapi asks the backend on every call.
   - **Server URL**: `https://<backend host>/api/v1/vapi/events`
   - **Credential**: the one you just created.

### 5. Check it

1. Call the number. It should greet you within a second or two and say it's the AI assistant.
2. Book a time and say yes when it reads the summary. Then call again, give the reference and your phone number, and move or cancel it.
3. In the staff dashboard, the booking appears and phone calls go up. In Vapi's **Call Logs**, each call shows the tool calls and the cost.

If the call says the assistant can't take calls, check `VAPI_ENABLED` and the phone limits. If tools fail, check the backend logs for `pawcare.vapi`, and that the credential's token matches `VAPI_SECRET` (a 401 in Vapi's logs means it doesn't).

## Recording

Calls are not recorded: the assistant sets `artifactPlan.recordingEnabled` to false, because Vapi records by default and callers aren't told about recording (California requires everyone's consent). Vapi may still keep a transcript and logs for its call history. If you ever turn recording on, add a recording notice to the greeting first, and update the privacy policy on helloeverai.com.

## Several businesses

Each Vapi call goes to the business that registered the number dialed. Point every number at the same server URL and register them as in [phone.md](phone.md#numbers-for-several-businesses).

## Choosing a voice

Browse voices in Vapi's voice library or ElevenLabs, copy the voice id, set `VAPI_VOICE_ID`, and redeploy (or use `VAPI_ASSISTANT_OVERRIDES` to try other settings). Change one thing at a time and call to compare.

## Costs

Vapi charges per minute for its platform, plus the transcriber, model and voice providers. Twilio charges for the number and minutes.

To pay a provider from your own account (and its credits) instead of through Vapi, add that provider's API key under **Integrations** in the Vapi dashboard. Vapi then uses your account for that part of every call; its platform fee stays. For speech recognition, add a Deepgram or AssemblyAI key and set `VAPI_TRANSCRIBER_PROVIDER` to match. Each call uses one transcriber, so switch providers when one account's credits run out. The voice and the model are usually larger shares of a call than speech recognition; the per-call breakdown is in Vapi's **Call Logs**. Each call's cost as reported by Vapi is stored on the voice session (`usage.cost_usd`). See Vapi's pricing page for current rates.
