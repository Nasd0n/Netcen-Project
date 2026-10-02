"""Shared connection settings for the Escape Plan prototype.

The client reads these values from source code, so players never have to type
an IP address or port number.
"""

# Use 127.0.0.1 when testing server and client on the same computer.
# For a second computer on the same Wi-Fi, replace this with the server
# computer's local IPv4 address, for example "192.168.1.23".
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 5050

# Browser players: the server shows this page on 127.0.0.1 only, and the
# Cloudflare tunnel publishes it as a public https:// link (see tunnel.py).
WEB_PORT = 5051
