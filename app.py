from flask import Flask, render_template, request, session, redirect, url_for
import sqlite3
import random
import re
from datetime import datetime

app = Flask(__name__)
# !!! IMPORTANT: CHANGE THIS TO A LONG, RANDOM, AND SECRET STRING !!!
app.secret_key = "my_super_secure_and_secret_random_key_that_is_very_long_12345!@#$" 


# =====================================
# Database Initialization (RUNS ON STARTUP)
# =====================================
def init_db():
    # check_same_thread=False is used here specifically because init_db()
    # is called outside a request context before app.run() starts the
    # Flask development server, which might access the DB from multiple threads
    # during its startup sequence (e.g., if using reloader).
    # For connections *within* request handlers, get_connection() doesn't need this,
    # relying on Flask's implicit request context handling for SQLite.
    conn = sqlite3.connect("srd.db", check_same_thread=False)
    cursor = conn.cursor()
    
    # Enforce foreign key constraints for this initialization connection
    cursor.execute("PRAGMA foreign_keys = ON")
    # ========================================================
    # NEW: Social Audit / Feedback Table
    # Captures citizen ratings for quality and service transparency.
    # ========================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ration_card TEXT NOT NULL,
            shop_id TEXT NOT NULL,
            quality_rating INTEGER NOT NULL,
            service_rating INTEGER NOT NULL,
            comment TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ration_card) REFERENCES citizens (ration_card)
        )
    """)
    # ================================
    # Citizens (LOGIN SYSTEM)
    # ================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS citizens (
            ration_card TEXT PRIMARY KEY NOT NULL,
            head_name TEXT NOT NULL,
            password TEXT NOT NULL,
            rice_allowed INTEGER NOT NULL
        )
    """)
    # ================================
    # Dealers (Shop Login System)
    # ================================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dealers (
        shop_id TEXT PRIMARY KEY,
        password TEXT NOT NULL
     )
""")

    # Default dealer (for testing)
    cursor.execute(
    "INSERT OR IGNORE INTO dealers (shop_id, password) VALUES ('SHOP001', 'admin123')"
    )

    # ================================
    # Family Members
    # ================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS family_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ration_card TEXT NOT NULL,
            aadhar TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY (ration_card) REFERENCES citizens (ration_card) ON DELETE CASCADE
        )
    """)

    # ========================================================
    # NEW: Transactions Table (THE CORE OF HISTORY FEATURE)
    # This stores a permanent record of every completed sale.
    # ========================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ration_card TEXT NOT NULL,
            date_time TEXT NOT NULL,
            shop_id TEXT NOT NULL,
            quantity REAL NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (ration_card) REFERENCES citizens (ration_card)
        )
    ''')
    
    # ================================
    # Tokens (QUEUE SYSTEM) - FIFO
    # ================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ration_card TEXT NOT NULL,
            token_no INTEGER NOT NULL,
            code INTEGER NOT NULL,
            status TEXT NOT NULL,        -- 'waiting', 'completed', 'skipped'
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            served_at DATETIME,
            FOREIGN KEY (ration_card) REFERENCES citizens (ration_card) ON DELETE CASCADE
        )
    """)

    # ================================
    # Shop Settings
    # ================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            shop_status TEXT NOT NULL    -- 'open' / 'closed'
        )
    """)

    # Default row (runs only once using INSERT OR IGNORE)
    cursor.execute(
        "INSERT OR IGNORE INTO settings (id, shop_status) VALUES (1, 'open')"
    )

    conn.commit()
    conn.close()
# ========================================================
# NEW: Submit Social Audit Feedback
# Process citizen feedback and stores it in the central database.
# ========================================================
@app.route("/submit_feedback", methods=["POST"])
def submit_feedback():
    # 1. Verification: Ensure the citizen session is active
    ration_card = session.get("ration_card")
    if not ration_card:
        return redirect(url_for("citizen_login"))

    # 2. Extract data from the Jan-Awaaz form
    # We use request.form.get to prevent KeyErrors if a field is missing
    shop_id = request.form.get("shop_id", "SHOP001")
    quality = request.form.get("quality_rating")
    service = request.form.get("service_rating")
    comment = request.form.get("comment", "").strip()

    # 3. Database Operation
    db_conn = get_connection()
    db_cursor = db_conn.cursor()

    try:
        # Perform the insertion into the feedback table
        db_cursor.execute("""
            INSERT INTO feedback (ration_card, shop_id, quality_rating, service_rating, comment)
            VALUES (?, ?, ?, ?, ?)
        """, (ration_card, shop_id, quality, service, comment))
        
        # We commit the transaction to save changes
        db_conn.commit()
        
    except sqlite3.Error as database_err:
        # Log the error and rollback if something goes wrong
        print(f"Social Audit Error: {database_err}")
        db_conn.rollback()
        return render_template("error.html", message="Unable to submit feedback at this time.")
    
    finally:
        # Resource cleanup
        db_conn.close()

    # 4. Success Handling
    # Instead of just a blank page, we show a professional success message
    return render_template("feedback_success.html", 
                         message="Thank you! Your feedback has been recorded for the National Social Audit.")

# =====================================
# Utility Functions
# =====================================
def get_connection():
    """Returns a new database connection with FK support enabled."""
    # For connections within request handlers, Flask's default behavior with sqlite3 usually
    # manages per-request connections safely. No check_same_thread=False needed here.
    conn = sqlite3.connect("srd.db")
    # CRITICAL FIX: SQLite foreign keys are OFF by default. 
    # Must enable them per-connection for ON DELETE CASCADE to work.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def get_shop_status():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT shop_status FROM settings WHERE id=1")
    row_data = cursor.fetchone()
    status = row_data[0]
    conn.close()
    return status

def token_generated_this_month(ration_card):
    """
    Checks if a token has been generated for the given ration_card in the current month.
    Considers 'waiting', 'completed', or 'skipped' as generated.
    Returns (token_no, code, status) tuple if found, otherwise None.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT token_no, code, status
        FROM tokens
        WHERE ration_card = ?
        AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
        AND status IN ('waiting', 'completed', 'skipped')
        ORDER BY created_at DESC
        LIMIT 1
    """, (ration_card,))
    token_data = cursor.fetchone()
    conn.close()
    return token_data

def get_queue_position(ration_card):
    """
    Returns (my_token_no, people_ahead_count) for the currently waiting token
    of the given ration_card, or None if no active token.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Get citizen's active token number
    cursor.execute("""
        SELECT token_no
        FROM tokens
        WHERE ration_card=? AND status='waiting'
        ORDER BY token_no ASC
        LIMIT 1
    """, (ration_card,))
    row = cursor.fetchone()

    if row is None:
        conn.close()
        return None # No active waiting token for this ration card

    my_token_no = row[0]

    # Count people ahead in the 'waiting' queue
    cursor.execute("""
        SELECT COUNT(*) FROM tokens
        WHERE status='waiting' AND token_no < ?
    """, (my_token_no,))
    ahead_count_row = cursor.fetchone()
    ahead_count = ahead_count_row[0]

    conn.close()
    return my_token_no, ahead_count


# =====================================
# Home Page
# =====================================
@app.route("/")
def home():
    return render_template("index.html")

# =====================================
# Citizen Authentication & Registration
# =====================================
@app.route("/citizen_login", methods=["GET", "POST"])
def citizen_login():
    if request.method == "GET":
        return render_template("citizen_login.html")

    ration_card = request.form["ration_card"].strip().upper()
    password = request.form["password"]

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT ration_card, head_name, rice_allowed FROM citizens WHERE ration_card=? AND password=?",
        (ration_card, password)
    )
    citizen = cursor.fetchone()
    conn.close()

    if citizen is None:
        return render_template(
            "error.html",
            message="Invalid Ration Card or Password"
        )

    session["ration_card"] = citizen[0] # Store login session
    session["name"] = citizen[1]        # Store name for history logic
    return redirect(url_for("citizen_dashboard"))


@app.route("/citizen_register", methods=["GET", "POST"])
def citizen_register():
    if request.method == "GET":
        return render_template("citizen_register.html")

    # Read & Normalize Input
    ration_card = request.form["ration_card"].strip().upper()
    head_name = request.form["head_name"].strip().title() # Capitalize first letter of each word
    password = request.form["password"]

    aadhar_list = request.form.getlist("aadhar[]")
    name_list = request.form.getlist("name[]")

    # Ration Card Validation (Format: RC12345678)
    if not re.fullmatch(r"RC\d{8}", ration_card):
        return render_template(
            "error.html",
            message="Invalid Ration Card Format. Expected: RC12345678"
        )

    # Basic Safety Checks
    if not head_name:
        return render_template("error.html", message="Family Head Name Required.")
    if not password or len(password) < 8:
        return render_template("error.html", message="Password must be at least 8 characters.")
    if not aadhar_list or not name_list or len(aadhar_list) != len(name_list) or len(aadhar_list) == 0:
        return render_template("error.html", message="Please add at least one family member with valid details.")

    # Business Rule: Calculate rice allowed
    family_count = len(aadhar_list)
    rice_allowed = family_count * 5 # Example: 5kg per member

    conn = get_connection()
    cursor = conn.cursor()

    # Prevent Duplicate Ration Card Registration
    cursor.execute(
        "SELECT ration_card FROM citizens WHERE ration_card=?",
        (ration_card,)
    )
    if cursor.fetchone():
        conn.close()
        return render_template(
            "error.html",
            message=f"Ration Card '{ration_card}' Already Registered"
        )

    try:
        # Insert Citizen
        cursor.execute(
            "INSERT INTO citizens (ration_card, head_name, password, rice_allowed) VALUES (?, ?, ?, ?)",
            (ration_card, head_name, password, rice_allowed)
        )

        # Insert Family Members
        for aadhar, name in zip(aadhar_list, name_list):
            aadhar = aadhar.strip().upper().replace(" ", "") # Remove spaces for DB storage
            name = name.strip().title() # Capitalize names

            # Validate Aadhaar format: 12 digits after stripping spaces
            if not aadhar or not name or not re.fullmatch(r'\d{12}', aadhar):
                raise ValueError(f"Invalid Aadhaar ('{aadhar}') or Name ('{name}') for a family member. Aadhaar must be 12 digits.")

            cursor.execute(
                "INSERT INTO family_members (ration_card, aadhar, name) VALUES (?, ?, ?)",
                (ration_card, aadhar, name)
            )

        conn.commit()
        return render_template("register_success.html", message="Registration Successful!")

    except sqlite3.IntegrityError as e:
        conn.rollback()
        # Specific error for duplicate Aadhaar
        if "UNIQUE constraint failed: family_members.aadhar" in str(e):
            return render_template("error.html", message="One or more Aadhaar numbers already registered with another family.")
        return render_template("error.html", message=f"Database error during registration: {e}")
    except ValueError as e:
        conn.rollback()
        return render_template("error.html", message=f"Registration failed: {e}")
    finally:
        conn.close()


@app.route("/citizen_dashboard")
def citizen_dashboard():
    # 1. Verification of user session to prevent unauthorized access
    ration_card = session.get("ration_card")
    if not ration_card:
        # Redirect to login page if session is not found
        return redirect(url_for("citizen_login"))

    # 2. Establish database connection for data retrieval
    conn = get_connection()
    cursor = conn.cursor()

    # 3. Retrieve core beneficiary information
    cursor.execute(
        "SELECT head_name, rice_allowed FROM citizens WHERE ration_card=?",
        (ration_card,)
    )
    citizen_info = cursor.fetchone()

    # Handle cases where session exists but user is deleted from DB
    if citizen_info is None:
        conn.close()
        session.clear()
        return redirect(url_for("citizen_login"))

    head_name, rice_allowed = citizen_info

    # 4. RECTIFYING THE UNDEFINED ERROR: Calculate Shop Inventory
    # Your HTML (Line 831) requires 'rice_stock' to render the progress bar.
    # We calculate this by taking a default stock and subtracting successful transactions.
    default_monthly_stock = 1500.0  # Setting 1500kg as the monthly stock limit
    cursor.execute("SELECT SUM(quantity) FROM transactions")
    distributed_sum_row = cursor.fetchone()
    distributed_amount = distributed_sum_row[0] if distributed_sum_row and distributed_sum_row[0] else 0
    
    # Calculate the remaining stock variable for the template
    current_rice_stock_value = default_monthly_stock - distributed_amount

    # 5. Token and Queue Management Logic
    token_info = token_generated_this_month(ration_card)

    # Initialize variables for the template context
    token_no = None
    token_code = None
    token_status = None
    people_ahead = None

    # Determine current status of the citizen in the system queue
    if token_info:
        token_no, token_code, token_status = token_info

        # If citizen is currently in 'waiting' status, calculate people ahead
        if token_status == "waiting":
            queue_info = get_queue_position(ration_card)

            if queue_info:
                my_token, ahead = queue_info
                people_ahead = ahead

    # Close database connection before rendering the UI
    conn.close()

    # 6. Final Rendering with all required context variables
    # Note: 'rice_stock' is passed here to solve the Jinja2 UndefinedError
    return render_template(
        "citizen_dashboard.html",
        name=head_name,
        rice=rice_allowed,
        ration_card=ration_card,
        token_no=token_no,
        token_code=token_code,
        token_status=token_status,
        people_ahead=people_ahead,
        rice_stock=current_rice_stock_value
    )


# ========================================================
# NEW: Allocation History Route
# This logic pulls REAL data from the transactions table.
# ========================================================
@app.route('/allocation_history')
def allocation_history():
    # 1. Check if user is logged in
    if 'ration_card' not in session:
        return redirect(url_for('citizen_login'))
    
    name = session.get('name')
    ration_card = session.get('ration_card')

    # 2. Connect to the srd.db (Your heart of the project)
    conn = get_connection()
    # Row Factory enables the record.column_name logic in HTML
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 3. Pull historical transactions
    cursor.execute("""
        SELECT * FROM transactions 
        WHERE ration_card = ? 
        ORDER BY date_time DESC
    """, (ration_card,))
    history_data = cursor.fetchall()

    # 4. Summary analytics for the UI cards
    cursor.execute("SELECT SUM(quantity) FROM transactions WHERE ration_card = ?", (ration_card,))
    total_rice_row = cursor.fetchone()
    total_received = total_rice_row[0] if total_rice_row[0] else 0

    cursor.execute("SELECT shop_id FROM transactions WHERE ration_card = ? LIMIT 1", (ration_card,))
    shop_row = cursor.fetchone()
    last_shop = shop_row[0] if shop_row else "N/A"

    conn.close()

    # 5. Render with all real data variables
    return render_template('history.html', 
                         name=name, 
                         history_data=history_data, 
                         total_rice_received=total_received,
                         shop_id=last_shop)

# ========================================================
# RECTIFIED: My Ration Card Route
# This function fixes the BuildError by creating the missing endpoint.
# It also fetches the exact data required by ration_card.html.
# ========================================================
@app.route("/my_ration_card")
def my_ration_card():
    # 1. Validation: Ensure the citizen is actually logged in
    ration_card_session = session.get("ration_card")
    
    if not ration_card_session:
        # Redirect to login if session has expired or is missing
        return redirect(url_for("citizen_login"))

    # 2. Database interaction to fetch real-time profile data
    db_connection = get_connection()
    # Row factory allows the template to use dot notation like citizen.head_name
    db_connection.row_factory = sqlite3.Row
    db_cursor = db_connection.cursor()

    try:
        # Fetch the primary details of the citizen (Head of Family)
        db_cursor.execute(
            "SELECT * FROM citizens WHERE ration_card = ?", 
            (ration_card_session,)
        )
        citizen_record = db_cursor.fetchone()

        # Fetch all associated family members for this specific ration card
        db_cursor.execute(
            "SELECT * FROM family_members WHERE ration_card = ?", 
            (ration_card_session,)
        )
        family_list = db_cursor.fetchall()

    except sqlite3.Error as database_error:
        # Log error to console and show a user-friendly error page
        print(f"Database error: {database_error}")
        return render_template("error.html", message="Could not retrieve card details.")
    
    finally:
        # Always close the connection to prevent database locking
        db_connection.close()

    # 3. Final Step: Render the beautiful ration_card.html you provided
    # We pass 'citizen' and 'members' variables to match your Jinja2 loops
    return render_template(
        "ration_card.html", 
        citizen=citizen_record, 
        members=family_list
    )
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# =====================================
# Book Token
# =====================================
@app.route("/book_token", methods=["GET", "POST"])
def book_token():
    ration_card = session.get("ration_card")
    if not ration_card:
        return redirect(url_for("citizen_login")) # Must be logged in to book token

    if get_shop_status() == "closed":
        return render_template("error.html", message="Shop is currently CLOSED. Please try again later.")

    # Check if a token has already been generated for this month
    if token_generated_this_month(ration_card):
        return render_template(
            "error.html",
            message="You have already generated a token for this month. You can check its status on your dashboard."
        )

    conn = get_connection()
    cursor = conn.cursor()

    # Generate next token number (guaranteed unique for the day)
    cursor.execute("SELECT MAX(token_no) FROM tokens")
    last_token_result = cursor.fetchone()
    last_token_val = last_token_result[0]
    last_token_no = last_token_val if last_token_result and last_token_val is not None else 0
    token_number = last_token_no + 1
    code = random.randint(1000, 9999)

    # Insert new token with 'waiting' status
    cursor.execute(
        "INSERT INTO tokens (ration_card, token_no, code, status) VALUES (?, ?, ?, ?)",
        (ration_card, token_number, code, "waiting")
    )
    conn.commit()
    conn.close()

    # Redirect to dashboard, which will now show the newly booked token
    return redirect(url_for("citizen_dashboard"))


# =====================================
# Dealer Dashboard (PURE FIFO) & Actions
# =====================================
@app.route("/dealer_login", methods=["GET", "POST"])
def dealer_login():

    if request.method == "GET":
        return render_template("dealer_login.html")

    shop_id = request.form["shop_id"].strip().upper()
    password = request.form["password"]

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT shop_id FROM dealers WHERE shop_id=? AND password=?",
        (shop_id, password)
    )

    dealer = cursor.fetchone()
    conn.close()

    if dealer is None:
        return render_template("error.html", message="Invalid Shop ID or Password")

    session["dealer"] = shop_id   # ✅ SESSION CREATED
    return redirect(url_for("dealer_dashboard"))

# =====================================
# Dealer Dashboard (RECTIFIED)
# Handles real-time inventory and queue management
# =====================================
@app.route("/dealer")
def dealer_dashboard():
    # 1. AUTHENTICATION: Check if the dealer is logged in via session
    if "dealer" not in session:
        # Redirect to login if session is not active
        return redirect(url_for("dealer_login"))

    # 2. DATABASE: Establish connection and prepare for queries
    conn = get_connection()
    cursor = conn.cursor()

    # 3. QUEUE LOGIC: Get the CURRENT token in line (FIFO)
    # This selects the next 'waiting' token, or a 'skipped' one if no waiting exists
    cursor.execute("""
        SELECT id, ration_card, token_no, code, status
        FROM tokens
        WHERE status IN ('waiting', 'skipped')
        ORDER BY
            CASE status
                WHEN 'waiting' THEN 0
                WHEN 'skipped' THEN 1
            END,
            token_no ASC
        LIMIT 1
    """)
    current_token = cursor.fetchone()

    # 4. SKIPPED TOKENS: Fetch all tokens marked as 'skipped' for the sidebar
    cursor.execute("""
        SELECT id, ration_card, token_no 
        FROM tokens 
        WHERE status = 'skipped' 
        ORDER BY token_no ASC
    """)
    skipped_list = cursor.fetchall()

    # 5. SHOP STATUS: Retrieve open/closed state from settings
    shop_status = get_shop_status()

    # 6. INVENTORY LOGIC: Rectifying the 'rice_stock' UndefinedError
    # We define a warehouse capacity and subtract what has been distributed
    warehouse_capacity = 1000.0
    cursor.execute("SELECT SUM(quantity) FROM transactions")
    distributed_result = cursor.fetchone()
    total_distributed = distributed_result[0] if distributed_result and distributed_result[0] else 0
    
    # This variable satisfies the requirement in dealer.html line 389
    available_rice_stock = warehouse_capacity - total_distributed

    # 7. ANALYTICS: Summary statistics for the dashboard cards
    cursor.execute("SELECT COUNT(*) FROM tokens WHERE status='completed'")
    served_count_row = cursor.fetchone()
    served_total = served_count_row[0]
    
    cursor.execute("SELECT COUNT(*) FROM tokens WHERE status='waiting'")
    waiting_count_row = cursor.fetchone()
    waiting_total = waiting_count_row[0]

    # Close connection to free up the srd.db file
    conn.close()

    # 8. RENDERING: Passing all variables to the template
    # Note: 'rice_stock' and 'skipped_tokens' are critical here
    return render_template(
        "dealer.html", 
        token=current_token, 
        shop_status=shop_status,
        served_count=served_total,
        waiting_count=waiting_total,
        rice_stock=available_rice_stock,
        skipped_tokens=skipped_list
    )
# ========================================================
# RECTIFIED: Dealer Intelligence & Analytics Route
# Provides real-time data visualization for Chart.js
# ========================================================
@app.route("/dealer_analytics")
def dealer_analytics():
    # 1. AUTHENTICATION: Ensure only authorized dealers access the terminal
    if "dealer" not in session:
        return redirect(url_for("dealer_login"))
    
    shop_id = session.get("dealer")
    
    # 2. DATABASE INITIALIZATION
    conn = get_connection()
    # Row factory is essential for accessing columns by name
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # 3. KPI CALCULATION: Total successful distributions
        cursor.execute(
            "SELECT COUNT(*) as total FROM transactions WHERE shop_id = ?", 
            (shop_id,)
        )
        total_served_row = cursor.fetchone()
        total_served = total_served_row['total'] if total_served_row else 0

        # 4. TREND ANALYSIS: Weekly Rice Outflow (Last 7 Days)
        # We aggregate quantity by date to create the line chart
        cursor.execute("""
            SELECT date(date_time) as trans_date, SUM(quantity) as daily_sum 
            FROM transactions 
            WHERE shop_id = ? 
            GROUP BY trans_date 
            ORDER BY trans_date ASC 
            LIMIT 7
        """, (shop_id,))
        trend_records = cursor.fetchall()
        
        # Format lists for Chart.js (Labels = Dates, Values = KG)
        # Coerce DB types to plain Python types to ensure safe JSON serialization
        if trend_records:
            labels = [str(row['trans_date']) for row in trend_records]
            values = [float(row['daily_sum'] or 0) for row in trend_records]
        else:
            labels = ["No Data"]
            values = [0]

        # 5. DEMOGRAPHIC SEGMENTATION: Family Size Mix
        # Logic: We count how many small/medium/large families are served
        # Based on your business rule (5kg per member)
        cursor.execute("""
            SELECT 
                CASE 
                    WHEN quantity <= 10 THEN 'Small (1-2)'
                    WHEN quantity <= 20 THEN 'Medium (3-4)'
                    ELSE 'Large (5+)'
                END as family_type,
                COUNT(*) as card_count
            FROM transactions 
            WHERE shop_id = ? 
            GROUP BY family_type
        """, (shop_id,))
        demo_records = cursor.fetchall()
        
        if demo_records:
            demo_labels = [str(row['family_type']) for row in demo_records]
            demo_values = [int(row['card_count'] or 0) for row in demo_records]
        else:
            demo_labels = ["N/A"]
            demo_values = [1]

    except sqlite3.Error as e:
        # Fault Tolerance: Print error to console and provide fallback data
        print(f"Analytics Data Error: {e}")
        labels, values = ["Error"], [0]
        demo_labels, demo_values = ["Error"], [0]
        total_served = 0
        
    finally:
        # Resources must always be released in production environments
        conn.close()

    # 6. DATA TRANSMISSION: Send processed arrays to dealer_analytics.html
    # This matches the tojson calls in your script section
    return render_template(
        "dealer_analytics.html",
        shop_id=shop_id,
        total_served=total_served,
        labels=labels,
        values=values,
        demo_labels=demo_labels,
        demo_values=demo_values
    )

@app.route("/recall_token", methods=["POST"])
def recall_token():
    if "dealer" not in session:
        return redirect(url_for("dealer_login"))
    
    token_id = request.form["token_id"]
    conn = get_connection()
    cursor = conn.cursor()
    # Change status back to waiting so it appears at the top of the queue
    cursor.execute("UPDATE tokens SET status='waiting' WHERE id=?", (token_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("dealer_dashboard"))
@app.route("/complete_token", methods=["POST"])
def complete_token():
    if "dealer" not in session:
        return redirect(url_for("dealer_login"))
    
    token_id = request.form["token_id"]
    entered_code = request.form["code"].strip()
    shop_id = session.get("dealer")

    conn = get_connection()
    cursor = conn.cursor()

    # 1. Verification of code and retrieval of Ration Card
    cursor.execute("SELECT code, ration_card FROM tokens WHERE id=?", (token_id,))
    row = cursor.fetchone()

    if row and str(row[0]) == entered_code:
        ration_card = row[1]
        
        # 2. Get the Rice Allowed for this family to record in history
        cursor.execute("SELECT rice_allowed FROM citizens WHERE ration_card=?", (ration_card,))
        rice_row = cursor.fetchone()
        rice_allowed = rice_row[0]

        # 3. Mark Token as Completed
        now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "UPDATE tokens SET status='completed', served_at=? WHERE id=?",
            (now_time, token_id)
        )

        # 4. LOG REAL TRANSACTION DATA (Ensures History is not empty)
        cursor.execute("""
            INSERT INTO transactions (ration_card, date_time, shop_id, quantity, status)
            VALUES (?, ?, ?, ?, ?)
        """, (ration_card, now_time, shop_id, rice_allowed, 'completed'))

        conn.commit()
    else:
        conn.close()
        return render_template("error.html", message="Invalid Security PIN. Transaction Failed!")

    conn.close()
    return redirect(url_for("dealer_dashboard"))

@app.route("/cancel_token", methods=["POST"])
def cancel_token():
    # !!! IMPORTANT: Implement dealer authentication here !!!
    if "dealer" not in session:
        return redirect(url_for("dealer_login"))
    token_id = request.form["token_id"]
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tokens WHERE id=?", (token_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("dealer_dashboard"))

@app.route("/skip_token", methods=["POST"])
def skip_token():
    # !!! IMPORTANT: Implement dealer authentication here !!!
    if "dealer" not in session:
        return redirect(url_for("dealer_login"))
    token_id = request.form["token_id"]
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE tokens SET status='skipped' WHERE id=?",
        (token_id,)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("dealer_dashboard"))

@app.route("/reset_day")
def reset_day():
    # !!! IMPORTANT: Implement dealer authentication here !!!
    if "dealer" not in session:
        return redirect(url_for("dealer_login"))
    # This action deletes ALL tokens, use with extreme caution!
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tokens")
    conn.commit()
    conn.close()
    return redirect(url_for("dealer_dashboard"))

@app.route("/open_shop")
def open_shop():
    # !!! IMPORTANT: Implement dealer authentication here !!!
    if "dealer" not in session:
        return redirect(url_for("dealer_login"))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE settings SET shop_status='open' WHERE id=1")
    conn.commit()
    conn.close()
    return redirect(url_for("dealer_dashboard"))

@app.route("/close_shop")
def close_shop():
    # !!! IMPORTANT: Implement dealer authentication here !!!
    if "dealer" not in session:
        return redirect(url_for("dealer_login"))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE settings SET shop_status='closed' WHERE id=1")
    conn.commit()
    conn.close()
    return redirect(url_for("dealer_dashboard"))


# =====================================
# Public Display & Stats
# =====================================
@app.route("/display")
def public_display():
    conn = get_connection()
    cursor = conn.cursor()

    # Query for CURRENT active token
    cursor.execute("""
        SELECT token_no FROM tokens
        WHERE status='waiting'
        ORDER BY token_no ASC
        LIMIT 1
    """)
    now_token_row = cursor.fetchone()
    now_token = now_token_row[0] if now_token_row else None

    # RECTIFIED: Simplified logic to get the NEXT person in the queue.
    # We simply select the person at OFFSET 1 of the 'waiting' list.
    cursor.execute("""
        SELECT token_no FROM tokens
        WHERE status='waiting'
        ORDER BY token_no ASC
        LIMIT 1 OFFSET 1
    """) 
    next_token_row = cursor.fetchone()
    next_token = next_token_row[0] if next_token_row else None


    cursor.execute("""
        SELECT COUNT(*) FROM tokens
        WHERE status='waiting'
    """)
    waiting_count_row = cursor.fetchone()
    waiting_count = waiting_count_row[0]

    shop_status = get_shop_status()
    conn.close()

    return render_template(
        "display.html",
        now_token=now_token,
        next_token=next_token,
        waiting_count=waiting_count,
        shop_status=shop_status
    )

@app.route("/stats")
def stats():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM tokens")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM tokens WHERE status='waiting'")
    waiting = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM tokens WHERE status='completed'")
    completed = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM tokens WHERE status='skipped'")
    skipped = cursor.fetchone()[0]

    conn.close()

    return render_template(
        "stats.html",
        total=total,
        waiting=waiting,
        completed=completed,
        skipped=skipped
    )

@app.route("/search", methods=["GET", "POST"])
def search():
    token_info = None
    if request.method == "POST":
        ration_card = request.form["ration_card"].strip().upper()
        if ration_card:
            conn = get_connection()
            cursor = conn.cursor()
            # Fetch most recent token for this ration card
            cursor.execute("""
                SELECT token_no, code, status, created_at, served_at FROM tokens
                WHERE ration_card=?
                ORDER BY created_at DESC
                LIMIT 1
            """, (ration_card,))
            token_info = cursor.fetchone()
            conn.close()
            if not token_info:
                 return render_template("error.html", message=f"No token found for Ration Card: {ration_card}.")
        else:
            return render_template("error.html", message="Ration card cannot be empty for search.")

    return render_template("search.html", token=token_info)


# =====================================
# Run App
# =====================================

# CRITICAL DEPLOYMENT FIX: 
# Move init_db() outside the __main__ check so it runs when 
# imported by a production WSGI server (Gunicorn/Waitress).
init_db() 

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)