import os
import psycopg
import bcrypt
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from psycopg.rows import dict_row
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv(override=True)

# CONFIGURATION
DATABASE_URL = os.getenv("DATABASE_URL", "").strip().strip("'").strip('"')
SECRET_KEY = os.getenv("JWT_SECRET", "default-secret-key-for-local-dev")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

app = FastAPI()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="users/login")

# --- MODELS (Fixed to match DB Schema) ---
class UserRegister(BaseModel):
    first_name: str
    last_name: str
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

# DATABASE & AUTH UTILS
def get_conn():
    return psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row)

def hash_password(password: str):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain, hashed):
    if not hashed: return False
    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    fail = HTTPException(status_code=401, detail="Invalid session")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if not email: raise fail
    except JWTError: raise fail
    
    with get_conn() as conn, conn.cursor() as cur:
        # FIXED: Select first_name/last_name instead of 'name'
        cur.execute("SELECT customer_id, first_name, last_name, email, role FROM customers WHERE email = %s", (email,))
        user = cur.fetchone()
        if not user: raise fail
        return user

def check_admin(user=Depends(get_current_user)):
    # Handle case where role might be None by defaulting to 'customer'
    if user.get("role", "customer") != "admin":
        raise HTTPException(status_code=403, detail="Admin permissions required")
    return user

# --- 1. AUTH ENDPOINTS ---

@app.post("/users", status_code=201)
def register_user(user: UserRegister):
    """Everyone can create a new user"""
    hashed = hash_password(user.password)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            # FIXED: Insert into first_name and last_name
            cur.execute(
                """
                INSERT INTO customers (first_name, last_name, email, password, role) 
                VALUES (%s, %s, %s, %s, 'customer') 
                RETURNING customer_id
                """,
                (user.first_name, user.last_name, user.email, hashed)
            )
            return {"id": cur.fetchone()["customer_id"], "message": "User registered successfully"}
    except Exception as e:
        # Added print so you can see the error in Docker logs
        print(f"REGISTER ERROR: {e}")
        raise HTTPException(status_code=400, detail="Registration failed (Email likely exists or DB error)")

@app.post("/users/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT email, password FROM customers WHERE email = %s", (form_data.username,))
        user = cur.fetchone()
        if not user or not verify_password(form_data.password, user["password"]):
            raise HTTPException(status_code=401, detail="Incorrect email or password")
        
        token = create_access_token(data={"sub": user["email"]})
        return {"access_token": token, "token_type": "bearer"}

# --- 2. PRODUCT ENDPOINTS ---

@app.get("/products")
def list_products():
    """Everyone can list products"""
    query = """
        SELECT p.product_id, p.name, c.name as category_name, p.price::FLOAT, p.stock
        FROM products p JOIN categories c ON p.category_id = c.category_id
        ORDER BY p.product_id;
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()

# --- 3. ORDER ENDPOINTS ---

@app.post("/orders", status_code=201)
def place_order(data: dict, current_user=Depends(get_current_user)):
    """Logged in customers can place orders"""
    product_id = data.get("product_id")
    quantity = data.get("quantity")
    
    # Security: Get ID from the logged-in user, not the request body
    customer_id = current_user["customer_id"]

    with get_conn() as conn, conn.cursor() as cur:
        # Check stock and get price
        cur.execute("SELECT name, price::float, stock FROM products WHERE product_id = %s", (product_id,))
        product = cur.fetchone()
        if not product or product["stock"] < quantity:
            raise HTTPException(status_code=400, detail="Invalid product or insufficient stock")

        # Atomic order creation
        cur.execute("INSERT INTO orders (customer_id) VALUES (%s) RETURNING order_id;", (customer_id,))
        order_id = cur.fetchone()["order_id"]
        cur.execute("INSERT INTO order_items (order_id, product_id, quantity) VALUES (%s, %s, %s)",
                    (order_id, product_id, quantity))
        cur.execute("UPDATE products SET stock = stock - %s WHERE product_id = %s", (quantity, product_id))

        return {
            "order_id": order_id,
            "product_name": product["name"],
            "total_price": product["price"] * quantity,
            "message": "Order placed successfully"
        }

@app.get("/orders")
def get_my_orders(current_user=Depends(get_current_user)):
    """Logged in customers can list their own past orders"""
    query = """
        SELECT o.order_id, oi.product_id, oi.quantity 
        FROM orders o 
        JOIN order_items oi ON o.order_id = oi.order_id 
        WHERE o.customer_id = %s
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, (current_user["customer_id"],))
        return cur.fetchall()

# --- 4. STATISTICS & ADMIN ENDPOINTS ---

@app.get("/statistics/products", dependencies=[Depends(check_admin)])
def get_product_stats():
    """Admin only: aggregated product data"""
    query = """
        SELECT p.name, SUM(oi.quantity) as total_units_sold, SUM(oi.quantity * p.price)::FLOAT as turnover
        FROM order_items oi JOIN products p ON oi.product_id = p.product_id
        GROUP BY p.name ORDER BY turnover DESC;
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()

@app.get("/statistics/users", dependencies=[Depends(check_admin)])
def get_user_stats():
    """Admin only: aggregated user data"""
    query = """
        SELECT o.customer_id, COUNT(DISTINCT o.order_id) as total_orders, SUM(oi.quantity * p.price)::FLOAT as money_spent
        FROM orders o JOIN order_items oi ON o.order_id = oi.order_id JOIN products p ON oi.product_id = p.product_id
        GROUP BY o.customer_id ORDER BY money_spent DESC;
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()

@app.delete("/users/{id}", dependencies=[Depends(check_admin)])
def delete_user(id: int):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM customers WHERE customer_id = %s", (id,))
        return {"message": f"User {id} deleted successfully"}