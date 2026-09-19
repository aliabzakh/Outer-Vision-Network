"""The phone link over Bluetooth LE: the headset is a GATT peripheral (bless: BlueZ on the Pi,
CoreBluetooth on a Mac). The phone's browser connects with Web Bluetooth, writes request chunks to RX and
gets response/event chunks as TX notifications. Framing and ops live in link.py.

It runs as a separate helper process that forwards to the rig's HTTP phone link. Bluetooth stacks can
kill their process outright (macOS aborts a terminal app without Bluetooth permission), and that must
never take the instrument down with it.

  python -m outer_vision.wallet.ble --http http://127.0.0.1:8765 --name OuterVision
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import subprocess
import sys
import threading
import urllib.request

from .link import Reassembler, chunk, encode

SERVICE = "7b3e0001-5f2a-4c1e-9d6a-0e7e5f0a11ce"
RX = "7b3e0002-5f2a-4c1e-9d6a-0e7e5f0a11ce"      # phone -> headset (write)
TX = "7b3e0003-5f2a-4c1e-9d6a-0e7e5f0a11ce"      # headset -> phone (notify)
CHUNK = 180                                        # fits the default 185-byte ATT MTU Android negotiates
HINTS = {134: "macOS stopped it: allow Bluetooth for your terminal app in System Settings > Privacy & Security > Bluetooth",
         -6: "macOS stopped it: allow Bluetooth for your terminal app in System Settings > Privacy & Security > Bluetooth"}


def start(http_url: str, name: str, log=print):
    """Launch the BLE helper; logs (never raises) if it dies. Returns the Popen or None."""
    try:
        import bless  # noqa: F401
    except ImportError:
        log("[ble] bless not installed (pip install bless); phone link over HTTP only")
        return None
    proc = subprocess.Popen([sys.executable, "-m", "outer_vision.wallet.ble", "--http", http_url, "--name", name],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def watch():
        for line in proc.stdout:
            log(line.rstrip())
        code = proc.wait()
        log(f"[ble] helper exited ({code}){': ' + HINTS[code] if code in HINTS else ''}; phone link over HTTP only")
    threading.Thread(target=watch, daemon=True, name="ble-watch").start()
    return proc


def _post(url, raw: bytes) -> bytes:
    req = urllib.request.Request(url + "/wallet", raw, {"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except Exception as e:
        return encode({"id": None, "ok": False, "error": f"headset link unreachable ({e})"})


async def serve(http_url: str, name: str):
    from bless import BlessServer, GATTAttributePermissions, GATTCharacteristicProperties

    loop = asyncio.get_running_loop()
    server = BlessServer(name=name, loop=loop)
    rx, ids = Reassembler(), itertools.count(1)

    def send(data: bytes):
        for part in chunk(data, next(ids), CHUNK):
            server.get_characteristic(TX).value = bytearray(part)
            server.update_value(SERVICE, TX)

    def on_write(characteristic, value, **kwargs):
        if str(characteristic.uuid).lower() != RX:
            return
        msg = rx.feed(bytes(value))
        if msg is not None:        # forward off the BLE callback thread; revoke can wait on the network
            threading.Thread(target=lambda: loop.call_soon_threadsafe(send, _post(http_url, msg)), daemon=True).start()

    server.read_request_func = lambda characteristic, **kw: characteristic.value
    server.write_request_func = on_write
    await server.add_new_service(SERVICE)
    await server.add_new_characteristic(SERVICE, RX, GATTCharacteristicProperties.write, None,
                                        GATTAttributePermissions.writeable)
    await server.add_new_characteristic(SERVICE, TX, GATTCharacteristicProperties.notify | GATTCharacteristicProperties.read,
                                        None, GATTAttributePermissions.readable)
    await server.start()
    print(f"[ble] advertising as {name!r}, service {SERVICE}", flush=True)
    since = 0
    while True:                                    # push wallet events as notifications
        await asyncio.sleep(1.0)
        try:
            evs = await asyncio.to_thread(lambda: json.load(urllib.request.urlopen(f"{http_url}/wallet/events?since={since}", timeout=5)))
        except Exception:
            continue
        for e in evs:
            since = max(since, e["t_ms"])
            send(encode({"event": e}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", required=True)
    ap.add_argument("--name", default="OuterVision")
    a = ap.parse_args()
    asyncio.run(serve(a.http.rstrip("/"), a.name[:20]))


if __name__ == "__main__":
    main()
