from fastapi import FastAPI, HTTPException
import os, psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv


# override=True ensures it replaces any "ghost" variables from Docker
load_dotenv(override=True) 

# Get the string and immediately strip any accidental whitespace
raw_url = os.getenv("DATABASE_URL", "")
DATABASE_URL = raw_url.strip().strip("'").strip('"')

# 1. Force load the .env file (important for local Docker runs)
#load_dotenv()

#DATABASE_URL=r"postgresql://neondb_owner:npg_io5fFZVLWIk3@ep-odd-bread-agpnkz8w-pooler.c-2.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# 2. Get the URL from the environment
#DATABASE_URL = os.getenv("DATABASE_URL")

# 3. CRITICAL: Add this check to see what is happening during startup
if not DATABASE_URL or "localhost" in DATABASE_URL:
    print(f"ERROR: Invalid DATABASE_URL detected: {DATABASE_URL}")
else:
    # This will now show your Neon URL (don't worry, password is obfuscated in logs usually)
    print(f"Connecting to database at: {DATABASE_URL[:20]}...") 

app = FastAPI()

def get_conn():
    # Ensure we use the URL fetched above
    return psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row)

# --- 1. PRODUCT ENDPOINTS ---
@app.get("/products")
def list_products():
    # Explicitly casting price to FLOAT handles the JSON serialization crash
    query = """
        SELECT p.product_id, p.name, c.name as category_name, 
               p.price::FLOAT as price, p.stock
        FROM products p
        INNER JOIN categories c ON p.category_id = c.category_id
        ORDER BY p.product_id;
    """
    print("Executing query to list products...")
    print(f"Using DATABASE_URL: {DATABASE_URL}...")  # Log the URL being used
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(query)
            return cur.fetchall()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        raise HTTPException(status_code=500, detail="Database query failed. Check logs.")

@app.post("/orders", status_code=201)
def place_order(data: dict):
    customer_id = data.get("customer_id")
    product_id = data.get("product_id")
    quantity = data.get("quantity")

    if not all([customer_id, product_id, quantity]):
        raise HTTPException(status_code=400, detail="Missing fields")

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 1. Get product price and stock from 'products' table
                cur.execute(
                    "SELECT name, price::float, stock FROM products WHERE product_id = %s", 
                    (product_id,)
                )
                product = cur.fetchone()
                
                if not product or product["stock"] < quantity:
                    raise HTTPException(status_code=400, detail="Invalid product or low stock")

                # 2. Insert into orders
                cur.execute(
                    "INSERT INTO orders (customer_id) VALUES (%s) RETURNING order_id;",
                    (customer_id,)
                )
                order_id = cur.fetchone()["order_id"]

                # 3. Insert into order_items WITHOUT the price column
                cur.execute(
                    "INSERT INTO order_items (order_id, product_id, quantity) VALUES (%s, %s, %s)",
                    (order_id, product_id, quantity)
                )
                
                # 4. Update Stock
                cur.execute("UPDATE products SET stock = stock - %s WHERE product_id = %s", (quantity, product_id))

                return {
                    "order_id": order_id,
                    "total_price": product["price"] * quantity,
                    "message": "Order placed successfully"
                }
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
# --- 3. STATISTICS ENDPOINTS (SQL Aggregations) ---
@app.get("/statistics/products")
def get_product_stats():
    # We JOIN with products to get the current price for the calculation
    query = """
        SELECT p.name, 
               SUM(oi.quantity) as total_units_sold, 
               SUM(oi.quantity * p.price)::FLOAT as total_revenue
        FROM order_items oi
        JOIN products p ON oi.product_id = p.product_id
        GROUP BY p.name
        ORDER BY total_revenue DESC;
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()

@app.get("/statistics/users")
def get_user_stats():
    query = """
        SELECT o.customer_id, 
               COUNT(DISTINCT o.order_id) as total_orders, 
               SUM(oi.quantity * p.price)::FLOAT as total_spent
        FROM orders o
        JOIN order_items oi ON o.order_id = oi.order_id
        JOIN products p ON oi.product_id = p.product_id
        GROUP BY o.customer_id
        ORDER BY total_spent DESC;
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()