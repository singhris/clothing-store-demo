import requests
import random
import string

# CONFIGURATION
# Ensure this matches the port in your docker-compose.yml (usually 8000 or 8080)
BASE_URL = "http://localhost:8080"

def generate_random_email():
    """Generates a random email to avoid registration errors."""
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"user_{random_str}@example.com"

def run_tests():
    print("🚀 STARTING API TESTS...\n")
    
    # --- 1. SETUP USER DATA ---
    email = generate_random_email()
    password = "testpassword123"
    print(f"🔹 Generated Test User: {email} / {password}")

    # --- 2. REGISTER ---
    print("\n--- 1. Testing Registration (POST /users) ---")
    url = f"{BASE_URL}/users"
    print(f"Requested URL: {url}")
    
    try:
        # UPDATED: We now send first_name and last_name to match your new main.py
        reg_response = requests.post(url, json={
            "first_name": "Test",
            "last_name": "User",
            "email": email,
            "password": password
        })
        print(f"Status: {reg_response.status_code}")
        print(f"Response: {reg_response.json()}")
        
        if reg_response.status_code != 201:
            print("❌ Registration failed. Stopping tests.")
            return
    except requests.exceptions.ConnectionError:
        print("❌ CRITICAL: Could not connect to server. Is Docker running?")
        return

    # --- 3. LOGIN ---
    print("\n--- 2. Testing Login (POST /users/login) ---")
    url = f"{BASE_URL}/users/login"
    print(f"Requested URL: {url}")
    
    # OAuth2 forms use 'data', not 'json', and expect 'username'/'password' fields
    login_data = {
        "username": email,
        "password": password
    }
    login_response = requests.post(url, data=login_data)
    
    if login_response.status_code != 200:
        print(f"❌ Login failed: {login_response.text}")
        return

    token_data = login_response.json()
    access_token = token_data["access_token"]
    print(f"✅ Login Successful!")
    print(f"🔑 Token received: {access_token[:15]}...") # Show first 15 chars only

    # --- 4. PREPARE AUTH HEADER ---
    auth_headers = {
        "Authorization": f"Bearer {access_token}"
    }

    # --- 5. LIST PRODUCTS (Public) ---
    print("\n--- 3. Testing List Products (GET /products) ---")
    url = f"{BASE_URL}/products"
    print(f"Requested URL: {url}")
    
    prod_response = requests.get(url)
    products = prod_response.json()
    print(f"✅ Found {len(products)} products.")
    
    if not products:
        print("❌ No products found in DB. Did you seed the database?")
        return
        
    first_product_id = products[0]["product_id"]
    print(f"   Targeting Product ID: {first_product_id} ({products[0]['name']})")

    # --- 6. PLACE ORDER (Protected) ---
    print("\n--- 4. Testing Place Order (POST /orders) ---")
    url = f"{BASE_URL}/orders"
    print(f"Requested URL: {url}")
    
    order_payload = {
        "product_id": first_product_id,
        "quantity": 1
    }
    
    order_response = requests.post(
        url, 
        json=order_payload, 
        headers=auth_headers
    )
    
    print(f"Status: {order_response.status_code}")
    print(f"Response: {order_response.json()}")

    if order_response.status_code == 201:
        print("✅ Order placed successfully!")
    else:
        print("❌ Order failed.")

    # --- 7. CHECK ORDER HISTORY (Protected) ---
    print("\n--- 5. Testing Order History (GET /orders) ---")
    url = f"{BASE_URL}/orders"
    print(f"Requested URL: {url}")
    
    history_response = requests.get(url, headers=auth_headers)
    print(f"Response: {history_response.json()}")

    # --- 8. TEST RBAC (Admin Only) ---
    print("\n--- 6. Testing Admin Access as Customer (Should Fail) ---")
    url = f"{BASE_URL}/statistics/users"
    print(f"Requested URL: {url}")
    
    admin_response = requests.get(url, headers=auth_headers)
    print(f"Status: {admin_response.status_code}")
    # print(f"Response: {admin_response.json()}") # Uncomment if you want to see the 403 detail
    
    if admin_response.status_code == 403:
        print("✅ Correctly blocked! (403 Forbidden)")
    elif admin_response.status_code == 200:
        print("❌ WARNING: Customer was allowed to access Admin route!")
    else:
        print(f"❓ Unexpected status: {admin_response.status_code}")

if __name__ == "__main__":
    run_tests()