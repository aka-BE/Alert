import asyncio
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from twilio.rest import Client

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")
TO_NUMBER = os.getenv("MY_PHONE_NUMBER")
SECRET = os.getenv("SECRET")  # optional, protects /test and /rearm

BASE_URL = "https://alert-production-f2becb6a.up.railway.app"

SYMBOL = "BTCUSDT"
POLL = float(os.getenv("POLL_SECONDS", "2"))
PRICE_URL = "https://fapi.binance.com/fapi/v1/ticker/price"

# name -> (level from Railway variable, direction)
ALERT_DEFS = {
    "A1": (os.getenv("A1"), "above"),
    "A2": (os.getenv("A2"), "above"),
    "B1": (os.getenv("B1"), "below"),
    "B2": (os.getenv("B2"), "below"),
}

# Only alerts with a level set are active
alerts = {}
for name, (level, direction) in ALERT_DEFS.items():
    if level:
        alerts[name] = {
            "level": float(level),
            "direction": direction,
            "armed": True,
            "fired_at": None,
            "fired_price": None,
        }

client = Client(ACCOUNT_SID, AUTH_TOKEN)
call_lock = asyncio.Lock()
status = {"last_price": None, "last_error": None, "last_call_error": None}


def authorized(request: Request) -> bool:
    return not SECRET or request.query_params.get("key") == SECRET


async def place_call(name: str):
    return await asyncio.to_thread(
        lambda: client.calls.create(
            to=TO_NUMBER, from_=FROM_NUMBER, url=f"{BASE_URL}/twiml?name={name}"
        )
    )


async def fire(name: str):
    # The lock makes simultaneous alerts call one after another, not at once
    async with call_lock:
        try:
            call = await place_call(name)
            status["last_call_error"] = None
            print(f"✅ {name}: call started {call.sid}")
        except Exception as e:
            status["last_call_error"] = str(e)
            print(f"❌ {name}: error making call: {e}")
        await asyncio.sleep(40)  # let this call finish before the next one


async def watcher():
    prev = None
    async with httpx.AsyncClient(timeout=10) as http:
        while True:
            try:
                r = await http.get(PRICE_URL, params={"symbol": SYMBOL})
                r.raise_for_status()
                price = float(r.json()["price"])
                status["last_price"] = price
                status["last_error"] = None

                if prev is not None:
                    for name, a in alerts.items():
                        if not a["armed"]:
                            continue
                        if a["direction"] == "above":
                            crossed = prev < a["level"] <= price
                        else:
                            crossed = prev > a["level"] >= price
                        if crossed:
                            print(f"🚨 {name}: {SYMBOL} crossed {a['direction']} {a['level']} at {price}")
                            a["armed"] = False
                            a["fired_at"] = time.time()
                            a["fired_price"] = price
                            asyncio.create_task(fire(name))
                prev = price
            except Exception as e:
                status["last_error"] = str(e)
                print(f"Price fetch error: {e}")
            await asyncio.sleep(POLL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(watcher())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def health():
    return {"status": "ok", "symbol": SYMBOL, **status, "alerts": alerts}


@app.api_route("/twiml", methods=["GET", "POST"])
async def twiml(request: Request):
    name = request.query_params.get("name", "alert")
    xml = (
        f'<Response><Say>Alert {name} triggered.</Say>'
        f'<Pause length="5"/></Response>'
    )
    return Response(content=xml, media_type="application/xml")


@app.get("/test")
async def test(request: Request):
    if not authorized(request):
        return Response("Forbidden", status_code=403)
    call = await place_call("test")
    return {"call": call.sid}


@app.get("/rearm")
async def rearm(request: Request):
    # /rearm re-arms all alerts, /rearm?name=A1 re-arms just one
    if not authorized(request):
        return Response("Forbidden", status_code=403)
    name = request.query_params.get("name")
    targets = [name] if name else list(alerts)
    for n in targets:
        if n in alerts:
            alerts[n]["armed"] = True
    return {"rearmed": targets}
