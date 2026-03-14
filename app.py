import os
import time
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from sqlalchemy import func, desc, text
from datetime import datetime, timedelta
import pandas as pd

from models import db, User, Client, Order

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')

# PostgreSQL Connection from Docker environment
DB_USER = os.environ.get('POSTGRES_USER', 'myuser')
DB_PASSWORD = os.environ.get('POSTGRES_PASSWORD', 'mypassword')
DB_NAME = os.environ.get('POSTGRES_DB', 'mydb')
DB_HOST = os.environ.get('DB_HOST', 'localhost')

app.config['SQLALCHEMY_DATABASE_URI'] = f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:5432/{DB_NAME}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

def wait_for_db():
    """Wait for PostgreSQL to become available."""
    print("Waiting for database...")
    max_retries = 30
    retry_interval = 2
    for i in range(max_retries):
        try:
            with app.app_context():
                db.session.execute(text('SELECT 1'))
                print("Database is ready!")
                return
        except Exception as e:
            print(f"Database not ready (attempt {i+1}/{max_retries}): {e}")
            time.sleep(retry_interval)
    raise Exception("Could not connect to the database after several retries.")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- ROUTES ---

@app.route('/')
@login_required
def dashboard():
    region_filter = request.args.get('region')
    industry_filter = request.args.get('industry')
    period = request.args.get('period', '30')
    
    try:
        days = int(period)
    except ValueError:
        days = 30

    # Base queries for filtering
    client_query = Client.query
    order_query = db.session.query(Order).join(Client)

    if region_filter:
        client_query = client_query.filter(Client.region == region_filter)
        order_query = order_query.filter(Client.region == region_filter)
    if industry_filter:
        client_query = client_query.filter(Client.industry == industry_filter)
        order_query = order_query.filter(Client.industry == industry_filter)

    # 1. KPIs
    total_revenue = order_query.with_entities(func.sum(Order.amount)).scalar() or 0
    total_clients = client_query.count()
    avg_order = order_query.with_entities(func.avg(Order.amount)).scalar() or 0
    
    # 2. Sales Trend
    today = datetime.utcnow()
    start_date = today - timedelta(days=days)
    
    sales_trend = order_query.with_entities(
        func.date(Order.created_at).label('date'),
        func.sum(Order.amount).label('total')
    ).filter(Order.created_at >= start_date).group_by(func.date(Order.created_at)).order_by(func.date(Order.created_at)).all()
    
    labels = [s.date.strftime('%Y-%m-%d') for s in sales_trend]
    values = [float(s.total) for s in sales_trend]
    
    # 3. Top 5 Clients
    top_clients = order_query.with_entities(
        Client.name,
        func.sum(Order.amount).label('revenue'),
        func.count(Order.id).label('orders_count')
    ).group_by(Client.id).order_by(desc('revenue')).limit(5).all()
    
    # 4. Regional Distribution
    regional_data = client_query.with_entities(
        Client.region,
        func.count(Client.id).label('count'),
        func.sum(Order.amount).label('revenue')
    ).join(Order, isouter=True).group_by(Client.region).all()
    
    reg_labels = [r.region for r in regional_data]
    reg_values = [r.count for r in regional_data]
    reg_revenue = [float(r.revenue or 0) for r in regional_data]

    # 5. Industry Analysis
    industry_data = client_query.with_entities(
        Client.industry,
        func.count(Client.id).label('count')
    ).group_by(Client.industry).all()
    
    ind_labels = [i.industry for i in industry_data]
    ind_values = [i.count for i in industry_data]

    # Get unique regions and industries for filters
    all_regions = db.session.query(Client.region).distinct().all()
    all_industries = db.session.query(Client.industry).distinct().all()

    return render_template('dashboard.html', 
                         total_revenue=total_revenue, 
                         total_clients=total_clients,
                         avg_order=avg_order,
                         labels=labels, 
                         values=values,
                         top_clients=top_clients,
                         reg_labels=reg_labels,
                         reg_values=reg_values,
                         reg_revenue=reg_revenue,
                         ind_labels=ind_labels,
                         ind_values=ind_values,
                         regions=[r[0] for r in all_regions if r[0]], 
                         industries=[i[0] for i in all_industries if i[0]],
                         current_region=region_filter,
                         current_industry=industry_filter,
                         current_period=period)

@app.route('/clients')
@login_required
def clients_list():
    region_filter = request.args.get('region')
    industry_filter = request.args.get('industry')
    search_query = request.args.get('search')

    query = Client.query

    if region_filter:
        query = query.filter(Client.region == region_filter)
    if industry_filter:
        query = query.filter(Client.industry == industry_filter)
    if search_query:
        query = query.filter(Client.name.ilike(f'%{search_query}%'))

    clients = query.all()
    
    # Get unique regions and industries for filters
    all_regions = db.session.query(Client.region).distinct().all()
    all_industries = db.session.query(Client.industry).distinct().all()
    
    return render_template('clients.html', 
                         clients=clients, 
                         regions=[r[0] for r in all_regions if r[0]], 
                         industries=[i[0] for i in all_industries if i[0]],
                         current_region=region_filter,
                         current_industry=industry_filter,
                         current_search=search_query)

@app.route('/add_client', methods=['POST'])
@login_required
def add_client():
    name = request.form.get('name')
    email = request.form.get('email')
    region = request.form.get('region')
    industry = request.form.get('industry')
    
    new_client = Client(name=name, email=email, region=region, industry=industry)
    db.session.add(new_client)
    db.session.commit()
    return redirect(url_for('clients_list'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Username exists')
        else:
            new_user = User(username=username, password_hash=generate_password_hash(password))
            db.session.add(new_user)
            db.session.commit()
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- DB INIT ---
@app.cli.command("init-db")
def init_db():
    db.create_all()
    # Create demo user
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', password_hash=generate_password_hash('admin'))
        db.session.add(admin)
        db.session.commit()
    print("Database initialized.")

if __name__ == '__main__':
    wait_for_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
