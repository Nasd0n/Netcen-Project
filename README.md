# Escape Plan

A two-player hide-and-chase game over TCP sockets. One player is the warder, the other is the prisoner. The prisoner wins by reaching the tunnel, and the warder wins by catching the prisoner first.

Written in Python 3.9+ with Tkinter. All traffic between the client and server is encrypted with TLS.

## Files

| File | What it does |
| --- | --- |
| `server.py` | Socket server + dashboard window (who's online, live map, reset button) |
| `client.py` | Player window: nickname and side screen, board, timer, Scout hints, rematch |
| `game.py` | Game rules: map generation, turns, timer, win checks, scores |
| `ai.py` | Scout, the path-finding helper (breadth-first search) |
| `solver.py` | Exact Night Escape solver used to deal fair starts |
| `security.py` | TLS certificate creation and certificate pinning |
| `web.py` | Hand-written HTTP + WebSocket server for browser players |
| `web/index.html` | The browser version of the game (works on phones too) |
| `tunnel.py` | Starts the Cloudflare tunnel that gives you a public invite link |
| `protocol.py` | JSON-lines messages; one thread per connection |
| `theme.py` | Colors, fonts, rounded buttons, character drawings |
| `config.py` | Server IP and port (players never type these) |

## Running it

**1. On the server computer**

```bash
python3 server.py
```

On the first run it creates `certs/server.crt` and `certs/server.key`. The dashboard shows the IP address that clients should use.

**2. Set up the second computer**

Copy the project folder to it, and make sure it includes `certs/server.crt`.
**Do not copy `certs/server.key`.** That key stays on the server.

In `config.py`, set `SERVER_HOST` to the IP address shown on the server dashboard.

**3. Start a client on each computer**

```bash
python3 client.py
```

Type a nickname and press Join. The match starts once two players are in.

If you're testing on one machine, leave `SERVER_HOST = "127.0.0.1"`.

## Invite link: play from any browser

Friends don't need Python or any files. They open a link in their browser and play, on a laptop or a phone, from anywhere.

1. Install the tunnel tool once on the server computer:
   ```bash
   brew install cloudflared
   ```
2. Start the server as usual (`python3 server.py`). After a few seconds the **Invite link** card shows a link like
   `https://some-random-words.trycloudflare.com/#key=...`
3. Click **Copy link** and send it to your friend (or let them scan the QR code, if you have `pip install qrcode`).

Browser players and desktop (`client.py`) players can play against each other. You get a new link each time the server starts, and the old link stops working.

## How to play

- The board is 5×5, with 19 open squares, 5 walls and 1 tunnel. Every new match gets a new random map. The server checks that every open square can be reached, so the map is never deadlocked.
- Pick a side: **Warder**, **Prisoner** or **Either** (the default). You choose on the nickname screen and can change it any time on the game screen, including on the Win/Lost card before pressing Rematch. You can see what your opponent picked.
- A pick takes effect when the next match starts (rematch, mode switch or server reset), never in the middle of a match. Picks that fit together (Warder + Prisoner, Warder + Either, Prisoner + Either) decide the roles. If you both want the same role (both Warder or both Prisoner), the winner of the most recent finished match gets it and the other player gets the other role. Example: you both pick Prisoner and Alex won the last match, so Alex is the prisoner. If there is no last winner in the room (first match, after the server's Reset, or the last winner has left), it is decided at random. A mode switch keeps the scores, so the last winner still counts after one. If you both picked Either, the usual rule decides: random roles for a first match, and the last winner plays warder on a rematch. Both players are told which rule was used.
- The warder always moves first.
- Starting spots are random but fair: the two players start at least 3 steps apart, and the prisoner starts at least 3 steps from the tunnel.
- You get 10 seconds per turn. To move, click a highlighted square or use the arrow keys / WASD.
- The warder can't enter the tunnel, and the prisoner can't walk into the warder.
- The winner gets +1. If both players press Rematch, a new match starts with roles decided as above.
- The server's Reset button starts a fresh match and sets both scores back to 0. Its **Swap sides next match** button sets both players' picks to the opposite of their current roles.

## Night Escape mode

Classic mode is the assignment's game, unchanged: 5×5, 5 walls, 1 tunnel. Night Escape is an extra mode. Switch between them on the server dashboard at any time; switching starts a new match and keeps the scores.

In Classic, a careful prisoner almost always gets away. Night Escape was designed by solving and simulating thousands of games (an exact solver plus bots). Every rule below is there for a reason.

| Rule | How it works |
| --- | --- |
| **Big dark yard** | 10×10 with 20 walls. The warder's lantern lights only the 8 squares around them. The prisoner spots the warder from 2 squares away (one more than the warder). |
| **Key** | The tunnel is locked until the prisoner picks up the key. Only the prisoner can see where it is. (When the warder knew the key's location, they just guarded it.) |
| **Lock-on grab** | When the prisoner is in the warder's lantern light, a target locks onto their square and the warder clicks it to grab them (Q/E/Z/C also grab diagonally). If the warder's step brings the prisoner into view, the warder gets one extra click that turn. If they don't click before the timer runs out, the prisoner slips away. On a board this big the warder almost never caught anyone without this step-then-grab. |
| **Footprints** | The warder sees where the prisoner stood 2 turns ago, anywhere on the map (smoke hides it). Without footprints the warder just camped at the tunnel. |
| **Items** | The prisoner starts every match with a **Smoke bomb**. There are 4 item boxes on the map, 2 per player, and each player only sees their own. Each box shows the item inside: every player gets one box of each of their items. Warder: **Searchlight** (lights the whole yard for 2 warder turns) and **Barricade** (blocks a square within 2 for 3 rounds). Prisoner: **Smoke bomb** (invisible, no footprints, can't be seen for 3 moves) and **Sprint** (2 steps in one turn). |
| **Lockdown** | If the prisoner hasn't escaped after 60 rounds, the prison locks down and the warder wins. |
| **Fair deals** | The warder starts at least as close to the tunnel as the prisoner, but never right next to it. An exact game-tree solver (`solver.py`) rejects any deal where the prisoner could escape by force. |

Both sides' views are filtered on the server. The warder never receives the prisoner's position or the key while it's dark, and the prisoner never receives a warder who is out of sight. Moves in the dark are announced without a direction, so a modified client can't cheat by reading network traffic.

**Timeouts (both modes):** if a player doesn't move within 10 seconds, they take a random legal step. A prisoner's random step avoids the lantern when it can.

## Security

Everything goes over TLS (1.2 or newer; on current Python it negotiates TLS 1.3).

- **Encryption:** someone else on the same Wi-Fi can't read or change the moves or nicknames.
- **Server identity (certificate pinning):** the client only trusts the exact certificate in `certs/server.crt`. It also checks the SHA-256 fingerprint after the handshake. A fake server that doesn't have `server.key` is rejected, even if it uses the same IP address. The first few bytes of the fingerprint appear on the server dashboard, so you can compare them.
- **Plain TCP is refused:** a connection that doesn't complete a TLS handshake within 8 seconds is dropped.
- **Input limits:** messages over 4 KB, more than 8 messages per second, unknown commands, invalid directions and unknown sides are all rejected. Nicknames are cleaned (no control characters, max 16 characters, no duplicates).
- **Timeouts:** the server disconnects clients that don't pick a name within 90 seconds or stay silent for 45 seconds. Clients send a small keep-alive every 15 seconds. At most 8 connections are allowed at once.
- **Server-side rules:** the server checks every move. A modified client can't teleport or move out of turn.

**Invite link security**

- **Real HTTPS:** Cloudflare provides a real HTTPS certificate, so browsers show a padlock and no warning.
- **Nothing opened to the internet:** the web part of the server only listens on `127.0.0.1`. `cloudflared` makes an outgoing connection to Cloudflare, so there's no port forwarding and no firewall hole. A secure WebSocket (`wss://`) carries the game.
- **Secret invite key:** the link ends in a random key that changes every time the server starts. Browsers without the key are refused (HTTP 403). The key is in the `#` part of the link, so it isn't sent when the page loads and never appears in a Referer header.
- **Blocks other websites:** a page on another site can't open a game connection (the `Origin` header is checked).
- **Only one file served:** the web server serves only the game page. Any other path gets a 404, so nothing else in the folder can be downloaded.
- **Locked-down page:** strict security headers (Content-Security-Policy, no framing, no referrer). Nicknames are inserted as plain text, so a name like `<script>` can't run code.
- **Same checks for everyone:** browser players get the same size limits, rate limits and server-side move checks as desktop players.

To make a new certificate (for example after `server.key` has leaked), run:

```bash
python3 security.py --force
```

After that, copy the new `certs/server.crt` to the clients again.

## Extra features

| Feature | AI? |
| --- | --- |
| Scout: a breadth-first-search hint that shows the best next square on the board. In Night Escape it plans around the grab zone, and a warder who can't see follows the footprints. | ✓ |
| Night Escape: exact game-tree solver that deals only fair starts (no forced wins) | ✓ |
| Night Escape mode: 10×10 fog of war for both sides, with server-side hidden information | |
| Night Escape: secret key that unlocks the tunnel | |
| Night Escape: step-and-grab + footprints | |
| Night Escape: private item boxes that show their item (Searchlight, Barricade, Smoke bomb, Sprint), and a starting Smoke bomb for the prisoner | |
| Night Escape: 60-round lockdown | |
| Random step when a player's 10 seconds run out | |
| TLS encryption with certificate pinning | |
| Public invite link: play in any browser, even on a phone, via a secure tunnel | |
| Click-to-move with highlighted legal squares | |
| Lobby: see who's online before joining, with live updates | |
| Choose your side (Warder, Prisoner or Either), see your opponent's pick, swap sides from the server dashboard | |
| Server dashboard with a live mini-map of the current match | |
| Fair random starting positions | |
| Countdown ring timer, footprints showing the last move, and a Win/Lost card | |
| "Try again" reconnect button if the server goes down | |

## Tests

```bash
python3 -m pytest -q
```

The tests cover the game rules, Scout, and real TLS connections on localhost. The side-picking tests cover every combination of picks, the random and last-winner fallbacks, and that a change waits for the next match. The TLS tests check that two players can join and play, that side picks travel both ways and bad ones are refused, that moves out of turn are refused, that plain TCP and oversized messages are rejected, and that the client refuses a server with the wrong certificate. The Night Escape tests cover:
- what each role can see
- the key and the locked tunnel
- the lock-on grab and the extra click after a step
- footprints
- all four items, private boxes and the lockdown
- random steps on timeout, and fair dealing (checked against the solver)
- Scout's night advice
- that neither side's real network messages leak hidden positions There are also browser-side tests: a wrong invite key or another website's origin gets a 403, only the game page is served, and a browser player can join, get the game state and pick a side.
