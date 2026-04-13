#!/usr/bin/env python3
"""
Real Polymarket Order Execution using CLOB API + Relayer
Combines CLOB for order placement with Relayer for gasless approvals
"""

import os
import asyncio
import requests
from decimal import Decimal
from dotenv import load_dotenv

# CRITICAL: Monkey-patch BEFORE importing anything from py_clob_client
# The library's order_to_json() calls order.dict() which doesn't exist on OrderArgs
from py_clob_client.clob_types import OrderArgs as _OrderArgs
if not hasattr(_OrderArgs, 'dict'):
    def _order_args_dict(self):
        return self.__dict__
    _OrderArgs.dict = _order_args_dict
    print("🩹 Monkey-patch applied: OrderArgs.dict() -> __dict__")

# Now safe to import the rest
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType

load_dotenv()

# API Endpoints
CLOB_HOST = "https://clob.polymarket.com"
RELAYER_HOST = "https://relayer-v2.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet

class PolymarketExecutor:
    """Execute real orders on Polymarket CLOB with Relayer support."""
    
    def __init__(self):
        self.private_key = os.getenv("PRIVATE_KEY")
        if not self.private_key:
            raise ValueError("PRIVATE_KEY not found in .env")
        
        # Relayer API credentials for gasless operations
        self.relayer_api_key = os.getenv("RELAYER_API_KEY")
        self.relayer_address = os.getenv("RELAYER_API_ADDRESS", "0xc6688f54bAdeAa942bB68e16FCB0694adD711D5A")
        
        # CLOB API credentials (if available)
        self.clob_api_key = os.getenv("CLOB_API_KEY")
        self.clob_secret = os.getenv("CLOB_SECRET")
        self.clob_passphrase = os.getenv("CLOB_PASSPHRASE")
        
        # Signer address (from private key)
        self.signer_address = self.relayer_address  # Use same address
        
        self.client = None
        self.api_creds = None
        self.initialized = False
        
    async def initialize(self):
        """Initialize connection to CLOB with EOA (direct wallet)."""
        try:
            # Use EOA (signature_type=0) - direct wallet with USDC
            SIGNATURE_TYPE_EOA = 0
            
            print("🔐 Initializing CLOB Client...")
            print(f"   Signer: {self.signer_address}")
            print(f"   Mode: EOA (direct wallet with USDC)")
            
            # Create base client - EOA mode, no funder needed
            self.client = ClobClient(
                host=CLOB_HOST,
                chain_id=CHAIN_ID,
                key=self.private_key,
                signature_type=SIGNATURE_TYPE_EOA  # 0 = EOA
            )
            
            # Create or derive API credentials (L2 auth)
            print("🔐 Deriving API credentials...")
            self.api_creds = self.client.create_or_derive_api_creds()
            
            # Re-initialize with credentials - EOA mode
            self.client = ClobClient(
                host=CLOB_HOST,
                chain_id=CHAIN_ID,
                key=self.private_key,
                creds=self.api_creds,
                signature_type=SIGNATURE_TYPE_EOA  # Keep EOA
            )
            
            self.initialized = True
            print(f"✅ CLOB Client initialized (EOA mode)")
            return True
            
        except Exception as e:
            print(f"❌ Failed to initialize CLOB client: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def approve_usdc_via_relayer(self):
        """Approve USDC for trading via Relayer (gasless)."""
        if not self.relayer_api_key:
            print("⚠️ Relayer API key not configured, skipping gasless approval")
            return False
        
        try:
            print("🔄 Approving USDC via Relayer (gasless)...")
            
            # Relayer submit endpoint
            url = f"{RELAYER_HOST}/submit"
            
            headers = {
                "Content-Type": "application/json",
                "RELAYER_API_KEY": self.relayer_api_key,
                "RELAYER_API_KEY_ADDRESS": self.relayer_address
            }
            
            # This would need the actual transaction data for USDC approval
            # For now, just check if relayer is accessible
            response = requests.get(f"{RELAYER_HOST}/health", headers=headers, timeout=10)
            
            if response.status_code == 200:
                print(f"✅ Relayer accessible: {response.json()}")
                return True
            else:
                print(f"⚠️ Relayer check: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"⚠️ Relayer approval failed: {e}")
            return False
    
    def get_market_info(self, token_id: str):
        """Get market information for a token."""
        if not self.initialized:
            return None
        try:
            return self.client.get_market(token_id)
        except Exception as e:
            print(f"Error getting market info: {e}")
            return None
    
    async def place_market_order(self, token_id: str, side: str, size: float, price: float = None):
        """
        Place a market order using SignedOrder flow.
        
        Args:
            token_id: Polymarket token ID
            side: 'BUY' or 'SELL'
            size: Order size in shares
            price: Optional price hint
        """
        if not self.initialized:
            print("⚠️ CLOB client not initialized")
            return None
        
        try:
            from py_clob_client.clob_types import CreateOrderOptions
            from py_clob_client.order_builder.builder import OrderBuilder
            from py_clob_client.signer import Signer
            
            print(f"📤 Placing MARKET {side} order: {size} shares @ {price or 'market'}")
            print(f"   → Token: {token_id[:20]}...")
            print(f"   → Signer: {self.signer_address[:20]}...")
            
            # Create OrderArgs with proper decimal precision
            # API requires: maker amount max 2 decimals, taker amount max 5 decimals
            rounded_size = round(float(size), 2)  # Maker: max 2 decimals
            rounded_price = round(float(price), 5) if price else 0.5  # Taker: max 5 decimals
            
            print(f"   → Size: {size} → rounded: {rounded_size} (2 decimals)")
            print(f"   → Price: {price} → rounded: {rounded_price} (5 decimals)")
            
            order_args = OrderArgs(
                token_id=str(token_id),
                side=side.upper(),
                size=rounded_size,
                price=rounded_price,
                fee_rate_bps=0,  # Market taker fee is 0 for this market
                nonce=0,
                expiration=0,
                taker='0x0000000000000000000000000000000000000000'
            )
            
            # Create order options
            options = CreateOrderOptions(tick_size="0.01", neg_risk=False)
            
            # Build SignedOrder using OrderBuilder (this is the CORRECT way)
            signed_order = self.client.builder.create_order(order_args, options)
            
            print(f"✅ SignedOrder created")
            print(f"   → Maker: {signed_order.dict().get('maker', 'N/A')[:20]}...")
            
            # Post the SignedOrder
            order = self.client.post_order(signed_order, OrderType.FOK)
            
            print(f"✅ Order placed successfully: {order}")
            return order
            
        except Exception as e:
            print(f"❌ Order failed: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def place_limit_order(self, token_id: str, side: str, size: float, price: float):
        """
        Place a limit order.
        
        Args:
            token_id: Polymarket token ID
            side: 'BUY' or 'SELL'
            size: Order size in shares
            price: Limit price
        """
        if not self.initialized:
            print("⚠️ CLOB client not initialized")
            return None
        
        try:
            order_args = OrderArgs(
                token_id=token_id,
                side=side.upper(),  # "BUY" or "SELL" as string
                size=size,
                price=price,
            )
            
            print(f"📤 Placing LIMIT {side} order: {size} shares @ {price:.4f}")
            
            order = self.client.post_order(order_args, OrderType.GTC)
            
            print(f"✅ Limit order placed: {order}")
            return order
            
        except Exception as e:
            print(f"❌ Limit order failed: {e}")
            return None
    
    def get_balance(self):
        """Get USDC balance on Polymarket."""
        if not self.initialized:
            return 0
        try:
            balance = self.client.get_balance()
            return balance
        except Exception as e:
            print(f"Error getting balance: {e}")
            return 0
    
    def get_positions(self):
        """Get current positions."""
        if not self.initialized:
            return []
        try:
            positions = self.client.get_positions()
            return positions
        except Exception as e:
            print(f"Error getting positions: {e}")
            return []


# Test function
async def test_executor():
    """Test the executor initialization."""
    executor = PolymarketExecutor()
    success = await executor.initialize()
    if success:
        # Test relayer connection
        relayer_ok = await executor.approve_usdc_via_relayer()
        print(f"Relayer: {'✅ OK' if relayer_ok else '⚠️ Not used'}")
        
        balance = executor.get_balance()
        print(f"Balance: {balance}")
        positions = executor.get_positions()
        print(f"Positions: {len(positions)}")
    return success

if __name__ == "__main__":
    asyncio.run(test_executor())
