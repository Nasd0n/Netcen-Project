# Escape Plan

A two-player hide-and-chase game over TCP sockets. One player is the warder, the other is the prisoner. The prisoner wins by reaching the tunnel, and the warder wins by catching the prisoner first.

Written in Python 3.9+ with Tkinter. All traffic between the client and server is encrypted with TLS.

## Files

| File | What it does |
| --- | --- |
| `server.py` | Socket server + dashboard window (who's online, live map, reset button) |
| `client.py` | Player window: nickname screen, board, timer, Scout hints, rematch |
| `game.py` | Game rules: map generation, turns, timer, win checks, scores |
| `ai.py` | Scout, the hint helper (breadth-first search, plus the exact solver in Classic) |
| `solver.py` | Exact game solvers for Classic and Night Escape, used to deal fair starts |
| `security.py` | TLS certificate creation and certificate pinning |
| `web.py` | Hand-written HTTP + WebSocket server for browser players |
| `web/index.html` | The browser version of the game (works on phones too) |
| `tunnel.py` | Starts the Cloudflare tunnel that gives you a public invite link |
| `protocol.py` | JSON-lines messages; one thread per connection |
| `theme.py` | Colors, fonts, rounded buttons, character drawings |
| `config.py` | Server IP and port (players never type these) |
| `tools/balance.py` | Bot-vs-bot simulation that reports win rates (not run by the tests) |

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
- The server picks the roles at random. The warder always moves first.
- Starts are random but fair. An exact solver checks every Classic start, and neither side may have a guaranteed win from it (with perfect play from both, nobody could force the result). The players start at least 3 walking steps apart (the tunnel counts as a wall for the warder), and the prisoner starts at least 3 walking steps from the tunnel. Maps where the tunnel has only one way in (the warder could camp on it), or where the tunnel cuts the yard in two, are thrown away and a new map is made.
- You get 10 seconds per turn. To move, click a highlighted square or use the arrow keys / WASD.
- Instead of moving you can **wait** (the Wait button or Space). A wait uses up your turn exactly like a move, you just stay on your square. Both players can wait, in both modes. To play the assignment's original rule (you must move every turn), set `ALLOW_WAIT = False` in `game.py`.
- The warder can't enter the tunnel, and the prisoner can't walk into the warder.
- The winner gets +1. If both players press Rematch, the last winner plays warder next.
- The server's Reset button starts a fresh match and sets both scores back to 0.

## Night Escape mode

Classic mode is the assignment's game: 5×5, 5 walls, 1 tunnel, the warder moves first, up/down/left/right only, 10-second turns. The one addition is waiting (see above), which `ALLOW_WAIT = False` turns off again. Night Escape is an extra mode. Switch between them on the server dashboard at any time; switching starts a new match and keeps the scores.

In Classic, a careful prisoner almost always gets away. Night Escape was designed by solving and simulating thousands of games (an exact solver plus bots). Every rule below is there for a reason.

| Rule | How it works |
| --- | --- |
| **Big dark yard** | 10×10 with 20 walls. The warder's lantern lights only the 8 squares around them. The prisoner spots the warder from 2 squares away (one more than the warder). |
| **Key** | The tunnel is locked until the prisoner picks up the key. Only the prisoner can see where it is. (When the warder knew the key's location, they just guarded it.) The key always has two separate ways out: at least 2 open squares next to it, and two routes to the tunnel that share no square, so no single square (the warder or a barricade) cuts it off from the tunnel. That also rules out a tunnel with only one way in. Nor can one warder two squares from the key light every way out with its 3×3 lantern while the key stays dark (`KEY_LANTERN_CHECK`). Before these rules, about a quarter of the keys sat in a dead end or a pocket: the footprints led the warder there, and it waited at the only way out. |
| **Lock-on grab** | When the prisoner is in the warder's lantern light, a target locks onto their square and the warder clicks it to grab them (Q/E/Z/C also grab diagonally). If the warder's step brings the prisoner into view, the warder gets one extra click that turn. If they don't click before the timer runs out, the prisoner slips away. On a board this big the warder almost never caught anyone without this step-then-grab. |
| **Footprints** | The warder sees where the prisoner stood 2 turns ago, anywhere on the map (smoke hides it). Without footprints the warder just camped at the tunnel. |
| **Items** | The prisoner starts every match with a **Smoke bomb**. There are 4 item boxes on the map, 2 per player, and each player only sees their own. Each box shows the item inside: every player gets one box of each of their items. Warder: **Searchlight** (lights the whole yard for 2 warder turns) and **Barricade** (blocks a square within 2 for 3 rounds). Prisoner: **Smoke bomb** (invisible, no footprints, can't be seen for 3 moves) and **Sprint** (2 steps in one turn). |
| **Lockdown** | If the prisoner hasn't escaped after 60 rounds, the prison locks down and the warder wins. |
| **Waiting** | Both sides may wait. A prisoner's wait ends their turn like a step: it leaves a footprint, counts down smoke, counts toward the lockdown, ticks barricades and ends a sprint. A warder's wait never gives the step-and-grab extra click (that only follows a step). A wait the other side can't see is announced and sent exactly like a move in the dark, so the network messages don't give it away. |
| **Fair deals** | The warder starts at least as close to the tunnel as the prisoner, but never right next to it. An exact game-tree solver (`solver.py`, with waiting for both sides) rejects any deal where the prisoner could escape by force. |
| **Start hint** | When the prisoner's first turn begins (after the warder's opening move), the prisoner sees a red 3×3 block labelled "Warder is somewhere here". The warder is in it, but not always in the middle: the block is picked at random once per match (and slides inward at the edges), so it can't be narrowed down by comparing updates. It stays as a faded "last known area" for one more turn (`START_HINT_TURNS`). Only the prisoner's view carries it, never the warder's exact square, and Scout plans the prisoner's route around it. |

Both sides' views are filtered on the server. The warder never receives the prisoner's position or the key while it's dark, and the prisoner never receives a warder who is out of sight. Moves in the dark are announced without a direction, so a modified client can't cheat by reading network traffic.

**Timeouts (both modes):** if a player doesn't move within 10 seconds, they take a random legal step, never a wait, so going idle isn't rewarded. A prisoner's random step avoids the lantern when it can.

## Security

Everything goes over TLS (1.2 or newer; on current Python it negotiates TLS 1.3).

- **Encryption:** someone else on the same Wi-Fi can't read or change the moves or nicknames.
- **Server identity (certificate pinning):** the client only trusts the exact certificate in `certs/server.crt`. It also checks the SHA-256 fingerprint after the handshake. A fake server that doesn't have `server.key` is rejected, even if it uses the same IP address. The first few bytes of the fingerprint appear on the server dashboard, so you can compare them.
- **Plain TCP is refused:** a connection that doesn't complete a TLS handshake within 8 seconds is dropped.
- **Input limits:** messages over 4 KB, more than 8 messages per second, unknown commands and invalid directions are all rejected. Nicknames are cleaned (no control characters, max 16 characters, no duplicates).
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
| Scout: a breadth-first-search hint that shows the best next square on the board, or tells you to wait when staying put is best. In Night Escape it plans around the grab zone, and a warder who can't see follows the footprints. Browser players get the same hint from the server (`ai.py`), computed only from their own view. | ✓ |
| Wait instead of moving (button or Space), with `ALLOW_WAIT` to switch back to the must-move rule | |
| Exact game-tree solvers that deal only fair starts (no forced wins) in Classic and Night Escape | ✓ |
| Scout in Classic: ranks every step and waiting with the exact solver (take a forced win, avoid a forced loss), then by walking distance | ✓ |
| Night Escape mode: 10×10 fog of war for both sides, with server-side hidden information | |
| Night Escape: secret key that unlocks the tunnel | |
| Night Escape: step-and-grab + footprints | |
| Night Escape: private item boxes that show their item (Searchlight, Barricade, Smoke bomb, Sprint), and a starting Smoke bomb for the prisoner | |
| Night Escape: 60-round lockdown | |
| Night Escape: the prisoner's start hint (a 3×3 block holding the warder) | |
| Random step when a player's 10 seconds run out | |
| TLS encryption with certificate pinning | |
| Public invite link: play in any browser, even on a phone, via a secure tunnel | |
| Click-to-move with highlighted legal squares | |
| Lobby: see who's online before joining, with live updates | |
| Server dashboard with a live mini-map of the current match | |
| Fair random starting positions | |
| Countdown ring timer, footprints showing the last move, and a Win/Lost card | |
| "Try again" reconnect button if the server goes down | |

## Balance

`python tools/balance.py` plays Scout against Scout through the real game engine. Each bot sees only its own view, and 15% of the moves are random so the bots make mistakes. The numbers are the warder's share of the games that finished.

| Classic | Bots' Scout | Warder |
| --- | --- | --- |
| Before: must move, straight-line distance starts | breadth-first only | 42% |
| Waiting allowed, same starts | breadth-first only | 49% |
| Waiting allowed, same starts | with the solver | 43% |
| + solver-checked starts (no guaranteed wins) | with the solver | 60% |
| + walking distances, no maps split by the tunnel | with the solver | 60% |
| + at least 2 ways into the tunnel (**final**) | with the solver | **58%** |
| Final rules, but must move (`ALLOW_WAIT = False`) | with the solver | 24% |

With the old starts, 40% of Classic starts were a guaranteed prisoner win and 9% a guaranteed warder win (with waiting allowed; 40% and 32% without). The final rules hold at 56-61% across 5-25% random moves. The breadth-first Scout made a poor prisoner (it walked right next to the warder), so with solver-checked starts it handed the warder 63%. That's why Scout's Classic advice now uses the solver for both sides.

Two rules were tried and dropped. "The warder can reach a square next to the tunnel no later than the prisoner reaches the tunnel" changed nothing, because the solver check already rules those starts out. Capping the prisoner's walk to the tunnel at 4-5 steps moved the result by about 1%. Requiring 3 ways into the tunnel gives 56%, but it throws away about 40% of maps; `MIN_TUNNEL_ENTRANCES` in `game.py` sets it.

Without waiting, solver-checked starts don't fix the balance: the distance between the players changes by exactly 1 every move, so on an even distance the warder can never be next to the prisoner on its own turn.

| Night Escape (bots also use items) | Warder |
| --- | --- |
| Before waiting and the start hint | 87% |
| With waiting and the start hint | 86% |
| + the key has two separate ways out (6,000 games) | 85.5% |
| + no single lantern spot watches both (**final**, 6,000 games) | **84%** |

Of the keys the old deal picked, 26% had a dead end (6%) or a single square that cut them off, and another 18% could be watched from one lantern spot. Dealing now throws away about 1 map in 8 (the tunnel itself is cut off, so no key spot passes) and takes about 39 ms instead of 35.

These Night numbers say more about the bots than the rules. Scout's night prisoner doesn't remember where it last saw the warder, so it walks into the dark. Even with no random moves at all it loses 84%. With the step-and-grab removed entirely the warder still wins 81% of bot games. Night Escape needs a smarter prisoner bot, or real playtests, before its rules are tuned further.

## Tests

```bash
python3 -m pytest -q
```
Waiting has its own tests (`test_wait.py` for Classic, with both `ALLOW_WAIT` settings, and the Night cases in `test_night.py`): a wait passes the turn without moving, can't be done out of turn, and in Night Escape leaves footprints, counts down smoke and counts toward the lockdown. A hidden wait produces exactly the same real network messages as a move in the dark. `test_solver.py` checks both solvers on positions you can work out by hand. The fair-start test checks over many seeds that no Classic start is a guaranteed win for either side. The start-hint tests check that only the prisoner gets the 3×3 block, that it holds the warder (not always in the middle), stays put and disappears on time, and that the prisoner's real messages never carry the warder's square. The key tests check over many seeds that the key has at least 2 open neighbors, that blocking any single square never cuts it off from the tunnel, that no lantern spot two from the key watches every way out, and that dealing a Night match stays quick.

The tests cover the game rules, Scout, and real TLS connections on localhost. The TLS tests check that two players can join and play, that moves out of turn are refused, that plain TCP and oversized messages are rejected, and that the client refuses a server with the wrong certificate. The Night Escape tests cover:
- what each role can see
- the key and the locked tunnel
- the lock-on grab and the extra click after a step
- footprints
- all four items, private boxes and the lockdown
- random steps on timeout, and fair dealing (checked against the solver)
- Scout's night advice
- that neither side's real network messages leak hidden positions There are also browser-side tests: a wrong invite key or another website's origin gets a 403, only the game page is served, and a browser player can join and get the game state.
