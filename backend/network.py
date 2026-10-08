"""LAN discovery and a same-origin, paired participant access boundary.

The operator remains loopback-only even when the participant server listens on
all interfaces. Nothing trusts forwarded headers, DNS names, or the Host header
alone to grant operator access.
"""
import ipaddress
import json
import platform
import re
import secrets
import socket
import subprocess
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse

COOKIE_NAME = "chengsi_participant"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
PARTICIPANT_FILES = {
    "participant.html", "participant.js", "participant.css", "training-scenes.css",
    "easy-going-profile.js", "audio-programs.js", "audio-engine.js", "silk-material.js",
    "renderer.js", "feedback-policy.js", "calibration-cues.js", "participant-visuals.js",
    "training-scenes.js",
}
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                         ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"))


def _interface_addresses():
    """Use OS inventory without third-party packages; include every IPv4 adapter."""
    system = platform.system()
    try:
        if system == "Windows":
            command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                       "Get-NetIPAddress -AddressFamily IPv4 | Select-Object IPAddress,InterfaceAlias | ConvertTo-Json -Compress"]
            output = subprocess.run(command, capture_output=True, text=True, timeout=5, check=True).stdout
            rows = json.loads(output or "[]")
            return [(row["InterfaceAlias"], row["IPAddress"]) for row in (rows if isinstance(rows, list) else [rows])]
        if system == "Darwin":
            output = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True, timeout=3, check=True).stdout
            addresses, interface = [], ""
            for line in output.splitlines():
                if line and not line[0].isspace():
                    interface = line.split(":", 1)[0]
                match = re.match(r"\s+inet\s+(\d+(?:\.\d+){3})\s", line)
                if match:
                    addresses.append((interface, match.group(1)))
            return addresses
        output = subprocess.run(["ip", "-j", "-4", "address", "show"], capture_output=True, text=True, timeout=3, check=True).stdout
        return [(row["ifname"], address["local"]) for row in json.loads(output)
                for address in row.get("addr_info", []) if address.get("family") == "inet"]
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return []


def lan_addresses():
    """Physical private adapters first, virtual adapters later, route fallback last."""
    addresses = _interface_addresses()
    if not addresses:
        try:
            addresses.extend(("hostname", value[4][0]) for value in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
        except OSError:
            pass
    # UDP connect selects a route without transmitting any packet or requiring Internet.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            addresses.append(("route-fallback", probe.getsockname()[0]))
    except OSError:
        pass
    ranked = {}
    for interface, value in addresses:
        try:
            address = ipaddress.IPv4Address(value)
        except ValueError:
            continue
        if not any(address in subnet for subnet in PRIVATE_NETWORKS):
            continue
        virtual = bool(re.search(r"utun|\btun|\btap|docker|veth|virbr|vmnet|virtual|vbox|tailscale|hamachi|wsl|vpn", interface, re.I))
        rank = (address.is_link_local, virtual, interface == "route-fallback", str(address))
        ranked[str(address)] = min(rank, ranked.get(str(address), rank))
    return sorted(ranked, key=ranked.get)


def local_peer(scope):
    peer = (scope.get("client") or ("", 0))[0]
    if peer == "testclient":  # Starlette's in-process transport, never a TCP peer.
        return True
    try:
        address = ipaddress.ip_address(peer)
        return address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False


class NetworkAccess:
    def __init__(self, lan=False, port=8768):
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        self.lan = bool(lan)
        self.port = port
        self.addresses = lan_addresses() if self.lan else []
        self.pair_token = secrets.token_urlsafe(32)
        self._credentials = {}
        self._client_credentials = {}

    def connection(self):
        # Refresh after joining Wi-Fi or enabling a hotspot without restarting
        # the active session; the same list is also the accepted Host boundary.
        if self.lan:
            self.addresses = lan_addresses()
        return {"lan_enabled": self.lan, "port": self.port,
                "participant_urls": [f"http://{address}:{self.port}/participant.html?pair={self.pair_token}" for address in self.addresses],
                "local_participant_url": f"http://127.0.0.1:{self.port}/participant.html"}

    def valid_pair(self, token):
        return self.lan and isinstance(token, str) and secrets.compare_digest(token.encode(), self.pair_token.encode())

    def credential(self, headers):
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get("cookie", ""))
            value = cookies.get(COOKIE_NAME)
            return value.value if value is not None and value.value in self._credentials else None
        except (CookieError, ValueError, TypeError):
            return None

    def paired(self, headers):
        return self.credential(headers) is not None

    def issue_credential(self, previous=None):
        """Each pairing gets its own secret; re-pairing the same browser rotates it."""
        token = secrets.token_urlsafe(32)
        self._credentials[token] = self._credentials.pop(previous, None)
        if previous is not None:
            for client, owner in tuple(self._client_credentials.items()):
                if owner == previous:
                    self._client_credentials[client] = token
        return token

    def authorize_client(self, credential, client_id, active_client_id, last_client_id, heartbeat=False):
        """An exposed client ID is never sufficient to impersonate its browser."""
        if credential not in self._credentials or not client_id:
            return False
        bound = self._credentials[credential]
        owner = self._client_credentials.get(client_id)
        if bound == client_id:
            return owner == credential
        if not heartbeat:
            return False
        # A second tab may replace its own ID only after that window's lease ends.
        if bound is not None and active_client_id == bound:
            return False
        if owner is not None and owner != credential:
            return False
        # This also protects a loopback participant, which has no pairing cookie.
        if last_client_id == client_id and owner != credential:
            return False
        self._credentials[credential] = client_id
        self._client_credentials[client_id] = credential
        return True

    def classify(self, scope, headers):
        """Return local operator access only for both a loopback Host and peer."""
        host = headers.get("host", "").lower()
        try:
            parsed = urlsplit("http://" + host)
            hostname, port = parsed.hostname, parsed.port or 80
            if (not hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                    or port != self.port):
                return None
            origin = headers.get("origin")
            if origin:
                source = urlsplit(origin)
                if source.scheme != "http" or source.netloc.lower() != host or source.path or source.query or source.fragment:
                    return None
        except ValueError:
            return None
        if headers.get("sec-fetch-site") == "cross-site":
            return None
        if hostname in LOOPBACK_HOSTS:
            return "operator" if local_peer(scope) else None
        if self.lan and hostname in self.addresses:
            return "participant"
        return None


class LocalOnlyMiddleware:
    """Preserve the old loopback default and opt in to paired, limited LAN access."""
    def __init__(self, app, access):
        self.app, self.access = app, access

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        header_items = scope.get("headers", [])
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in header_items}
        # Ambiguous duplicate authority/origin headers cannot cross the boundary.
        duplicate = any(sum(key.decode("latin-1").lower() == name for key, _ in header_items) > 1
                        for name in ("host", "origin"))
        role = None if duplicate else self.access.classify(scope, headers)
        scope.setdefault("state", {})["local_operator"] = role == "operator"
        path, method = scope.get("path", ""), scope.get("method", "GET")
        allowed = role == "operator"
        if role == "participant":
            static = method in {"GET", "HEAD"} and (path.lstrip("/") in PARTICIPANT_FILES or path.startswith("/assets/"))
            pair = path == "/api/pair" and method == "POST"
            public = static or pair
            credential = self.access.credential(headers)
            scope["state"]["participant_credential"] = credential
            paired = credential is not None
            participant_route = ((path == "/api/state" and method == "GET")
                                 or (path == "/ws/live" and scope["type"] == "websocket")
                                 or (method == "POST" and path in {"/api/presentation/heartbeat", "/api/presentation/wear-confirmation", "/api/presentation/preview"})
                                 or (method == "POST" and re.fullmatch(r"/api/sessions/[^/]+/commands", path)))
            allowed = public or (paired and participant_route)
        if not allowed:
            detail = "Local operator or paired same-origin participant access only"
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403, "reason": detail})
            else:
                await JSONResponse({"detail": detail}, status_code=403)(scope, receive, send)
            return
        # Bound image submissions before FastAPI buffers/parses the JSON body.
        if path == "/api/presentation/preview" and method == "POST":
            body, more = bytearray(), True
            while more:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 110_000:
                    await JSONResponse({"detail": "Preview frame is too large"}, status_code=413)(scope, receive, send)
                    return
                more = message.get("more_body", False)
            async def buffered_receive():
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await self.app(scope, buffered_receive, send)
        await self.app(scope, receive, send)
