import asyncio


class ConnectionHub:
    def __init__(self):
        self.clients = set()

    async def connect(self, websocket):
        await websocket.accept()
        self.clients.add(websocket)

    def disconnect(self, websocket):
        self.clients.discard(websocket)

    async def broadcast(self, message):
        if not self.clients:
            return
        results = await asyncio.gather(*(client.send_json(message) for client in list(self.clients)), return_exceptions=True)
        for client, result in zip(list(self.clients), results):
            if isinstance(result, Exception):
                self.disconnect(client)


hub = ConnectionHub()
