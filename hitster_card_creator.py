"""
Hitster Card Generator
Generate custom Hitster-style music game cards from Spotify playlists.
"""

import qrcode
import requests
import json
import numpy as np
from PIL import Image, ImageOps, ImageDraw, ImageFont
import matplotlib.colors as mcolors
import random
import textwrap
import os
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import cm
import re


# =============================================================================
# CONFIGURATION
# =============================================================================

# Color gradient for year-based card colors (oldest to newest)
COLOR_GRADIENT = [
    "#7030A0",  # Purple (oldest)
    "#E31C79",  # Pink
    "#FF6B9D",  # Light pink
    "#FFA500",  # Orange
    "#FFD700",  # Gold
    "#87CEEB",  # Sky blue
    "#4169E1",  # Royal blue (newest)
]

# Card design parameters
CARD_SIZE = 2000  # pixels
NEON_COLORS = [(255, 0, 100), (0, 200, 255), (255, 255, 0), (0, 255, 120)]

# Font paths (will fall back to system fonts if not found)
FONT_PATHS = {
    'year': "/home/USER/.fonts/Montserrat-Bold.ttf",
    'artist': "/home/USER/.fonts/Montserrat-SemiBold.ttf",
    'song': "/home/USER/.fonts/Montserrat-MediumItalic.ttf"
}


# =============================================================================
# SPOTIFY API FUNCTIONS
# =============================================================================

def fetch_spotify_playlist(playlist_url, client_id, client_secret):
    """
    Fetch all tracks from a Spotify playlist (handles pagination).
    
    Args:
        playlist_url: Full Spotify playlist URL
        client_id: Spotify API client ID
        client_secret: Spotify API client secret
        
    Returns:
        dict: Complete playlist data with all tracks
    """
    # Extract playlist ID
    playlist_id = playlist_url.split('/playlist/')[1].split('?')[0]
    
    # Get access token
    auth_response = requests.post('https://accounts.spotify.com/api/token', {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
    })
    access_token = auth_response.json()['access_token']
    
    # Fetch playlist data
    headers = {'Authorization': f'Bearer {access_token}'}
    response = requests.get(f'https://api.spotify.com/v1/playlists/{playlist_id}', 
                           headers=headers)
    playlist_data = response.json()
    
    print(f"Playlist: {playlist_data['name']}")
    print(f"Total tracks: {playlist_data['tracks']['total']}")
    
    # Handle pagination (Spotify returns max 100 tracks per request)
    all_tracks = playlist_data['tracks']['items']
    next_url = playlist_data['tracks']['next']
    
    while next_url:
        print(f"Fetching more tracks... (currently have {len(all_tracks)})")
        response = requests.get(next_url, headers=headers)
        data = response.json()
        all_tracks.extend(data['items'])
        next_url = data.get('next')
    
    playlist_data['tracks']['items'] = all_tracks
    print(f"✓ Fetched all {len(all_tracks)} tracks!")
    
    return playlist_data


def parse_playlist_data(playlist_data):
    """
    Extract song information from playlist data.
    
    Returns:
        tuple: (song_names, release_years, artists, links)
    """
    tracks = playlist_data['tracks']['items']
    
    song_names = [item['track']['name'] for item in tracks]
    release_dates = [item['track']['album']['release_date'] for item in tracks]
    artists = [item['track']['artists'][0]['name'] for item in tracks]
    links = [item['track']['external_urls']['spotify'] for item in tracks]
    
    # Extract only year from release date
    release_years = [int(date.split("-")[0]) for date in release_dates]
    
    return song_names, release_years, artists, links


# =============================================================================
# CARD GENERATION FUNCTIONS
# =============================================================================

def create_qr_code(song_link):
    """Generate inverted QR code (white on black)."""
    qr = qrcode.QRCode(version=1, box_size=10, border=0)
    qr.add_data(song_link)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    return ImageOps.invert(img)


def create_qr_with_neon_rings(qr_code, output_path):
    """
    Create QR code card with colorful neon rings background.
    """
    size = CARD_SIZE
    img = Image.new("RGB", (size, size), "black")
    draw = ImageDraw.Draw(img)
    
    # Draw neon rings
    center = size // 2
    max_radius = size // 2 - 50
    
    random.seed(42)  # Reproducible pattern
    for i, color in enumerate(NEON_COLORS * 2):
        radius = max_radius - i * 50
        if radius <= 0:
            break
        
        # Draw arc with random gaps
        num_gaps = random.randint(1, 3)
        for gap in range(num_gaps):
            gap_start = random.randint(0, 360)
            gap_length = random.randint(20, 60)
            
            draw.arc(
                (center - radius, center - radius, center + radius, center + radius),
                start=0, end=360, fill=color, width=12
            )
            draw.arc(
                (center - radius, center - radius, center + radius, center + radius),
                start=gap_start, end=gap_start + gap_length, fill="black", width=12
            )
    
    # Overlay QR code
    qr_size = int(size * 0.45)
    qr_code_rgb = qr_code.convert('RGB')
    qr_code_resized = qr_code_rgb.resize((qr_size, qr_size), Image.Resampling.LANCZOS)
    
    # Create transparency mask (white = opaque, black = transparent)
    qr_array = qr_code_resized.convert('L')
    mask = qr_array.point(lambda x: 255 if x > 128 else 0, mode='1')
    
    img.paste(qr_code_resized, (center - qr_size // 2, center - qr_size // 2), mask)
    img.save(output_path)
    return output_path


def get_year_color(year, all_years):
    """
    Get color for a year based on its percentile in the distribution.
    """
    sorted_years = sorted(all_years)
    
    # Calculate percentile position
    count_below = sum(1 for y in sorted_years if y < year)
    count_equal = sum(1 for y in sorted_years if y == year)
    percentile = (count_below + count_equal / 2) / len(sorted_years)
    
    # Map to color gradient
    n_colors = len(COLOR_GRADIENT)
    idx = percentile * (n_colors - 1)
    idx_low = int(np.floor(idx))
    idx_high = int(np.ceil(idx))
    
    if idx_low == idx_high:
        return mcolors.hex2color(COLOR_GRADIENT[idx_low])
    
    # Linear interpolation
    color_low = mcolors.hex2color(COLOR_GRADIENT[idx_low])
    color_high = mcolors.hex2color(COLOR_GRADIENT[idx_high])
    frac = idx - idx_low
    
    r = color_low[0] + (color_high[0] - color_low[0]) * frac
    g = color_low[1] + (color_high[1] - color_low[1]) * frac
    b = color_low[2] + (color_high[2] - color_low[2]) * frac
    
    return (r, g, b)


def load_fonts():
    """Load Montserrat fonts with cross-platform fallback."""
    # Try Montserrat first
    try:
        font_year = ImageFont.truetype(FONT_PATHS['year'], 380)
        font_artist = ImageFont.truetype(FONT_PATHS['artist'], 110)
        font_song = ImageFont.truetype(FONT_PATHS['song'], 100)
        return font_year, font_artist, font_song
    except:
        pass
    
    # Try common system fonts (cross-platform)
    fallback_fonts = [
        # Linux
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
        # macOS
        ("/System/Library/Fonts/Helvetica.ttc",
         "/System/Library/Fonts/Helvetica.ttc",
         "/System/Library/Fonts/Helvetica.ttc"),
        # Windows
        ("C:\\Windows\\Fonts\\arialbd.ttf",
         "C:\\Windows\\Fonts\\arial.ttf",
         "C:\\Windows\\Fonts\\ariali.ttf"),
    ]
    
    for bold_path, regular_path, italic_path in fallback_fonts:
        try:
            font_year = ImageFont.truetype(bold_path, 300)
            font_artist = ImageFont.truetype(regular_path, 140)
            font_song = ImageFont.truetype(italic_path, 140)
            return font_year, font_artist, font_song
        except:
            continue
    
    # Last resort
    print("Warning: Using default fonts (may not look optimal)")
    return ImageFont.load_default(), ImageFont.load_default(), ImageFont.load_default()


def create_solution_side(song_name, artist, year, all_years, output_path):
    """
    Create solution card with year-based color background.
    """
    size = CARD_SIZE
    margin = 150
    max_width = size - (2 * margin)
    
    # Get color for this year
    color_rgb = get_year_color(year, all_years)
    color_int = tuple(int(c * 255) for c in color_rgb)
    
    img = Image.new("RGB", (size, size), color_int)
    draw = ImageDraw.Draw(img)
    
    font_year, font_artist, font_song = load_fonts()
    
    def get_fitted_text(text, font, max_width):
        """Wrap text to fit within max_width."""
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        
        if text_width <= max_width:
            return text
        
        avg_char_width = text_width / len(text)
        chars_per_line = int(max_width / avg_char_width * 0.85)
        wrapped = '\n'.join(textwrap.wrap(text, width=max(chars_per_line, 10)))
        
        return wrapped
    
    # Prepare text
    song_text = get_fitted_text(song_name, font_song, max_width)
    artist_text = get_fitted_text(artist, font_artist, max_width)
    year_text = str(year)
    
    # Draw centered text
    gap = 400
    center_x = size / 2
    center_y = size / 2
    
    draw.text((center_x, center_y), year_text, fill="black", 
             font=font_year, anchor="mm")
    
    artist_y = center_y - gap
    if '\n' in artist_text:
        draw.multiline_text((center_x, artist_y), artist_text, fill="black",
                          font=font_artist, align="center", anchor="mm")
    else:
        draw.text((center_x, artist_y), artist_text, fill="black",
                 font=font_artist, anchor="mm")
    
    song_y = center_y + gap
    if '\n' in song_text:
        draw.multiline_text((center_x, song_y), song_text, fill="black",
                          font=font_song, align="center", anchor="mm")
    else:
        draw.text((center_x, song_y), song_text, fill="black",
                 font=font_song, anchor="mm")
    
    img.save(output_path)
    return output_path


# =============================================================================
# PDF GENERATION
# =============================================================================

def create_cards_pdf(cards_folder, output_pdf_path):
    """
    Create print-ready PDF with alternating front/back pages.
    4x5 grid (20 cards per page), 5cm x 5cm cards, ready for duplex printing.
    """
    c = canvas.Canvas(output_pdf_path, pagesize=A4)
    width, height = A4
    
    # Card configuration
    card_size = 5 * cm
    gap_size = 0.2 * cm
    cards_per_row = 4
    cards_per_col = 5
    cards_per_page = cards_per_row * cards_per_col
    
    # Calculate layout
    total_width = cards_per_row * card_size + (cards_per_row - 1) * gap_size
    total_height = cards_per_col * card_size + (cards_per_col - 1) * gap_size
    margin_x = (width - total_width) / 2
    margin_y = (height - total_height) / 2
    
    # Get sorted card files
    qr_images = sorted([f for f in os.listdir(cards_folder) if f.endswith('_qr.png')],
                      key=lambda x: int(re.search(r'(\d+)', x).group()))
    solution_images = sorted([f for f in os.listdir(cards_folder) if f.endswith('_solution.png')],
                            key=lambda x: int(re.search(r'(\d+)', x).group()))
    
    total_pages = (len(qr_images) + cards_per_page - 1) // cards_per_page
    
    # Create alternating front/back pages
    for page_idx in range(total_pages):
        start_card = page_idx * cards_per_page
        end_card = min(start_card + cards_per_page, len(qr_images))
        
        # FRONT PAGE (QR codes) - black background
        c.setFillColorRGB(0, 0, 0)
        c.rect(0, 0, width, height, stroke=0, fill=1)
        
        for card_idx in range(start_card, end_card):
            idx = card_idx - start_card
            row = idx // cards_per_row
            col = idx % cards_per_row
            
            x = margin_x + col * (card_size + gap_size)
            y = height - margin_y - (row + 1) * card_size - row * gap_size
            
            qr_path = os.path.join(cards_folder, qr_images[card_idx])
            c.drawImage(ImageReader(qr_path), x, y, 
                       width=card_size, height=card_size, preserveAspectRatio=True)
        
        c.showPage()
        
        # BACK PAGE (Solutions) - white background, mirrored
        c.setFillColorRGB(1, 1, 1)
        c.rect(0, 0, width, height, stroke=0, fill=1)
        
        for card_idx in range(start_card, end_card):
            idx = card_idx - start_card
            row = idx // cards_per_row
            col = idx % cards_per_row
            col_mirrored = cards_per_row - 1 - col  # Mirror for duplex
            
            x = margin_x + col_mirrored * (card_size + gap_size)
            y = height - margin_y - (row + 1) * card_size - row * gap_size
            
            sol_path = os.path.join(cards_folder, solution_images[card_idx])
            c.drawImage(ImageReader(sol_path), x, y,
                       width=card_size, height=card_size, preserveAspectRatio=True)
        
        c.showPage()
    
    c.save()
    print(f"\n✓ Created PDF: {output_pdf_path}")
    print(f"  - {len(qr_images)} cards total")
    print(f"  - {total_pages * 2} pages (alternating front/back)")
    print(f"  - Ready for duplex printing!")
    return output_pdf_path


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def generate_hitster_cards(playlist_url, client_id, client_secret, output_dir="hitster_cards"):
    """
    Complete pipeline: Fetch playlist → Generate cards → Create PDF
    """
    print("=== Hitster Card Generator ===\n")
    
    # Create output directory first so we can check/save the JSON file
    os.makedirs(output_dir, exist_ok=True)
    json_file = os.path.join(output_dir, "songs.json")

    # --- NEW LOGIC START ---
    # If a local file exists, load it (allows you to fix years manually)
    if os.path.exists(json_file):
        print(f"Step 1: Loading local data from {json_file}...")
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Unpack the list of dictionaries back into separate lists
            song_names = [d['name'] for d in data]
            release_years = [d['year'] for d in data]
            artists = [d['artist'] for d in data]
            links = [d['link'] for d in data]
            
    else:
        # Otherwise, fetch from Spotify as usual
        print("Step 1: Fetching playlist from Spotify...")
        playlist_data = fetch_spotify_playlist(playlist_url, client_id, client_secret)
        
        print("\nStep 2: Parsing song data...")
        song_names, release_years, artists, links = parse_playlist_data(playlist_data)
        
        # Save to JSON so you can edit it later
        with open(json_file, 'w', encoding='utf-8') as f:
            data = [
                {'name': n, 'year': y, 'artist': a, 'link': l}
                for n, y, a, l in zip(song_names, release_years, artists, links)
            ]
            json.dump(data, f, indent=2)
        print(f"✓ Saved data to {json_file} (Edit this file to fix incorrect years!)")
    # --- NEW LOGIC END ---

    print(f"✓ Processing {len(song_names)} songs")
    
    # 3. Generate cards (Existing logic)
    print(f"\nStep 3: Generating cards...")
    for i, (link, name, artist, year) in enumerate(zip(links, song_names, artists, release_years)):
        if (i + 1) % 20 == 0:
            print(f"  Progress: {i+1}/{len(song_names)} cards...")
        
        qr_code = create_qr_code(link)
        qr_path = f"{output_dir}/card_{i+1:03d}_qr.png"
        sol_path = f"{output_dir}/card_{i+1:03d}_solution.png"
        
        create_qr_with_neon_rings(qr_code, qr_path)
        create_solution_side(name, artist, year, release_years, sol_path)
    
    # 4. Create PDF (Existing logic)
    print("\nStep 4: Creating print-ready PDF...")
    pdf_path = f"{output_dir}.pdf"
    create_cards_pdf(output_dir, pdf_path)
    
    print(f"\nDone! Your Hitster cards are ready:")
    print(f"   Cards: {output_dir}/")
    print(f"   PDF: {pdf_path}")


if __name__ == "__main__":
    # Example usage - replace with your credentials and playlist
    PLAYLIST_URL = "your_spotify_playlist_url_here"
    CLIENT_ID = "your_spotify_client_id_here"
    CLIENT_SECRET = "your_spotify_client_secret_here"
    
    generate_hitster_cards(PLAYLIST_URL, CLIENT_ID, CLIENT_SECRET)