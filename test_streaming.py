#!/usr/bin/env python3
"""
Comprehensive test script for AI streaming and review endpoints.

USAGE (automatic):
  python test_streaming.py

This script will:
  1. Register or login as a test user
  2. Run AI generation streaming test
  3. Run review contract analysis test (if contracts exist)
"""

import sys
import requests
import json
import time
import uuid
import random
import string

BASE_URL = "https://lawflow-811882866295.asia-south1.run.app"
TEST_EMAIL = f"test-{uuid.uuid4().hex[:8]}@example.com"
TEST_PASSWORD = "TestPassword123!"


def register_user(email, password):
    """Register a new test user."""
    print("\n" + "="*70)
    print("STEP 1: Register Test User")
    print("="*70)
    
    url = f"{BASE_URL}/api/auth/register/"
    payload = {
        "email": email,
        "password": password,
        "full_name": "Test User",
        "company": "Test Company",
    }
    
    print(f"\nPOST {url}")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        print(f"Status: {response.status_code}")
        data = response.json()
        
        if response.status_code in (200, 201):
            print(f"✓ User registered: {email}")
            return True
        else:
            # User might already exist, that's ok
            if "already exists" in str(data).lower() or response.status_code == 400:
                print(f"⚠ User may already exist, proceeding with login...")
                return True
            print(f"✗ Error: {data}")
            return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def login_user(email, password):
    """Login user and return access token."""
    print("\n" + "="*70)
    print("STEP 2: Login & Get Access Token")
    print("="*70)
    
    url = f"{BASE_URL}/api/auth/login/"
    payload = {
        "email": email,
        "password": password,
    }
    
    print(f"\nPOST {url}")
    print(f"Payload: {json.dumps({'email': email, 'password': '***'}, indent=2)}")
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        print(f"Status: {response.status_code}")
        data = response.json()
        
        if response.status_code == 200:
            token = data.get("access")
            user = data.get("user", {})
            print(f"✓ Login successful!")
            print(f"  User ID: {user.get('user_id')}")
            print(f"  Email: {user.get('email')}")
            print(f"  Tenant ID: {user.get('tenant_id')}")
            print(f"  Token: {token[:50]}...")
            return token
        else:
            print(f"✗ Login failed: {data}")
            return None
    except Exception as e:
        print(f"✗ Error: {e}")
        return None

def test_ai_stream(token):
    """Test AI generation streaming endpoint."""
    print("\n" + "="*70)
    print("TEST 1: AI Generation Streaming (SSE)")
    print("="*70)
    
    url = f"{BASE_URL}/api/v1/ai/generate/template-stream/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": "Add a termination clause",
        "current_text": "This is a sample service agreement between Company A and Company B.",
        "contract_type": "service_agreement",
    }
    
    print(f"\nPOST {url}")
    print(f"Headers: Authorization: Bearer {token[:20]}...")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        print("\nStreaming response:")
        print("-" * 70)
        
        response = requests.post(url, headers=headers, json=payload, stream=True, timeout=30)
        print(f"Status Code: {response.status_code}")
        print(f"Headers: {dict(response.headers)}\n")
        
        if response.status_code != 200:
            print(f"ERROR: Expected 200, got {response.status_code}")
            print(f"Response: {response.text}")
            return False
        
        # Parse SSE stream
        event_count = 0
        delta_count = 0
        task_id = None
        
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            
            print(f"  {line}")
            
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
                event_count += 1
                
                if event_type == "meta":
                    print(f"    ✓ Meta event received")
                elif event_type == "delta":
                    delta_count += 1
                elif event_type == "done":
                    print(f"    ✓ Done event received")
                elif event_type == "error":
                    print(f"    ✗ Error event received")
            
            elif line.startswith("data:"):
                data_str = line.split(":", 1)[1].strip()
                try:
                    data = json.loads(data_str)
                    if "task_id" in data:
                        task_id = data["task_id"]
                        print(f"    Task ID: {task_id}")
                    if "delta" in data:
                        preview = data["delta"][:50] + "..." if len(data["delta"]) > 50 else data["delta"]
                        print(f"    Delta preview: {preview}")
                    if "error" in data:
                        print(f"    Error: {data['error']}")
                except json.JSONDecodeError:
                    pass
        
        print("-" * 70)
        print(f"\nSummary:")
        print(f"  Events: {event_count}")
        print(f"  Deltas: {delta_count}")
        print(f"  Task ID: {task_id}")
        
        if delta_count == 0:
            print(f"\n  ⚠ WARNING: No delta events received!")
            print(f"     The stream ended after meta without generating content.")
            return False
        
        print(f"\n  ✓ Stream working correctly!")
        return True
        
    except requests.exceptions.Timeout:
        print(f"\n✗ Timeout after 30s - endpoint may be hanging or slow")
        return False
    except Exception as e:
        print(f"\n✗ Error: {type(e).__name__}: {e}")
        return False


def test_review_analyze(token, contract_id):
    """Test contract review analyze endpoint."""
    print("\n" + "="*70)
    print(f"TEST 2: Contract Review Analysis")
    print("="*70)
    
    url = f"{BASE_URL}/api/v1/review-contracts/{contract_id}/analyze/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    print(f"\nPOST {url}")
    print(f"Headers: Authorization: Bearer {token[:20]}...")
    
    try:
        print("\nSending request...")
        response = requests.post(url, headers=headers, timeout=30)
        print(f"Status Code: {response.status_code}")
        print(f"Headers: {dict(response.headers)}\n")
        
        print("Response body:")
        try:
            data = response.json()
            print(json.dumps(data, indent=2))
            
            if response.status_code == 200 and data.get("success"):
                print("\n✓ Review analysis successful!")
                return True
            else:
                print(f"\n✗ Request failed: {data.get('error', 'Unknown error')}")
                return False
                
        except json.JSONDecodeError:
            print(response.text)
            return False
        
    except requests.exceptions.Timeout:
        print(f"\n✗ Timeout after 30s")
        return False
    except Exception as e:
        print(f"\n✗ Error: {type(e).__name__}: {e}")
        return False


def main():
    print("\n" + "="*70)
    print("COMPREHENSIVE ENDPOINT DIAGNOSTICS TEST")
    print("="*70)
    print(f"Base URL: {BASE_URL}")
    print(f"Test Email: {TEST_EMAIL}")
    
    # Step 1: Register
    if not register_user(TEST_EMAIL, TEST_PASSWORD):
        print("\n✗ Registration failed!")
        return 1
    
    # Step 2: Login
    token = login_user(TEST_EMAIL, TEST_PASSWORD)
    if not token:
        print("\n✗ Login failed!")
        return 1
    
    results = {}
    
    # Step 3: Test AI Streaming
    print("\n" + "="*70)
    print("STEP 3: AI Generation Test")
    print("="*70)
    results["ai_stream"] = test_ai_stream(token)
    
    # Step 4: Fetch contracts for review analysis
    print("\n" + "="*70)
    print("STEP 4: Fetching Contracts")
    print("="*70)
    
    try:
        contracts_url = f"{BASE_URL}/api/v1/review-contracts/"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        response = requests.get(contracts_url, headers=headers, timeout=10)
        contracts = response.json() if response.status_code == 200 else []
        
        if isinstance(contracts, dict) and "results" in contracts:
            contracts = contracts["results"]
        
        if contracts and len(contracts) > 0:
            contract_id = contracts[0].get('id') or contracts[0].get('contract_id')
            print(f"✓ Found {len(contracts)} contracts")
            results["review_analyze"] = test_review_analyze(token, contract_id)
        else:
            print(f"⚠ No contracts found, skipping review test")
    except Exception as e:
        print(f"⚠ Could not fetch contracts: {e}")
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
