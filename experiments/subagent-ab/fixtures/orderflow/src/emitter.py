"""Fulfillment event emitter."""
import json

def make_event(order_id: str, status: str) -> dict:
    return {"order_id": order_id, "status": status}

def serialize(event: dict) -> str:
    return json.dumps(event, separators=(",", ":"))

class Emitter:
    def __init__(self, sink):
        self.sink = sink
    def emit(self, event: dict):
        self.sink.append(serialize(event))
