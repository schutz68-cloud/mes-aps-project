import asyncio
from fastapi import WebSocket

connections = []

# ======================
# CONNECT
# ======================
async def connect(ws: WebSocket):
    await ws.accept()
    connections.append(ws)

# ======================
# DISCONNECT
# ======================
async def disconnect(ws: WebSocket):
    if ws in connections:
        connections.remove(ws)

# ======================
# ASYNC BROADCAST
# ======================
async def broadcast(message: dict):
    stale_connections = []

    for ws in list(connections):
        try:
            await ws.send_json(message)
        except Exception:
            stale_connections.append(ws)

    for ws in stale_connections:
        await disconnect(ws)

# ======================
# SYNC WRAPPER (для FastAPI endpoints)
# ======================
def broadcast_sync(message: dict):
    loop = asyncio.get_event_loop()
    loop.create_task(broadcast(message))

# connections = []

# async def connect(ws):
#     await ws.accept()
#     connections.append(ws)

# async def disconnect(ws):
#     connections.remove(ws)

# def broadcast_sync(message):
#     import asyncio
#     loop = asyncio.get_event_loop()

#     for ws in connections:
#         loop.create_task(ws.send_json(message))
