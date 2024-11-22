from flask import Flask, render_template, request, redirect, url_for, flash, session
import pickle
import numpy as np
import mysql.connector
from flask_bcrypt import Bcrypt
from gtts import gTTS
import os
import pandas as pd
import re
from dotenv import load_dotenv

load_dotenv()

# Load the book recommendation data
try:
    popular_df = pickle.load(open('popular.pkl', 'rb'))
    pt = pickle.load(open('pt.pkl', 'rb'))
    books = pickle.load(open('books.pkl', 'rb'))
    similarity_scores = pickle.load(open('similarity_scores.pkl', 'rb'))
except Exception as e:
    print(f"Error loading pickle files: {e}")
    raise

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')  # Secret key from .env
bcrypt = Bcrypt(app)

# MySQL database connection function using environment variables
def get_db_connection():
    try:
        connection = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST"),
            user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASSWORD"),
            database=os.getenv("MYSQL_DB")
        )
        return connection
    except mysql.connector.Error as err:
        print(f"Error connecting to database: {err}")
        raise

# Ensure the 'static/audio' directory exists
audio_dir = 'static/audio'
if not os.path.exists(audio_dir):
    os.makedirs(audio_dir)

# Function to clean file names by removing special characters
def clean_filename(title):
    return re.sub(r'[\\/*?:"<>|\'\"]', "", title)  # Remove special characters

# Generate audio introductions for the top 50 books
top_50_books = popular_df.head(50)
for index, row in top_50_books.iterrows():
    book_title = row['Book-Title']  
    safe_title = clean_filename(book_title)  # Clean the book title
    intro_text = f"This is  '{book_title}', a popular book  by {row['Book-Author']}."  
    audio_file_path = f"{audio_dir}/{safe_title}_intro.mp3"
    
    # Check if the audio file already exists
    if not os.path.exists(audio_file_path):
        tts = gTTS(text=intro_text, lang='en')
        tts.save(audio_file_path)

# Registration Route
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        # Validation for username and email
        errors = []
        
        # Validate username
        if not (3 <= len(username) <= 20) or not username.isalnum():
            errors.append('Username must be between 3 to 20 characters long and contain only letters and numbers.')

        # Validate email format
        email_regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
        if not re.match(email_regex, email):
            errors.append('Please enter a valid email address.')

        if errors:
            for error in errors:
                flash(error, 'danger')
            return redirect(url_for('register'))

        password = bcrypt.generate_password_hash(password).decode('utf-8')

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                'INSERT INTO users (username, email, password) VALUES (%s, %s, %s)',
                (username, email, password)
            )
            conn.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except mysql.connector.Error as err:
            flash(f"Error: {err}", 'danger')
        finally:
            cursor.close()
            conn.close()
    return render_template('register.html')

# Login Route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Sanitize username (ensure it is alphanumeric)
        if not username.isalnum():
            flash('Username can only contain letters and numbers.', 'danger')
            return redirect(url_for('login'))

        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            # Check if the user exists in the database
            cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
            user = cursor.fetchone()

            if user:
                # If user found, check if password is correct
                if bcrypt.check_password_hash(user['password'], password):
                    session['user_id'] = user['id']
                    session['username'] = user['username']
                    flash('Login successful!', 'success')
                    return redirect(url_for('index'))
                else:
                    flash('Invalid password, please try again.', 'danger')
            else:
                flash('Username not found, please try again or register.', 'danger')

        except mysql.connector.Error as err:
            flash(f"Error: {err}", 'danger')
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    return render_template('login.html')

# Logout Route
@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# Home Page (Accessible after login)
@app.route('/')
@app.route('/index')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html',
                           book_name=list(popular_df['Book-Title'].values),
                           author=list(popular_df['Book-Author'].values),
                           image=list(popular_df['Image-URL-M'].values),
                           votes=list(popular_df['num_ratings'].values),
                           rating=list(popular_df['avg_rating'].values))

# Book Detail Page with Audio Intro
@app.route('/book/<book_title>')
def book_detail(book_title):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # Find book details
    book = popular_df[popular_df['Book-Title'] == book_title].iloc[0]
    audio_file = f"static/audio/{book_title}_intro.mp3"
    
    # Handle the case where the book title is not found
    if not os.path.exists(audio_file):
        flash(f"Audio file for {book_title} not found.", 'danger')
        return redirect(url_for('index'))
    
    return render_template('book_detail.html', book=book, audio_file=audio_file)

# Recommend Page UI
@app.route('/recommend')
def recommend_ui():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('recommend.html')

# Recommend Books Logic
@app.route('/recommend_books', methods=['POST'])
def recommend():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_input = request.form.get('user_input')

    # Check if user input is valid
    if user_input not in pt.index:
        flash(f"Book '{user_input}' not found in the database.", 'danger')
        return redirect(url_for('recommend_ui'))

    index = np.where(pt.index == user_input)[0][0]
    similar_items = sorted(list(enumerate(similarity_scores[index])), key=lambda x: x[1], reverse=True)[1:9]

    data = []
    for i in similar_items:
        item = []
        temp_df = books[books['Book-Title'] == pt.index[i[0]]]
        item.extend(list(temp_df.drop_duplicates('Book-Title')['Book-Title'].values))
        item.extend(list(temp_df.drop_duplicates('Book-Title')['Book-Author'].values))
        item.extend(list(temp_df.drop_duplicates('Book-Title')['Image-URL-M'].values))

        data.append(item)

    return render_template('recommend.html', data=data)

# Contact Page
@app.route('/contact')
def contact():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('contact.html')

if __name__ == '__main__':
    app.run(debug=True)
