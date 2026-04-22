#!/usr/bin/env python3
"""
WebSocket Feed for Polymarket Real-time Price Updates
Replaces polling with WebSocket for faster execution
"""

import asyncio
import json
import websockets
from typing import Optional, Callable, Dict, Any
from datetime import datetime


class PolymarketWebSocket:
    """Real-time WebSocket feed for Polymarket prices"""
    
    def __init__(self, asset_ids: list[str], on_price_update: Callable):
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.asset_ids = asset_ids
        self.on_price_update = on_price_update
        self.ws = None
        self.running = False
        self.reconnect_delay = 5
        self.last_prices = {}
        
    async def connect(self):
        """Connect to WebSocket and subscribe to markets"""
        try:
            self.ws = await websockets.connect(self.ws_url)
            
            # Subscribe to markets with custom features
            subscribe_msg = {
                "assets_ids": self.asset_ids,
                "type": "market",
                "custom_feature_enabled": True
            }
            
            await self.ws.send(json.dumps(subscribe_msg))
            print(f"[WS] Connected and subscribed to {len(self.asset_ids)} assets")
            return True
            
        except Exception as e:
            print(f"[WS ERROR] Connection failed: {e}")
            return False
    
    async def handle_message(self, message: str):
        """Process incoming WebSocket messages"""
        try:
            data = json.loads(message)
            event_type = data.get("event_type")
            
            if event_type == "book":
                # Initial orderbook snapshot
                await self._handle_book(data)
                
            elif event_type == "price_change":
                # Order placed/cancelled
                await self._handle_price_change(data)
                
            elif event_type == "best_bid_ask":
                # Best bid/ask update (most important for us)
                await self._handle_best_bid_ask(data)
                
            elif event_type == "last_trade_price":
                # Trade execution
                await self._handle_trade(data)
                
            elif event_type == "tick_size_change":
                print(f"[WS] Tick size changed for {data.get('asset_id')[:8]}...")
                
            elif event_type == "new_market":
                print(f"[WS] New market: {data.get('question', 'Unknown')}")
                
            elif event_type == "market_resolved":
                print(f"[WS] Market resolved: {data.get('question', 'Unknown')}")
                
        except Exception as e:
            print(f"[WS ERROR] Message handling failed: {e}")
    
    async def _handle_book(self, data: Dict[str, Any]):
        """Handle orderbook snapshot"""
        asset_id = data.get("asset_id")
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        
        if bids and asks:
            best_bid = float(bids[0]["price"])
            best_ask = float(asks[0]["price"])
            midpoint = (best_bid + best_ask) / 2
            
            self.last_prices[asset_id] = {
                "price": midpoint,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": best_ask - best_bid,
                "timestamp": datetime.now().timestamp()
            }
            
            # Notify callback
            await self.on_price_update(asset_id, midpoint, best_bid, best_ask)
    
    async def _handle_price_change(self, data: Dict[str, Any]):
        """Handle price change event"""
        price_changes = data.get("price_changes", [])
        
        for change in price_changes:
            asset_id = change.get("asset_id")
            best_bid = float(change.get("best_bid", 0))
            best_ask = float(change.get("best_ask", 0))
            
            if best_bid > 0 and best_ask > 0:
                midpoint = (best_bid + best_ask) / 2
                
                self.last_prices[asset_id] = {
                    "price": midpoint,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread": best_ask - best_bid,
                    "timestamp": datetime.now().timestamp()
                }
                
                await self.on_price_update(asset_id, midpoint, best_bid, best_ask)
    
    async def _handle_best_bid_ask(self, data: Dict[str, Any]):
        """Handle best bid/ask update (most important)"""
        asset_id = data.get("asset_id")
        best_bid = float(data.get("best_bid", 0))
        best_ask = float(data.get("best_ask", 0))
        spread = float(data.get("spread", 0))
        
        if best_bid > 0 and best_ask > 0:
            midpoint = (best_bid + best_ask) / 2
            
            self.last_prices[asset_id] = {
                "price": midpoint,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "timestamp": datetime.now().timestamp()
            }
            
            await self.on_price_update(asset_id, midpoint, best_bid, best_ask)
    
    async def _handle_trade(self, data: Dict[str, Any]):
        """Handle trade execution"""
        asset_id = data.get("asset_id")
        price = float(data.get("price", 0))
        side = data.get("side")
        size = float(data.get("size", 0))
        
        # Update last trade price
        if asset_id in self.last_prices:
            self.last_prices[asset_id]["last_trade"] = {
                "price": price,
                "side": side,
                "size": size,
                "timestamp": datetime.now().timestamp()
            }
    
    async def listen(self):
        """Main listen loop with auto-reconnect"""
        self.running = True
        
        while self.running:
            try:
                if not self.ws or self.ws.closed:
                    print("[WS] Connecting...")
                    connected = await self.connect()
                    
                    if not connected:
                        print(f"[WS] Reconnecting in {self.reconnect_delay}s...")
                        await asyncio.sleep(self.reconnect_delay)
                        continue
                
                # Listen for messages
                async for message in self.ws:
                    await self.handle_message(message)
                    
            except websockets.exceptions.ConnectionClosed:
                print(f"[WS] Connection closed, reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                
            except Exception as e:
                print(f"[WS ERROR] {e}, reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
    
    async def stop(self):
        """Stop WebSocket connection"""
        self.running = False
        if self.ws:
            await self.ws.close()
        print("[WS] Stopped")
    
    def get_price(self, asset_id: str) -> Optional[float]:
        """Get latest price for asset"""
        if asset_id in self.last_prices:
            return self.last_prices[asset_id]["price"]
        return None
    
    def get_best_bid_ask(self, asset_id: str) -> tuple[Optional[float], Optional[float]]:
        """Get best bid/ask for asset"""
        if asset_id in self.last_prices:
            return (
                self.last_prices[asset_id]["best_bid"],
                self.last_prices[asset_id]["best_ask"]
            )
        return (None, None)
    
    def is_stale(self, asset_id: str, max_age_seconds: int = 30) -> bool:
        """Check if price data is stale"""
        if asset_id not in self.last_prices:
            return True
        
        age = datetime.now().timestamp() - self.last_prices[asset_id]["timestamp"]
        return age > max_age_seconds
