#!/usr/bin/env python3
"""Smoke Test 1: Read-Only CLOB (Safe, no auth required)"""
from py_clob_client.client import ClobClient

HOST = "https://clob.polymarket.com"

client = ClobClient(HOST)
print("get_ok:", client.get_ok())
print("server_time:", client.get_server_time())
print("✅ Read-only test passed!")
