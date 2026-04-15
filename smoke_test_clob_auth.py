#!/usr/bin/env python3
"""Smoke Test 2: Auth L1 + Derive Creds L2"""
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient

load_dotenv()

HOST = os.getenv("HOST", "https://clob.polymarket.com")
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SIGNATURE_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "2"))
FUNDER = os.getenv("POLY_FUNDER_ADDRESS")

assert PRIVATE_KEY, "PRIVATE_KEY belum diisi"
assert FUNDER, "POLY_FUNDER_ADDRESS belum diisi"

client = ClobClient(
    HOST,
    key=PRIVATE_KEY,
    chain_id=CHAIN_ID,
    signature_type=SIGNATURE_TYPE,
    funder=FUNDER,
)

api_creds = client.create_or_derive_api_creds()
client.set_api_creds(api_creds)

print("✅ AUTH OK")
print("api_key:", api_creds.api_key if hasattr(api_creds, "api_key") else "derived")
