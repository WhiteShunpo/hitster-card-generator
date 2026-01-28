import io
import time
import os
import random
import textwrap
import re
import qrcode
import requests
import numpy as np
import urllib.parse
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, ImageDraw, ImageFont
import matplotlib.colors as mcolors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import cm
db = None

# =============================================================================
# Correction of year fetching functions to use MusicBrainz and iTunes APIs
# =============================================================================
def get_year_from_musicbrainz(title, artist) -> int | None:
    q = f'recording:"{title}" AND artist:"{artist}"'
    params = {"query": q, "fmt": "json", "limit": 5}
    headers = {"User-Agent": "hitster-card-fix/1.0 (you@example.com)"}
    try:
        r = requests.get("https://musicbrainz.org/ws/2/recording", params=params, headers=headers, timeout=10)
        r.raise_for_status()
        result_json = r.json()
        years = []
        if not result_json or "recordings" not in result_json:
            return None
        for rec in result_json.get("recordings", []):
            # releases may be embedded
            for rel in rec.get("releases", []) or []:
                date = rel.get("date")
                if date:
                    years.append(int(date.split("-")[0]))
        if not years:
            return None
        return min(years)
    except Exception:
        return None

def get_year_from_itunes(title, artist) -> int | None:
    q = urllib.parse.quote(f"{artist} {title}")
    url = f"https://itunes.apple.com/search?term={q}&entity=song&limit=5"
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        result_json = r.json()
        if not result_json or "results" not in result_json:
            return None
        years = []
        for res in result_json.get("results", []):
            rd = res.get("releaseDate")
            if rd:
                years.append(int(rd.split("-")[0]))
        if not years:
            return None
        return min(years)
    except Exception:
        return None
    
def get_year_and_source(title, artist, orig_year) -> tuple[int | None, str | None]:
    """Get release year and source ('iTunes' or 'MusicBrainz') for a song."""
    itunes_year = get_year_from_itunes(title, artist)
    if itunes_year is not None:
        return itunes_year, 'iTunes'
    
    musicbrainz_year = get_year_from_musicbrainz(title, artist)
    if musicbrainz_year is not None:
        return musicbrainz_year, 'MusicBrainz'
    
    return orig_year, 'Spotify'

# =============================================================================
# NO-API SCRAPER FUNCTIONS (FALLBACK)
# =============================================================================

def fetch_no_api_data(links_file):
    """Scrapes metadata from public Spotify pages based on links.txt."""
    if not os.path.exists(links_file):
        return None
        
    print(f"Found {links_file}. Switching to No-API Scraper Mode...")
    with open(links_file, 'r') as f:
        urls = [line.strip() for line in f.readlines() if 'spotify.com/track/' in line]

    return fetch_no_api_data_from_list(urls)

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
        array of songs ('name', 'year', 'artist', 'link')
    """
    tracks = playlist_data['tracks']['items']

    songs = []

    for item in tracks:
        track = item['track']

        name = track['name']
        artist = track['artists'][0]['name']
        release_date = track['album']['release_date']
        spotify_year = int(release_date.split("-")[0])
        year = spotify_year # default
        
        # Try to get more accurate year from MusicBrainz or iTunes   
        year, year_source = get_year_and_source(name, artist, spotify_year)     

        song = {}
        song['name'] = sanitize_name(name)
        song['original_name'] = name
        song['original_year'] = spotify_year
        song['year'] = year
        song['year_source'] = year_source
        song['artist'] = artist
        song['link'] = track['external_urls']['spotify']
        song['album'] = track['album']['name']
        songs.append(song)
        
    return songs

def sanitize_name(name):
    """Remove remastered info from title: """
    sanitized = re.sub(r'\s?-\s?\d{4} remaster(ed)?', '', name, flags=re.IGNORECASE)
    sanitized = re.sub(r'\s?/\s?\d{4} remaster(ed)?', '', sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r'\s?\(\d{4} remaster(ed)?\)', '', sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r'\s?-\s?remaster(ed)?\s?\d{4}', '', sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r'\s?/\s?remaster(ed)?\s?\d{4}', '', sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r'\s?\(remaster(ed)?\s?\d{4}\)', '', sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r'\s?-\s?remaster(ed)?', '', sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r'\s?-\s?\d{4} version', '', sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r'\s?-\s?version \d{4}', '', sanitized, flags=re.IGNORECASE)
    sanitized = sanitized.strip()
    return sanitized


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
    img = create_qr_with_neon_rings_in_memory(qr_code)
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
    n_colors = len(db['color_gradient'])
    idx = percentile * (n_colors - 1)
    idx_low = int(np.floor(idx))
    idx_high = int(np.ceil(idx))
    
    if idx_low == idx_high:
        return mcolors.hex2color(db['color_gradient'][idx_low])
    
    # Linear interpolation
    color_low = mcolors.hex2color(db['color_gradient'][idx_low])
    color_high = mcolors.hex2color(db['color_gradient'][idx_high])
    frac = idx - idx_low
    
    r = color_low[0] + (color_high[0] - color_low[0]) * frac
    g = color_low[1] + (color_high[1] - color_low[1]) * frac
    b = color_low[2] + (color_high[2] - color_low[2]) * frac
    
    return (r, g, b)


def load_fonts():
    """Load Montserrat fonts with cross-platform fallback."""
    # Try Montserrat first
    try:
        font_year = ImageFont.truetype(db['fonts_dict']['year'], 380)
        font_artist = ImageFont.truetype(db['fonts_dict']['artist'], 110)
        font_song = ImageFont.truetype(db['fonts_dict']['song'], 100)
        font_label = ImageFont.truetype(db['fonts_dict']['artist'], 50)
        return font_year, font_artist, font_song, font_label
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
            font_label = ImageFont.truetype(regular_path, 50)
            return font_year, font_artist, font_song, font_label
        except:
            continue
    
    # Last resort
    print("Warning: Using default fonts (may not look optimal)")
    return ImageFont.load_default(), ImageFont.load_default(), ImageFont.load_default()


def create_solution_side(song_name, artist, year, all_years, output_path, card_label=None):
    """
    Create solution card with year-based color background.
    """
    img = create_solution_side_in_memory(song_name, artist, year, all_years, card_label=card_label)    
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
        
        # FRONT PAGE (QR codes)
        if db['ink_saving_mode']: # FIXME: should be always white?
            c.setFillColorRGB(1, 1, 1) # white
        else:
            c.setFillColorRGB(0, 0, 0) # black
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
# WEBUTILS
# =============================================================================

def create_qr_with_neon_rings_in_memory(qr_code):
    """
    Create QR code card with colorful neon rings background.
    """
    size = db['card_size']
    background_color = db['card_background_color']
    border_color = db['card_border_color']
    draw_border = db['card_draw_border']
    img = Image.new("RGB", (size, size), background_color)
    # draw border around the card for easier cutting
    if draw_border:
        border_draw = ImageDraw.Draw(img)
        border_width = 20
        border_color = border_color
        border_draw.rectangle(
            [(border_width, border_width), (size - border_width, size - border_width)],
            outline=border_color,
            width=border_width
        )

    draw = ImageDraw.Draw(img)
    
    # Draw neon rings
    center = size // 2
    max_radius = size // 2 - 50
    
    random.seed(42)  # Reproducible pattern
    for i, color in enumerate(db['neon_colors'] * 2):
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
                start=gap_start, end=gap_start + gap_length, fill=background_color, width=12
            )
    
    # Overlay QR code
    qr_size = int(size * 0.45)
    qr_code_rgb = qr_code.convert('RGB')
    qr_code_resized = qr_code_rgb.resize((qr_size, qr_size), Image.Resampling.LANCZOS)
    
    # Create a robust module mask (True where QR modules are present),
    # independent of whether modules are light or dark in the supplied image.
    qr_l = qr_code_resized.convert('L')
    arr = np.array(qr_l)
    # dark_mask = True where pixels are dark (<128)
    dark_mask = arr < 128
    num_dark = dark_mask.sum()
    total = arr.size
    # Modules usually occupy the minority of pixels; assume the smaller group corresponds to modules.
    if num_dark < total / 2:
        modules_mask = dark_mask
    else:
        modules_mask = ~dark_mask

    # Convert boolean mask to a PIL mask (255 = opaque)
    mask_img = Image.fromarray((modules_mask.astype('uint8') * 255)).convert('1')
    
    # Choose module color that contrasts with the background area where the QR will be placed.
    left = center - qr_size // 2
    top = center - qr_size // 2
    bg_crop = img.crop((left, top, left + qr_size, top + qr_size)).convert('L')
    bg_mean = np.array(bg_crop).mean()
    module_color = (0, 0, 0) if bg_mean > 127 else (255, 255, 255)
    
    # Create a solid overlay for modules and paste it using the mask so only module pixels are drawn.
    overlay = Image.new('RGB', (qr_size, qr_size), module_color)
    img.paste(overlay, (left, top), mask_img)
    
    return img

def create_solution_side_in_memory(song_name, artist, year, all_years, card_label=None):
    """
    Create solution card and return the PIL Image object directly.
    """
    size = db['card_size']
    margin = 150
    max_width = size - (2 * margin)
    
    # Get color for this year
    color_rgb = get_year_color(year, all_years)
    color_int = tuple(int(c * 255) for c in color_rgb)
    
    # Create the base image
    ink_saving_mode = db['ink_saving_mode']
    background_color = db['card_background_color'] if ink_saving_mode else color_int
    border_width = 100

    img = Image.new("RGB", (size, size), background_color)
    if ink_saving_mode:
        # draw border in the correct color only in ink saving mode
        draw = ImageDraw.Draw(img)
        draw.rectangle([(0, 0), (size - 1, size - 1)], outline=color_int, width=border_width)
    draw = ImageDraw.Draw(img)
    
    font_year, font_artist, font_song, font_label = load_fonts()
    
    def get_fitted_text_in_memory(text, font, max_width):
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
    song_text = get_fitted_text_in_memory(song_name, font_song, max_width)
    artist_text = get_fitted_text_in_memory(artist, font_artist, max_width)
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
        
    if card_label:
        # draw optional label in the bottom right corner centered in the border (if any)
        label_y = size - border_width // 2
        label_x = size - border_width // 2
        draw.text((label_x, label_y), card_label, fill="black", font=font_label, anchor="rm")
    
    # IMPORTANT: Return the PIL Image object instead of saving to a file
    return img


def fetch_no_api_data_from_list(urls, progress_bar=None):
    """
    Scrapes metadata from public Spotify pages based on a provided list of URLs.
    """

    songs = []
    total = len(urls)
    
    for i, url in enumerate(urls, 1):
        print(f"  [{i}/{len(urls)}] Scraping: {url}")
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            res = requests.get(url, headers=headers, timeout=5)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # Metadata from OpenGraph tags
            title = soup.find("meta", property="og:title")['content']
            desc = soup.find("meta", property="og:description")['content']
            artist = desc.split(" · ")[0]
            
            year, year_source = get_year_and_source(artist, title, -1000)
            song = {}
            song['name'] = sanitize_name(title)
            song['original_name'] = title
            song['original_year'] = 0
            song['year'] = year
            song['year_source'] = year_source
            song['artist'] = artist
            song['link'] = url
            
            songs.append(song)
            
            print(f"{year} | {artist} - {title}")
            time.sleep(0.5)
            # Update Progress
            if progress_bar:
                percent = (i + 1) / total
                progress_bar.progress(percent, text=f"Scraped {i+1}/{total}: {title[:30]}...")

        except Exception as e:
            print(f"Error: {e}")
            
    return songs

def create_pdf_in_memory(songs, valid_playlist_link_found, progress_bar=None):
    if not songs:
        return None

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Grid Settings (5x5 cm cards)
    card_size = 5 * cm
    cols, rows = 4, 5  # 20 cards per page
    margin_x = (width - (cols * card_size)) / 2
    margin_y = (height - (rows * card_size)) / 2

    total_cards = len(songs)
    years = [song['year'] for song in songs]

    card_label = db.get('label', None)

    for i in range(0, total_cards, 20):
        # Slice the data for this specific page
        batch_songs = list(songs[i:i+20])

        # --- PAGE 1: FRONT (QR CODES) ---
        # --- Inside create_pdf_in_memory ---
        for idx, song in enumerate(batch_songs):
            col = idx % cols
            row = (idx // cols) 
            x = margin_x + col * card_size
            y = height - margin_y - (row + 1) * card_size
            
            # STEP 1: Turn the URL string into a QR Image object
            song_link = song['link'] + '?context=spotify:playlist:' + valid_playlist_link_found[0].split('/playlist/')[1].split('?')[0] + '&' + valid_playlist_link_found[0].split('?')[1]
            base_qr = create_qr_code(song_link) 
            
            # STEP 2: Pass that IMAGE object to the neon rings function
            qr_pil = create_qr_with_neon_rings_in_memory(base_qr) 
            
            img_byte_arr = io.BytesIO()
            qr_pil.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)
            
            c.drawImage(ImageReader(img_byte_arr), x, y, width=card_size, height=card_size)
        
        c.showPage() # Finish the Front page

        # --- PAGE 2: BACK (SOLUTIONS - MIRRORED) ---
        for idx, song in enumerate(batch_songs):
            orig_col = idx % cols
            mirrored_col = (cols - 1) - orig_col # Flip horizontally for duplex
            row = (idx // cols)
            
            x = margin_x + mirrored_col * card_size
            y = height - margin_y - (row + 1) * card_size
            
            # 1. Generate Solution Image
            # IMPORTANT: Ensure your create_solution_side returns a PIL Image!
            sol_pil = create_solution_side_in_memory(song['name'], song['artist'], song['year'], years, card_label=card_label) 
            sol_byte_arr = io.BytesIO()
            sol_pil.save(sol_byte_arr, format='PNG')
            sol_byte_arr.seek(0)
            
            # 2. Draw to Canvas
            c.drawImage(ImageReader(sol_byte_arr), x, y, width=card_size, height=card_size)

        if progress_bar:
            processed = min(i + 20, total_cards)
            percent = processed / total_cards
            progress_bar.progress(percent, text=f"Generated {processed}/{total_cards} cards...")
        c.showPage() # Finish the Back page

    c.save()
    pdf_data = buffer.getvalue()
    buffer.close()
    return pdf_data

