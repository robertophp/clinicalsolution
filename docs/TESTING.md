# Testing the Clinica Assistant Agent

## Project idea (summary)

**Clinica Assistant Agent** is a backend for a **WhatsApp dental clinic assistant**:

- **Twilio** receives WhatsApp messages and forwards them to your server.
- Your server identifies the **clinic** via `?clinic_id=xxx`, loads that clinic’s **system prompt** from `backend/data/clinics_mock.json`.
- **Vertex AI Gemini 1.5 Flash** generates a reply using that prompt and the user message.
- The reply is sent back as **TwiML** so Twilio can deliver it on WhatsApp.

You also have **BigQuery + SQLAlchemy** and a **Cita** (appointment) model set up for future use (e.g. booking flow).

---

## How to test this code

### 1. Health check (no GCP/Twilio needed)

```powershell
cd "c:\Users\rober\OneDrive\Desktop\AI projects\Clinica Assistant Agent\clinicalsolution"
.venv\Scripts\activate
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

In another terminal:

```powershell
curl http://localhost:8000/health
```

You should get `OK`.

### 2. Simulate the WhatsApp webhook locally (needs GCP + .env)

Create a `.env` with `PROJECT_ID` and `LOCATION` (and optionally `TWILIO_AUTH`). Then:

```powershell
# Simulate Twilio POST (form data)
curl -X POST "http://localhost:8000/whatsapp?clinic_id=demo_clinic_1" ^
  -F "From=+1234567890" ^
  -F "Body=Hola, me duele una muela"
```

Response will be **XML (TwiML)** with the assistant’s message inside.

### 3. Use the JSON test endpoint (recommended for quick tests)

The app exposes a **JSON** endpoint that accepts the same inputs without form encoding, so you can test from a browser, Postman, or scripts:

```powershell
curl -X POST "http://localhost:8000/chat?clinic_id=demo_clinic_1" ^
  -H "Content-Type: application/json" ^
  -d "{\"from_number\": \"+1234567890\", \"body\": \"Hola, quiero agendar una cita\"}"
```

Response is JSON, e.g. `{"reply": "..."}`.

### 4. Test with real WhatsApp (Twilio + ngrok)

1. Install [ngrok](https://ngrok.com/) and run: `ngrok http 8000`
2. In Twilio Console → WhatsApp Sandbox (or your number) → set webhook to:  
   `https://YOUR_NGROK_URL/whatsapp?clinic_id=demo_clinic_1`
3. Send a WhatsApp message to your Twilio number; the agent reply should appear in WhatsApp.

### 5. Automated tests (pytest)

From project root with venv activated:

```powershell
pip install -r requirements-dev.txt
pytest tests/ -v
```

See `tests/` for unit and API tests (health, chat, and WhatsApp-style endpoint with mocked Gemini).

---

## Next steps to make it a “potential agent solution” to test

1. **Add tests** – Use `tests/` and the `/chat` endpoint so you can run and debug without Twilio.
2. **Persist conversation** – Store chat history by `from_number` + `clinic_id` (e.g. in BigQuery or a cache) so the agent has context across messages.
3. **Use the Cita model** – Add an “agent step” that detects booking intent and creates/updates a `Cita` (e.g. via Gemini function calling or a small classifier).
4. **Structured agent flow** – Optional: add intents (e.g. `greeting`, `book_appointment`, `symptoms`) and branch logic or tools so the agent can “do things” (e.g. “create appointment”) instead of only replying in free text.

Starting with **health** → **/chat** → **pytest** gives you a clear path to test the current code and then iterate toward the full agent solution.
