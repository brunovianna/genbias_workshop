from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
from datetime import datetime
import random
import requests
import json
import base64
import io
import os
from PIL import Image

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Change this in production

# Automatic1111 API configuration
AUTOMATIC1111_URL = "https://027511349adb11cb59.gradio.live:7860"  # Adjust to your server's address

def get_available_models():
    """Fetch available models from Automatic1111 API"""
    try:
        response = requests.get(f"{AUTOMATIC1111_URL}/sdapi/v1/sd-models")
        if response.status_code == 200:
            models = response.json()
            return [model["title"] for model in models]
        return []
    except requests.exceptions.RequestException:
        return []

def generate_images(prompt, num_images, model, seed=None):
    """Generate images using Automatic1111 API"""
    payload = {
        "prompt": prompt,
        "negative_prompt": "",  # You can add negative prompt support if needed
        "steps": 20,  # Adjust these parameters as needed
        "cfg_scale": 7,
        "width": 512,
        "height": 512,
        "batch_size": num_images,
        "seed": seed if seed is not None else -1,
        "sampler_name": "Euler a",  # You can make this configurable
        "override_settings": {
            "sd_model_checkpoint": model
        }
    }

    try:
        response = requests.post(
            f"{AUTOMATIC1111_URL}/sdapi/v1/txt2img",
            json=payload
        )
        
        if response.status_code != 200:
            return None, None

        result = response.json()
        images = []
        
        # Create images directory if it doesn't exist
        os.makedirs('static/generated', exist_ok=True)
        
        # Save and get paths for all generated images
        for i, img_data in enumerate(result['images']):
            image = Image.open(io.BytesIO(base64.b64decode(img_data.split(",", 1)[0])))
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'generated_{timestamp}_{i}.png'
            filepath = os.path.join('static/generated', filename)
            image.save(filepath)
            images.append(filepath)

        # Get the seed that was actually used
        used_seed = result.get('seed', seed if seed is not None else random.randint(0, 2**32 - 1))
        
        return images, used_seed

    except requests.exceptions.RequestException as e:
        print(f"Error generating images: {e}")
        return None, None

def init_db():
    conn = sqlite3.connect('images.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS saved_comparisons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt1 TEXT NOT NULL,
            prompt2 TEXT NOT NULL,
            num_images INTEGER NOT NULL,
            seed INTEGER,
            model TEXT NOT NULL,
            notes TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            images_data TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

@app.route('/', methods=['GET'])
def index():
    available_models = get_available_models()
    if not available_models:
        flash('Warning: Could not connect to Automatic1111 API. Please check if the server is running.', 'error')
        available_models = []
    
    return render_template('index.html',
                         available_models=available_models,
                         image_counts=range(2, 7))

@app.route('/generate', methods=['POST'])
def generate():
    prompt1 = request.form['prompt1']
    prompt2 = request.form['prompt2']
    num_images = int(request.form['num_images'])
    model = request.form['model']
    use_last_seed = request.form.get('use_last_seed', 'new') == 'last'
    
    # Get seed from session or generate new one
    seed = int(request.form.get('last_seed')) if use_last_seed and request.form.get('last_seed') else random.randint(0, 2**32 - 1)
    
    # Generate first set of images
    images1, used_seed = generate_images(prompt1, num_images, model, seed)
    if not images1:
        flash('Error generating first set of images. Please check if Automatic1111 is running.', 'error')
        return redirect(url_for('index'))
    
    # Generate second set of images with the same seed
    images2, _ = generate_images(prompt2, num_images, model, used_seed)
    if not images2:
        flash('Error generating second set of images.', 'error')
        return redirect(url_for('index'))
    
    # Store the seed in session for potential reuse
    session['last_seed'] = used_seed
    
    generated_images = {
        'prompt1': images1,
        'prompt2': images2
    }
    
    return render_template('results.html',
                         images=generated_images,
                         prompt1=prompt1,
                         prompt2=prompt2,
                         seed=used_seed,
                         model=model)

@app.route('/save', methods=['POST'])
def save_comparison():
    conn = sqlite3.connect('images.db')
    c = conn.cursor()
    
    # Get the image paths and copy images to a permanent storage location
    images1 = request.form.getlist('images1[]')
    images2 = request.form.getlist('images2[]')
    
    # Create permanent storage directory if it doesn't exist
    os.makedirs('static/saved', exist_ok=True)
    
    # Copy and update paths
    saved_images = []
    for img_path in images1 + images2:
        if os.path.exists(img_path):
            new_filename = f'saved_{os.path.basename(img_path)}'
            new_path = os.path.join('static/saved', new_filename)
            try:
                Image.open(img_path).save(new_path)
                saved_images.append(new_path)
                # Optionally delete the original generated image
                os.remove(img_path)
            except Exception as e:
                print(f"Error saving image: {e}")
    
    c.execute('''
        INSERT INTO saved_comparisons 
        (prompt1, prompt2, num_images, seed, model, notes, images_data)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        request.form['prompt1'],
        request.form['prompt2'],
        len(request.form.getlist('images1[]')),
        request.form['seed'],
        request.form['model'],
        request.form['notes'],
        json.dumps(saved_images)
    ))
    
    conn.commit()
    conn.close()
    
    flash('Comparison saved successfully!')
    return redirect(url_for('index'))

@app.route('/gallery')
def gallery():
    conn = sqlite3.connect('images.db')
    c = conn.cursor()
    c.execute('SELECT * FROM saved_comparisons ORDER BY timestamp DESC')
    saved_comparisons = c.fetchall()
    conn.close()
    
    # Process the image paths from JSON string
    processed_comparisons = []
    for comparison in saved_comparisons:
        comparison_list = list(comparison)
        comparison_list[8] = json.loads(comparison_list[8])  # Convert JSON string to list
        processed_comparisons.append(comparison_list)
    
    return render_template('gallery.html', comparisons=processed_comparisons)

if __name__ == '__main__':
    init_db()
    app.run(
        host = '0.0.0.0', 
        port = 8000, 
        debug = True
    )   
