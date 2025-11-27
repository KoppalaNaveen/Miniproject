import os
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
import bcrypt
import jwt
from datetime import datetime, timedelta
from functools import wraps
from dotenv import load_dotenv
import google.generativeai as genai
import requests
import random

# Load environment variables from a .env file
load_dotenv()

app = Flask(__name__)

# --- SECRET KEY HANDLING ---
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-' + os.urandom(16).hex())
JWT_SECRET = os.environ.get('JWT_SECRET', 'jwt-secret-' + os.urandom(16).hex())

CORS(app, supports_credentials=True, origins=["*"])

TOKEN_EXPIRY_HOURS = 24

# Configure Google Gemini API
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    generation_config = {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 40,
        "max_output_tokens": 1024,
    }
    
    safety_settings = [
        {
            "category": "HARM_CATEGORY_HARASSMENT",
            "threshold": "BLOCK_MEDIUM_AND_ABOVE"
        },
        {
            "category": "HARM_CATEGORY_HATE_SPEECH",
            "threshold": "BLOCK_MEDIUM_AND_ABOVE"
        },
        {
            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "threshold": "BLOCK_MEDIUM_AND_ABOVE"
        },
        {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": "BLOCK_MEDIUM_AND_ABOVE"
        },
    ]
    
    try:
        model = genai.GenerativeModel(
            model_name="gemini-pro",
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        GEMINI_AVAILABLE = True
    except Exception as e:
        print(f"Error initializing Gemini model: {e}")
        GEMINI_AVAILABLE = False
else:
    print("WARNING: GOOGLE_API_KEY not found. AI Chatbot will use simple fallback responses.")
    GEMINI_AVAILABLE = False

users_db = {}
password_reset_tokens_db = {}
chat_sessions = {}

# UPDATED HOUSE PLANS - Match your exact files
HOUSE_PLANS = {
    "1bhk": {
        "400": {
            "filename": "1bhk-400.jpg",
            "description": "Compact 1BHK with efficient space utilization"
        },
        "500": {
            "filename": "1bhk-500.jpg",
            "description": "Spacious 1BHK with balcony"
        },
        "520": {
            "filename": "1bhk-520.jpg",
            "description": "Efficient 1BHK with modern layout"
        },
        "560": {
            "filename": "1bhk-560.png",
            "description": "Cozy 1BHK with open kitchen"
        },
        "625": {
            "filename": "1bhk-625.jpg",
            "description": "Modern 1BHK with extra storage"
        },
        "760": {
            "filename": "1bhk-760.png",
            "description": "Luxury 1BHK with premium amenities"
        },
        "900": {
            "filename": "1bhk-900.png",
            "description": "Executive 1BHK with spacious design"
        },
        "1225": {
            "filename": "1bhk-1225.png",
            "description": "Premium 1BHK with garden view"
        },
        "1748": {
            "filename": "1bhk-1748.png",
            "description": "Deluxe 1BHK with rooftop access"
        }
    },
    "2bhk": {
        "500": {
            "filename": "2bhk-500.jpg",
            "description": "Compact 2BHK with smart space design"
        },
        "700": {
            "filename": "2bhk-700.png",
            "description": "Premium 2BHK with balcony and garden view"
        },
        "800": {
            "filename": "2bhk-800.png",
            "description": "Spacious 2BHK with utility area"
        },
        "1060": {
            "filename": "2bhk-1060.png",
            "description": "Modern 2BHK with open kitchen"
        },
        "1500": {
            "filename": "2bhk-1500.png",
            "description": "Luxury 2BHK with master suite"
        }
    },
    "3bhk": {
        "900": {
            "filename": "3bhk-900.png",
            "description": "Compact 3BHK with efficient layout"
        },
        "1400": {
            "filename": "3bhk-1400.png",
            "description": "Spacious 3BHK with modern amenities"
        },
        "1450": {
            "filename": "3bhk-1450.png",
            "description": "Luxury 3BHK with master suite"
        },
        "1580": {
            "filename": "3bhk-1580.png",
            "description": "Premium 3BHK with balcony and garden view"
        },
        "1800": {
            "filename": "3bhk-1800.png",
            "description": "Deluxe 3BHK with extensive amenities"
        }
    }
}

HOUSEPLANS_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "houseplans")

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({'error': 'Token is missing'}), 401
        try:
            token = auth_header.split()[1]
            data = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
            current_user = users_db.get(data['user_id'])
            if not current_user:
                return jsonify({'error': 'User not found'}), 404
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token has expired'}), 401
        except Exception:
            return jsonify({'error': 'Token is invalid'}), 401
        return f(current_user, *args, **kwargs)
    return decorated

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/houseplans/<filename>')
def serve_houseplan(filename):
    return send_from_directory(HOUSEPLANS_FOLDER, filename)

@app.route('/api/get-plan', methods=['POST'])
def get_plan():
    try:
        data = request.get_json()
        square_footage = str(data.get('square_footage', "")).strip()
        bedrooms = data.get('bedrooms', "")
        bhk = f"{bedrooms}bhk"

        # Validate inputs
        if not bhk or not square_footage or not square_footage.isdigit():
            return jsonify({"error": "Invalid request parameters"}), 400

        if bhk in HOUSE_PLANS:
            available_sizes = list(HOUSE_PLANS[bhk].keys())
            sizes_int = [int(size) for size in available_sizes]
            requested = int(square_footage)
            
            # Find exact match first
            if square_footage in HOUSE_PLANS[bhk]:
                plan_data = HOUSE_PLANS[bhk][square_footage]
                filename = plan_data.get("filename", "")
                file_path = os.path.join(HOUSEPLANS_FOLDER, filename)

                if os.path.isfile(file_path):
                    image_url = f"/houseplans/{filename}"
                    return jsonify({
                        "success": True,
                        "exact_match": True,
                        "bhk": bhk,
                        "size": square_footage,
                        "requested_size": square_footage,
                        "image_url": image_url,
                        "direct_link": image_url,
                        "description": plan_data.get("description", "")
                    })
                else:
                    return jsonify({
                        "error": f"Plan file not found: {filename}"
                    }), 404
            
            # Find the closest available plan
            smaller_plans = [size for size in sizes_int if size < requested]
            larger_plans = [size for size in sizes_int if size > requested]
            
            # Try smaller plans first
            if smaller_plans:
                nearest_size = max(smaller_plans)
                nearest_size_str = str(nearest_size)
                plan_data = HOUSE_PLANS[bhk][nearest_size_str]
                filename = plan_data.get("filename", "")
                file_path = os.path.join(HOUSEPLANS_FOLDER, filename)

                if os.path.isfile(file_path):
                    image_url = f"/houseplans/{filename}"
                    return jsonify({
                        "success": True,
                        "exact_match": False,
                        "smaller_plan": True,
                        "bhk": bhk,
                        "size": nearest_size_str,
                        "requested_size": square_footage,
                        "image_url": image_url,
                        "direct_link": image_url,
                        "description": plan_data.get("description", "")
                    })
            
            # Try larger plans if no smaller ones
            if larger_plans:
                nearest_size = min(larger_plans)
                nearest_size_str = str(nearest_size)
                plan_data = HOUSE_PLANS[bhk][nearest_size_str]
                filename = plan_data.get("filename", "")
                file_path = os.path.join(HOUSEPLANS_FOLDER, filename)

                if os.path.isfile(file_path):
                    image_url = f"/houseplans/{filename}"
                    return jsonify({
                        "success": True,
                        "exact_match": False,
                        "larger_plan": True,
                        "bhk": bhk,
                        "size": nearest_size_str,
                        "requested_size": square_footage,
                        "image_url": image_url,
                        "direct_link": image_url,
                        "description": plan_data.get("description", "")
                    })
            
            # If no plans available
            available_sizes_str = ", ".join(available_sizes)
            return jsonify({
                "error": f"No house plans available for {bhk} with size {square_footage} sq ft. "
                         f"Available sizes for {bhk}: {available_sizes_str} sq ft."
            }), 404
        else:
            return jsonify({"error": f"No house plans available for {bhk}. Please try 1, 2, or 3 BHK."}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        
        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400
        
        # Check if user exists
        user = None
        user_id_to_check = None
        for user_id, user_data in users_db.items():
            if user_data['email'] == email:
                user = user_data
                user_id_to_check = user_id
                break
        
        if not user:
            # Auto-create account with any email/password
            user_id = str(len(users_db) + 1)
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
            user = {
                'id': user_id,
                'email': email,
                'name': email.split('@')[0],  # Use part of email as name
                'password': hashed_password.decode('utf-8')
            }
            users_db[user_id] = user
        
        # Verify password
        if not bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
            return jsonify({'error': 'Invalid credentials'}), 401
        
        # Generate token
        token = jwt.encode({
            'user_id': user['id'],
            'exp': datetime.utcnow() + timedelta(hours=TOKEN_EXPIRY_HOURS)
        }, JWT_SECRET, algorithm='HS256')
        
        return jsonify({
            'message': 'Login successful',
            'token': token,
            'user': {
                'id': user['id'],
                'email': user['email'],
                'name': user['name']
            }
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        message = data.get('message', '').strip()
        action = data.get('action', '')
        
        if not message and action != 'clear':
            return jsonify({'error': 'Message is required'}), 400
        
        if action == 'clear':
            return jsonify({
                'response': 'How can I help you with your house planning needs today?',
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'source': 'system'
            }), 200
        
        if GEMINI_AVAILABLE:
            try:
                chat_session = model.start_chat(history=[])
                response = chat_session.send_message(message)
                
                return jsonify({
                    'response': response.text,
                    'timestamp': datetime.now().strftime('%H:%M:%S'),
                    'source': 'gemini'
                }), 200
                
            except Exception as e:
                print(f"Gemini API error: {e}")
                return get_simple_chat_response(message)
        else:
            return get_simple_chat_response(message)
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_simple_chat_response(message):
    """Fallback simple chat responses when Gemini is not available"""
    message_lower = message.lower()
    
    responses = {
        'hello': 'Hello! How can I help you with your house planning needs today?',
        'hi': 'Hi there! Welcome to NIVASA AI. How can I assist you?',
        'help': 'I can help you with questions about our house plans for 1, 2, and 3 BHKs.',
        'plan': 'We offer house plans for 1, 2, and 3 BHK configurations. What are you looking for?',
        'bhk': 'We have plans for 1 BHK (400-1748 sq ft), 2 BHK (500-1500 sq ft), and 3 BHK (900-1800 sq ft).',
        'thank': 'You\'re welcome! Is there anything else I can help you with?',
        'bye': 'Goodbye! Feel free to come back if you have more questions.',
        'clear': 'Conversation cleared. How can I help you with your house planning needs today?'
    }
    
    for keyword, bot_response in responses.items():
        if keyword in message_lower:
            return jsonify({
                'response': bot_response,
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'source': 'simple'
            }), 200
            
    default_responses = [
        "I'm sorry, my advanced AI capabilities are currently offline. Please ensure the GOOGLE_API_KEY is configured correctly to enable intelligent responses.",
        "My apologies, I can't provide a detailed answer right now as my main AI is not connected. I can only handle basic questions about our house plans.",
        "It seems my connection to the advanced AI is down. For a full response, the GOOGLE_API_KEY needs to be configured. For now, what basic information can I provide?",
        "I'm operating in a limited mode. To unlock my full potential for answering any question, please check the GOOGLE_API_KEY configuration."
    ]
    
    return jsonify({
        'response': random.choice(default_responses),
        'timestamp': datetime.now().strftime('%H:%M:%S'),
        'source': 'simple-fallback'
    }), 200

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint to verify API status"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'gemini_available': GEMINI_AVAILABLE
    }), 200

if __name__ == '__main__':
    print("Available house plans:")
    for bhk_type, sizes in HOUSE_PLANS.items():
        print(f"{bhk_type}: {', '.join(sizes.keys())} sq ft")
    app.run(debug=True, port=5000)