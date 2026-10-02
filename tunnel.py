"""Start a Cloudflare quick tunnel and report the public HTTPS address.

``cloudflared`` connects out to Cloudflare and forwards
``https://<random-name>.trycloudflare.com`` to our local web port. Visitors
get a real HTTPS certificate, and nothing on this computer has to accept
incoming connections from the internet.

Install once:  brew install cloudflared      (macOS)
               winget install Cloudflare.cloudflared   (Windows)
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from typing import Callable, Optional


URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class Tunnel:
    def __init__(self, local_port: int, on_url: Callable[[str], None],
                 on_problem: Callable[[str], None]) -> None:
        self.local_port = local_port
        self.on_url = on_url
        self.on_problem = on_problem
        self.process: Optional[subprocess.Popen] = None

    @staticmethod
    def available() -> bool:
        return shutil.which("cloudflared") is not None

    def start(self) -> None:
        if not self.available():
            self.on_problem("cloudflared isn't installed. Run: brew install cloudflared")
            return
        self.process = subprocess.Popen(
            [
                "cloudflared", "tunnel", "--no-autoupdate",
                "--url", "http://127.0.0.1:%d" % self.local_port,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self) -> None:
        found = False
        for line in self.process.stdout:
            if not found:
                match = URL_PATTERN.search(line)
                if match:
                    found = True
                    self.on_url(match.group(0))
        if self.process.poll() is not None and self.process.returncode not in (0, -15):
            self.on_problem("The tunnel stopped. Check your internet connection and restart the server.")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
