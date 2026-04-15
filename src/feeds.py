import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests
import websockets

import config


class State:
    def __init__(self):
        self.bids: list[tuple[float, float]] = []
        self.asks: list[tuple[float, float]] = []
        self.mid: float = 0.0
        self.trades: list[dict] = []
        self.klines: list[dict] = []
        self.cur_kline: dict | None = None

        self.pm_up_id: str | None = None
        self.pm_dn_id: str | None = None
        self.pm_up: float | None = None
        self.pm_dn: float | None = None

        # Feed health / recovery state
        self.pm_market_slug: str | None = None
        self.pm_feed_connected: bool = False
        self.pm_feed_guard_active: bool = True
        self.pm_last_msg_ts: float = 0.0
        self.pm_last_pong_ts: float = 0.0
        self.pm_last_refresh_ts: float = 0.0
        self.pm_last_refresh_reason: str = "startup"
        self.pm_last_error: str = ""
        self.pm_last_quote_ts: float = 0.0
        self.pm_empty_msg_count: int = 0
        self.pm_invalid_price_count: int = 0
        self.pm_reconnect_count: int = 0
        self.pm_quote_source: str = ""
        self.pm_last_book_market: str | None = None


OB_POLL_INTERVAL = 2
PM_HEARTBEAT_SEC = int(os.getenv("PM_HEARTBEAT_SEC", "10"))
PM_RECV_TIMEOUT_SEC = int(os.getenv("PM_RECV_TIMEOUT_SEC", "15"))
PM_EMPTY_REFRESH_THRESHOLD = int(os.getenv("PM_EMPTY_REFRESH_THRESHOLD", "3"))
PM_INVALID_REFRESH_THRESHOLD = int(os.getenv("PM_INVALID_REFRESH_THRESHOLD", "5"))
PM_REFRESH_COOLDOWN_SEC = int(os.getenv("PM_REFRESH_COOLDOWN_SEC", "15"))
PM_RETRY_DELAY_SEC = int(os.getenv("PM_RETRY_DELAY_SEC", "5"))
PM_STALE_QUOTE_SEC = int(os.getenv("PM_STALE_QUOTE_SEC", "20"))
PM_DISCOVERY_LIMIT = int(os.getenv("PM_DISCOVERY_LIMIT", "100"))
PM_DISCOVERY_MAX_PAGES = int(os.getenv("PM_DISCOVERY_MAX_PAGES", "5"))
PM_DISCOVERY_NEAR_WINDOW_SEC = int(os.getenv("PM_DISCOVERY_NEAR_WINDOW_SEC", "1200"))
PM_DISCOVERY_PAST_GRACE_SEC = int(os.getenv("PM_DISCOVERY_PAST_GRACE_SEC", "120"))


async def ob_poller(symbol: str, state: State):
    url = f"{config.BINANCE_REST}/depth"
    print(f" [Binance OB] polling {symbol} every {OB_POLL_INTERVAL}s")
    while True:
        try:
            resp = requests.get(url, params={"symbol": symbol, "limit": 20}, timeout=3).json()
            state.bids = [(float(p), float(q)) for p, q in resp["bids"]]
            state.asks = [(float(p), float(q)) for p, q in resp["asks"]]
            if state.bids and state.asks:
                state.mid = (state.bids[0][0] + state.asks[0][0]) / 2
        except Exception:
            pass
        await asyncio.sleep(OB_POLL_INTERVAL)


async def binance_feed(symbol: str, kline_iv: str, state: State):
    sym = symbol.lower()
    streams = "/".join([
        f"{sym}@trade",
        f"{sym}@kline_{kline_iv}",
    ])
    url = f"{config.BINANCE_WS}?streams={streams}"

    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=60, close_timeout=10) as ws:
                print(f" [Binance WS] connected – {symbol}")
                while True:
                    try:
                        data = json.loads(await ws.recv())
                        stream = data.get("stream", "")
                        pay = data["data"]

                        if "@trade" in stream:
                            state.trades.append(
                                {
                                    "t": pay["T"] / 1000.0,
                                    "price": float(pay["p"]),
                                    "qty": float(pay["q"]),
                                    "is_buy": not pay["m"],
                                }
                            )
                            if len(state.trades) > 5000:
                                cut = time.time() - config.TRADE_TTL
                                state.trades = [t for t in state.trades if t["t"] >= cut]
                        elif "@kline" in stream:
                            k = pay["k"]
                            candle = {
                                "t": k["t"] / 1000.0,
                                "o": float(k["o"]),
                                "h": float(k["h"]),
                                "l": float(k["l"]),
                                "c": float(k["c"]),
                                "v": float(k["v"]),
                            }
                            state.cur_kline = candle
                            if k["x"]:
                                state.klines.append(candle)
                                state.klines = state.klines[-config.KLINE_MAX :]
                    except websockets.exceptions.ConnectionClosed:
                        print(f" [Binance WS] connection closed, reconnecting...")
                        break
        except Exception as e:
            print(f" [Binance WS] connection error: {e}, reconnecting in 5s...")
            await asyncio.sleep(5)


async def bootstrap(symbol: str, interval: str, state: State):
    resp = requests.get(
        f"{config.BINANCE_REST}/klines",
        params={"symbol": symbol, "interval": interval, "limit": config.KLINE_BOOT},
    ).json()
    state.klines = [
        {
            "t": r[0] / 1e3,
            "o": float(r[1]),
            "h": float(r[2]),
            "l": float(r[3]),
            "c": float(r[4]),
            "v": float(r[5]),
        }
        for r in resp
    ]
    print(f" [Binance] loaded {len(state.klines)} historical candles")


_MONTHS = [
    "",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


def _et_now() -> datetime:
    utc = datetime.now(timezone.utc)
    year = utc.year
    mar1_dow = datetime(year, 3, 1).weekday()
    mar_sun = 1 + (6 - mar1_dow) % 7
    dst_start = datetime(year, 3, mar_sun + 7, 2, 0, 0, tzinfo=timezone.utc)
    nov1_dow = datetime(year, 11, 1).weekday()
    nov_sun = 1 + (6 - nov1_dow) % 7
    dst_end = datetime(year, 11, nov_sun, 6, 0, 0, tzinfo=timezone.utc)
    offset = timedelta(hours=-4) if dst_start <= utc < dst_end else timedelta(hours=-5)
    return utc + offset


def _to_12h(hour24: int) -> str:
    if hour24 == 0:
        return "12am"
    if hour24 < 12:
        return f"{hour24}am"
    if hour24 == 12:
        return "12pm"
    return f"{hour24 - 12}pm"


def _build_slug(coin: str, tf: str, now_utc: datetime | None = None) -> str | None:
    now_utc = now_utc or datetime.now(timezone.utc)
    now_ts = int(now_utc.timestamp())
    et = _et_now()

    if tf == "5m":
        ts = (now_ts // 300) * 300
        return f"{config.COIN_PM[coin]}-updown-5m-{ts}"
    if tf == "15m":
        ts = (now_ts // 900) * 900
        return f"{config.COIN_PM[coin]}-updown-15m-{ts}"
    if tf == "4h":
        ts = ((now_ts - 3600) // 14400) * 14400 + 3600
        return f"{config.COIN_PM[coin]}-updown-4h-{ts}"
    if tf == "1h":
        return (
            f"{config.COIN_PM_LONG[coin]}-up-or-down-"
            f"{_MONTHS[et.month]}-{et.day}-{_to_12h(et.hour)}-et"
        )
    if tf == "daily":
        resolution = et.replace(hour=12, minute=0, second=0, microsecond=0)
        target = et if et < resolution else et + timedelta(days=1)
        return f"{config.COIN_PM_LONG[coin]}-up-or-down-on-{_MONTHS[target.month]}-{target.day}"
    return None


def _candidate_slugs(coin: str, tf: str) -> list[str]:
    now = datetime.now(timezone.utc)
    slugs: list[str] = []

    base = _build_slug(coin, tf, now)
    if base:
        slugs.append(base)

    if tf in {"5m", "15m", "4h"}:
        delta = {"5m": 300, "15m": 900, "4h": 14400}[tf]
        for sign in (-1, 1):
            alt = _build_slug(coin, tf, now + timedelta(seconds=sign * delta))
            if alt and alt not in slugs:
                slugs.append(alt)

    return slugs


def _extract_token_ids(event_data: dict[str, Any]) -> tuple[str | None, str | None]:
    try:
        ids = json.loads(event_data["markets"][0]["clobTokenIds"])
        if isinstance(ids, list) and len(ids) >= 2:
            return ids[0], ids[1]
    except Exception:
        pass
    return None, None


def fetch_pm_event_data_by_slug(slug: str) -> dict | None:
    try:
        data = requests.get(config.PM_GAMMA, params={"slug": slug, "limit": 1}, timeout=5).json()
        if not data:
            return None
        event = data[0]
        ticker = str(event.get("ticker") or "")
        event_slug = str(event.get("slug") or "")
        if ticker == slug or event_slug == slug:
            return event
        return None
    except Exception as e:
        print(f" [PM] event fetch failed ({slug}): {e}")
        return None


def _event_text(event: dict[str, Any]) -> str:
    markets = event.get("markets") or []
    market0 = markets[0] if markets and isinstance(markets[0], dict) else {}
    parts = [
        event.get("ticker"),
        event.get("slug"),
        event.get("title"),
        event.get("question"),
        market0.get("question"),
        market0.get("slug"),
    ]
    return " | ".join(_safe_lower(v) for v in parts if v)


def _event_end_dt(event: dict[str, Any]) -> datetime | None:
    return _parse_gamma_dt(
        event.get("endDate")
        or event.get("end_date")
        or event.get("endTime")
        or event.get("end_time")
        or event.get("end")
    )


def _has_updown_outcomes(event: dict[str, Any]) -> bool:
    markets = event.get("markets") or []
    market0 = markets[0] if markets and isinstance(markets[0], dict) else {}
    outcomes = market0.get("outcomes")
    if not outcomes:
        return False
    try:
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        labels = {_safe_lower(x) for x in outcomes if x is not None}
        return "up" in labels and "down" in labels
    except Exception:
        return False


def _score_active_event_for_tf(event: dict[str, Any], coin: str, tf: str) -> int:
    text = _event_text(event)
    if not text:
        return -999

    coin_short = _safe_lower(config.COIN_PM[coin])
    coin_long = _safe_lower(config.COIN_PM_LONG[coin])
    if coin_short not in text and coin_long not in text:
        return -999

    if event.get("active") is not True or event.get("closed") is not False:
        return -999

    score = 0
    end_dt = _event_end_dt(event)
    now = datetime.now(timezone.utc)

    if tf == "15m":
        strong_slug = re.compile(rf"\b{re.escape(coin_short)}-updown-15m(?:-\d+)?\b")
        has_updown = ("updown" in text) or ("up or down" in text)
        has_15m = any(tok in text for tok in ["15m", "15 min", "15-min", "15 minute", "15 minutes"])
        bad_terms = [
            "will bitcoin", "what price will", "what price", "price on",
            " hit ", " reach ", " above ", " below ", " first",
            "all time high", "150k", "100k", "200k", "end of", "by ",
        ]

        if any(term in f" {text} " for term in bad_terms):
            return -999
        if not (strong_slug.search(text) or (has_updown and has_15m)):
            return -999

        if strong_slug.search(text):
            score += 180
        if has_updown:
            score += 80
        if has_15m:
            score += 80
        if _has_updown_outcomes(event):
            score += 50

        if end_dt is not None:
            delta = (end_dt - now).total_seconds()
            if delta < -PM_DISCOVERY_PAST_GRACE_SEC:
                return -999
            if 0 <= delta <= PM_DISCOVERY_NEAR_WINDOW_SEC:
                score += 120
            elif -PM_DISCOVERY_PAST_GRACE_SEC <= delta < 0:
                score += 40
            elif delta <= PM_DISCOVERY_NEAR_WINDOW_SEC * 2:
                score += 20
            else:
                score -= 40

        score += 20 if event.get("markets") else 0
        return score

    ticker = _safe_lower(event.get("ticker") or event.get("slug"))
    prefix_short = f"{coin_short}-updown-{tf}-"
    prefix_long = f"{coin_long}-up-or-down"
    if ticker.startswith(prefix_short):
        score += 100
    if prefix_long in ticker:
        score += 50
    if event.get("markets"):
        score += 10
    return score


def _active_event_sort_key(event: dict[str, Any], coin: str, tf: str) -> tuple[int, float, str]:
    score = _score_active_event_for_tf(event, coin, tf)
    end_dt = _event_end_dt(event)
    now = datetime.now(timezone.utc)
    if end_dt is None:
        proximity = float("inf")
    else:
        proximity = abs((end_dt - now).total_seconds())
    slug = str(event.get("ticker") or event.get("slug") or "")
    return (score, -proximity, slug)


def fetch_pm_event_data_active(coin: str, tf: str) -> dict | None:
    try:
        candidates: list[dict[str, Any]] = []
        for page in range(PM_DISCOVERY_MAX_PAGES):
            offset = page * PM_DISCOVERY_LIMIT
            data = requests.get(
                config.PM_GAMMA,
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": PM_DISCOVERY_LIMIT,
                    "offset": offset,
                    "order": "end_date",
                    "ascending": "true",
                },
                timeout=8,
            ).json()
            if not isinstance(data, list) or not data:
                break

            page_hits = [e for e in data if _score_active_event_for_tf(e, coin, tf) > 0]
            candidates.extend(page_hits)

            if len(data) < PM_DISCOVERY_LIMIT:
                break

        if not candidates:
            return None

        candidates.sort(key=lambda e: _active_event_sort_key(e, coin, tf), reverse=True)
        chosen = candidates[0]
        score = _score_active_event_for_tf(chosen, coin, tf)
        end_dt = _event_end_dt(chosen)
        end_txt = end_dt.isoformat() if end_dt else "unknown"
        slug = str(chosen.get("ticker") or chosen.get("slug") or "")
        print(f" [PM] active discovery selected slug={slug} score={score} end={end_txt}")
        return chosen
    except Exception as e:
        print(f" [PM] active market discovery failed: {e}")
        return None


def fetch_pm_event_data(coin: str, tf: str) -> dict | None:
    for slug in _candidate_slugs(coin, tf):
        event = fetch_pm_event_data_by_slug(slug)
        if event:
            return event

    event = fetch_pm_event_data_active(coin, tf)
    if event is None:
        print(f" [PM] no active market for {coin} {tf}")
    return event


def fetch_pm_tokens(coin: str, tf: str) -> tuple[str | None, str | None]:
    event_data = fetch_pm_event_data(coin, tf)
    if event_data is None:
        return None, None
    up, dn = _extract_token_ids(event_data)
    if not up:
        print(" [PM] token extraction failed")
    return up, dn


def fetch_pm_tokens_robust(coin: str, tf: str) -> tuple[str | None, str | None, str | None]:
    event_data = fetch_pm_event_data(coin, tf)
    if event_data is None:
        return None, None, None
    up, dn = _extract_token_ids(event_data)
    slug = str(event_data.get("ticker") or event_data.get("slug") or "") or None
    return up, dn, slug


async def _pm_heartbeat(ws, state: State):
    try:
        while True:
            try:
                await asyncio.sleep(PM_HEARTBEAT_SEC)
                await ws.send("PING")
                state.pm_last_pong_ts = time.time()
            except asyncio.CancelledError:
                raise
            except Exception:
                break
    except asyncio.CancelledError:
        pass


def _pm_reset_quotes(state: State):
    state.pm_up = None
    state.pm_dn = None


def _pm_mark_guard(state: State, active: bool, reason: str = ""):
    state.pm_feed_guard_active = active
    if reason:
        state.pm_last_refresh_reason = reason


def _pm_price_valid(price: float | None) -> bool:
    return price is not None and 0.0 < price < 1.0


def _pm_quotes_healthy(state: State) -> bool:
    if not (_pm_price_valid(state.pm_up) and _pm_price_valid(state.pm_dn)):
        return False
    if abs((state.pm_up or 0.0) + (state.pm_dn or 0.0) - 1.0) > 0.20:
        return False
    if state.pm_last_quote_ts and (time.time() - state.pm_last_quote_ts) > PM_STALE_QUOTE_SEC:
        return False
    return True


def _pm_apply(asset: str | None, asks: list[dict[str, Any]], state: State):
    if asks:
        prices = []
        for a in asks:
            try:
                prices.append(float(a["price"]))
            except Exception:
                pass
        if prices:
            _pm_set(asset, min(prices), state, source="book")


def _pm_pick_quote(entry: dict[str, Any]) -> float | None:
    for key in ("best_ask", "price", "best_bid"):
        try:
            val = entry.get(key)
            if val is None or val == "":
                continue
            return float(val)
        except Exception:
            continue
    return None


def _pm_set(asset: str | None, price: float, state: State, source: str = ""):
    if not asset:
        return
    if asset == state.pm_up_id:
        state.pm_up = price
    elif asset == state.pm_dn_id:
        state.pm_dn = price
    else:
        return

    state.pm_last_quote_ts = time.time()
    if source:
        state.pm_quote_source = source


def _pm_process_message(raw: Any, state: State) -> bool:
    updated = False

    if isinstance(raw, list):
        if not raw:
            state.pm_empty_msg_count += 1
            return False
        for entry in raw:
            if isinstance(entry, dict):
                state.pm_last_book_market = entry.get("market") or state.pm_last_book_market
                _pm_apply(entry.get("asset_id"), entry.get("asks", []), state)
                if entry.get("asks"):
                    updated = True
        return updated

    if not isinstance(raw, dict):
        return False

    event_type = raw.get("event_type")

    if event_type == "price_change":
        for ch in raw.get("price_changes", []):
            px = _pm_pick_quote(ch)
            if px is not None:
                _pm_set(ch.get("asset_id"), px, state, source="price_change")
                updated = True
        return updated

    if event_type == "best_bid_ask":
        px = _pm_pick_quote(raw)
        if px is not None:
            _pm_set(raw.get("asset_id"), px, state, source="best_bid_ask")
            updated = True
        return updated

    if event_type == "book":
        state.pm_last_book_market = raw.get("market") or state.pm_last_book_market
        _pm_apply(raw.get("asset_id"), raw.get("asks", []), state)
        return bool(raw.get("asks"))

    if event_type == "new_market":
        # Helpful signal that a new round exists; reconnect and refresh token IDs.
        state.pm_last_refresh_reason = "new_market_event"
        return False

    if event_type == "market_resolved":
        state.pm_last_refresh_reason = "market_resolved_event"
        return False

    return False


def _needs_refresh(state: State) -> tuple[bool, str]:
    now = time.time()

    if state.pm_empty_msg_count >= PM_EMPTY_REFRESH_THRESHOLD:
        return True, f"empty_messages={state.pm_empty_msg_count}"

    invalid_quotes = 0
    if not _pm_price_valid(state.pm_up):
        invalid_quotes += 1
    if not _pm_price_valid(state.pm_dn):
        invalid_quotes += 1
    if invalid_quotes > 0:
        state.pm_invalid_price_count += 1
    else:
        state.pm_invalid_price_count = 0

    if state.pm_invalid_price_count >= PM_INVALID_REFRESH_THRESHOLD:
        return True, f"invalid_quotes={state.pm_invalid_price_count}"

    if state.pm_last_msg_ts and (now - state.pm_last_msg_ts) > PM_RECV_TIMEOUT_SEC:
        return True, f"recv_timeout>{PM_RECV_TIMEOUT_SEC}s"

    if state.pm_last_quote_ts and (now - state.pm_last_quote_ts) > PM_STALE_QUOTE_SEC:
        return True, f"stale_quote>{PM_STALE_QUOTE_SEC}s"

    return False, ""


async def pm_feed(state: State, coin: str = "BTC", tf: str = "15m"):
    """Polymarket market-feed with heartbeat + auto re-discovery of active token IDs."""

    if not state.pm_up_id:
        state.pm_up_id, state.pm_dn_id, state.pm_market_slug = fetch_pm_tokens_robust(coin, tf)
    if not state.pm_up_id:
        print(" [PM] no tokens for this coin/timeframe – skipped")
        state.pm_last_error = "no_active_market"
        state.pm_feed_guard_active = True
        return

    while True:
        assets = [state.pm_up_id, state.pm_dn_id]
        hb_task = None
        try:
            print(f" [PM] Connecting with assets: {assets}")
            state.pm_feed_connected = False
            _pm_mark_guard(state, True, "connecting")
            _pm_reset_quotes(state)

            async with websockets.connect(
                config.PM_WS,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=10,
            ) as ws:
                state.pm_feed_connected = True
                state.pm_last_msg_ts = time.time()
                state.pm_empty_msg_count = 0
                state.pm_invalid_price_count = 0
                state.pm_reconnect_count = 0
                await ws.send(
                    json.dumps(
                        {
                            "assets_ids": assets,
                            "type": "market",
                            "custom_feature_enabled": True,
                        }
                    )
                )
                hb_task = asyncio.create_task(_pm_heartbeat(ws, state))
                print(" [PM] WebSocket connected, subscribed, waiting for prices...")

                while True:
                    try:
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=PM_RECV_TIMEOUT_SEC)
                    except asyncio.TimeoutError:
                        state.pm_last_error = f"recv_timeout>{PM_RECV_TIMEOUT_SEC}s"
                        state.pm_empty_msg_count += 1
                        need_refresh, reason = _needs_refresh(state)
                        if need_refresh:
                            print(f" [PM] no fresh messages, refreshing feed ({reason})")
                            break
                        continue

                    state.pm_last_msg_ts = time.time()

                    if raw_msg == "PONG":
                        state.pm_last_pong_ts = time.time()
                        continue

                    try:
                        raw = json.loads(raw_msg)
                    except Exception:
                        # Ignore non-json noise but keep the socket alive.
                        continue

                    updated = _pm_process_message(raw, state)
                    if updated:
                        state.pm_empty_msg_count = 0
                        if _pm_quotes_healthy(state):
                            _pm_mark_guard(state, False, "quotes_healthy")
                    elif raw == []:
                        state.pm_empty_msg_count += 1
                        print(" [PM] Received data: []...")

                    need_refresh, reason = _needs_refresh(state)
                    if need_refresh:
                        now = time.time()
                        if now - state.pm_last_refresh_ts >= PM_REFRESH_COOLDOWN_SEC:
                            print(f" [PM] refreshing tokens/feed: {reason}")
                            state.pm_last_refresh_ts = now
                            state.pm_last_refresh_reason = reason
                            break

        except websockets.exceptions.ConnectionClosed:
            state.pm_last_error = "connection_closed"
            state.pm_reconnect_count += 1
            print(" [PM] connection closed, reconnecting...")
        except Exception as e:
            state.pm_last_error = str(e)
            state.pm_reconnect_count += 1
            print(f" [PM] connection error: {e}, reconnecting in {PM_RETRY_DELAY_SEC}s...")
        finally:
            if hb_task is not None:
                hb_task.cancel()
                with contextlib.suppress(Exception):
                    await hb_task
            state.pm_feed_connected = False
            _pm_mark_guard(state, True, state.pm_last_refresh_reason or "reconnect")

        new_up, new_dn, new_slug = fetch_pm_tokens_robust(coin, tf)
        if new_up and new_dn:
            changed = (new_up != state.pm_up_id) or (new_dn != state.pm_dn_id)
            if changed:
                print(" [PM] New active market detected. Updating token IDs...")
            state.pm_up_id = new_up
            state.pm_dn_id = new_dn
            state.pm_market_slug = new_slug
        else:
            state.pm_last_error = "no_active_market_after_refresh"
            print(" [PM] no active market found during refresh; will retry...")

        await asyncio.sleep(PM_RETRY_DELAY_SEC)


# Needed for suppress in finally
import contextlib  # noqa: E402
