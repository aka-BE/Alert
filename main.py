from fastapi import FastAPI, Request, Response
from twilio.rest import Client
import time
import os
from pathlib import Path

app = FastAPI()

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")
TO_NUMBER = os.getenv("MY_PHONE_NUMBER")
ALERT_SECRET = os.getenv("ALERT_SECRET")  # optional, see below

BASE_URL = "https://alert-production-f2becb6a.up.railway.app"

COOLDOWN = 300  # 5 minutes
COOLDOWN_FILE = Path("last_call.txt")

client = Client(ACCOUNT_SID, AUTH_TOKEN)


def get_last_call_time() -> float:
    if COOLDOWN_FILE.exists():
        try:
            return float(COOLDOWN_FILE.read_text().strip())
        except Exception:
            return 0
    return 0


def set_last_call_time(timestamp: float):
    COOLDOWN_FILE.write_text(str(timestamp))


@app.get("/")
async def health():
    return {"status": "ok", "message": "Twilio alert service is running"}


@app.api_route("/twiml", methods=["GET", "POST"])
async def twiml():
    # Twilio fetches this when the call is answered
    return Response(
        content='<Response><Pause length="30"/></Response>',
        media_type="application/xml",
    )


@app.api_route("/alert", methods=["GET", "POST"])
async def alert(request: Request):
    # Only enforced if ALERT_SECRET is set in Railway Variables
    if ALERT_SECRET and request.query_params.get("key") != ALERT_SECRET:
        return Response(content="Forbidden", status_code=403)

    now = time.time()
    last_call = get_last_call_time()

    if now - last_call < COOLDOWN:
        remaining = int(COOLDOWN - (now - last_call))
        print(f"Cooldown active — {remaining}s left")
        return Response(content=f"Cooldown active ({remaining}s left)", status_code=200)

    try:
        call = client.calls.create(
            to=TO_NUMBER,
            from_=FROM_NUMBER,
            url=f"{BASE_URL}/twiml",
        )
        set_last_call_time(now)
        print(f"✅ Call started: {call.sid}")
        return Response(content="Call triggered", status_code=200)

    except Exception as e:
        print(f"❌ Error making call: {e}")
        return Response(content="Error making call", status_code=500)
