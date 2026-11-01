import io
import gc
import time
import os
import random
import textwrap
import re
import qrcode
import requests
import numpy as np
import urllib.parse
from datetime import datetime
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import cm
db = None
_font_cache = None


def to_rgba(color):
    """Convert a color to (r, g, b, a) floats in 0..1.

    Replaces matplotlib.colors.to_rgba for our use (hex strings from color
    pickers, plus the occasional RGB(A) tuple), so the app doesn't pull in
    the heavy matplotlib dependency just for color parsing. For "#RRGGBB"
    input the result is byte-identical to matplotlib's (both do int/255).
    """
    if isinstance(color, (tuple, list)):
        vals = [float(v) for v in color]
        if any(v > 1 for v in vals):  # looks like 0..255
            vals = [v / 255.0 for v in vals]
        if len(vals) == 3:
            vals.append(1.0)
        return tuple(vals[:4])

    s = str(color).strip().lstrip('#')
    try:
        if len(s) in (3, 4):  # shorthand: expand each nibble
            s = ''.join(ch * 2 for ch in s)
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
        a = int(s[6:8], 16) / 255.0 if len(s) >= 8 else 1.0
        return (r, g, b, a)
    except (ValueError, IndexError):
        return (0.0, 0.0, 0.0, 1.0)  # safe fallback, matches old except branches


def hex2color(color):
    """RGB-only variant of to_rgba (replaces matplotlib.colors.hex2color)."""
    return to_rgba(color)[:3]

# =============================================================================
# DEFAULT DESIGN SETTINGS
# =============================================================================
DEFAULT_DESIGN_SETTINGS = {
    "card_size": 2000,
    "ink_saving_mode": False,
    "card_draw_border": False,
    "card_border_color": (255, 255, 255),
    "neon_colors": [(255, 0, 100), (0, 200, 255), (0, 255, 120), (255, 255, 0)],

    "fonts_dict": {
        "year": os.path.join("fonts", "Montserrat-Bold.ttf"),
        "artist": os.path.join("fonts", "Montserrat-SemiBold.ttf"),
        "song": os.path.join("fonts", "Montserrat-MediumItalic.ttf"),
    },
    "color_gradient": [
        "#7030A0", "#E31C79", "#FF6B9D", "#FFA500",
        "#FFD700", "#87CEEB", "#4169E1",
    ],

    "google_font": "Montserrat",

    # QR Side Settings
    "qr_bg_type": "neon_rings", # "solid", "neon_rings", "image"
    "qr_bg_color": (0, 0, 0),
    "qr_bg_image": None, # PIL Image object
    "qr_bg_scale": 1.0,
    "qr_bg_offset_x": 0.0,
    "qr_bg_offset_y": 0.0,
    
    "qr_background_mode": "transparent", # "transparent" or "solid"
    "qr_background_color": (0, 0, 0), # solid backplate color
    "qr_module_color": (255, 255, 255),
    "qr_quiet_zone": 2, 
    "qr_backplate_padding": 40,
    "qr_backplate_radius": 20,
    "qr_size_ratio": 0.45,
    
    "neon_ring_opacity": 1.0,
    "neon_ring_thickness": 12,
    "neon_ring_count": 8,
    
    "qr_title": "",
    "qr_title_enabled": False,
    "qr_title_pos": "top", # "top", "bottom", "center_above_qr", "center_below_qr"
    "qr_title_size": 80,
    "qr_title_color": (255, 255, 255),
    "qr_title_bg": False,

    # Solution Side Settings
    "sol_bg_type": "gradient", # "gradient", "image"
    "sol_bg_image": None,
    "sol_bg_scale": 1.0,
    "sol_bg_offset_x": 0.0,
    "sol_bg_offset_y": 0.0,

    "sol_title": "",
    "sol_title_enabled": False,
    "sol_title_pos": "top", # "top", "bottom"
    "sol_title_size": 80,
    "sol_title_color": (0, 0, 0),
    "sol_title_bg": False,
}

def get_settings(override=None):
    """Get settings merged with defaults."""
    settings = DEFAULT_DESIGN_SETTINGS.copy()
    if db:
        settings.update(db)
    if override:
        settings.update(override)
    
    # Ensure colors are tuples if they came as strings
    for key in ["card_border_color", "qr_bg_color", "qr_background_color", "qr_module_color", "qr_title_color", "sol_title_color"]:
        if isinstance(settings.get(key), str):
            try:
                settings[key] = tuple(int(c * 255) for c in to_rgba(settings[key]))
            except:
                pass
    return settings

# =============================================================================
# YEAR VALIDATION
# =============================================================================
MIN_VALID_YEAR = 1500
MAX_VALID_YEAR = datetime.now().year + 1

def _validate_year(year: int | None) -> int | None:
    """Return year only if it falls in a plausible range, else None."""
    if year is None:
        return None
    if MIN_VALID_YEAR <= year <= MAX_VALID_YEAR:
        return year
    return None

# =============================================================================
# Year fetching functions using MusicBrainz and iTunes APIs
# =============================================================================
def get_year_from_musicbrainz(title, artist) -> int | None:
    q = f'recording:"{title}" AND artist:"{artist}"'
    params = {"query": q, "fmt": "json", "limit": 5}
    headers = {"User-Agent": "hitster-card-generator/2.0 (https://github.com/WhiteShunpo/hitster-cards-generator)"}
    try:
        r = requests.get("https://musicbrainz.org/ws/2/recording", params=params, headers=headers, timeout=10)
        if r.status_code in (429, 503):
            time.sleep(2)
            r = requests.get("https://musicbrainz.org/ws/2/recording", params=params, headers=headers, timeout=10)
        r.raise_for_status()
        result_json = r.json()
        years = []
        if not result_json or "recordings" not in result_json:
            return None
        for rec in result_json.get("recordings", []):
            for rel in rec.get("releases", []) or []:
                date = rel.get("date")
                if date:
                    y = _validate_year(int(date.split("-")[0]))
                    if y is not None:
                        years.append(y)
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
                y = _validate_year(int(rd.split("-")[0]))
                if y is not None:
                    years.append(y)
        if not years:
            return None
        return min(years)
    except Exception:
        return None
    
def get_year_and_source(title, artist, orig_year) -> tuple[int | None, str | None]:
    """Get release year and source ('iTunes' or 'MusicBrainz') for a song.
    
    Args:
        title: Song title (first!)
        artist: Artist name (second!)
        orig_year: Fallback year from Spotify
    """
    itunes_year = get_year_from_itunes(title, artist)
    if itunes_year is not None:
        return itunes_year, 'iTunes'
    
    # Rate-limit: MusicBrainz enforces ~1 req/sec
    time.sleep(1.1)
    musicbrainz_year = get_year_from_musicbrainz(title, artist)
    if musicbrainz_year is not None:
        return musicbrainz_year, 'MusicBrainz'
    
    validated = _validate_year(orig_year)
    if validated is not None:
        return validated, 'Spotify'
    return None, None

# =============================================================================
# NAME SANITIZATION
# =============================================================================
def sanitize_name(name):
    """Remove common version/edition suffixes from song title.

    Strips remaster, live, acoustic, radio edit, feat., extended mix, etc.
    Both parenthetical forms (Live) / [Live] and dash forms - Live are handled.
    """
    # Keyword groups for inside parentheses/brackets — feat. may contain any char except the closing bracket
    _PAREN = (
        r'(?:\d{4}\s*)?remaster(?:ed)?(?:\s*\d{4})?'
        r'|live(?:\s+[^\)\]]*)?'
        r'|acoustic(?:\s+version)?'
        r'|radio\s+edit'
        r'|(?:original|extended|club|deluxe)\s+(?:mix|version|edit)'
        r'|(?:single|album)\s+version'
        r'|bonus\s+track'
        r'|(?:mono|stereo)'
        r'|(?:feat|ft|featuring)\.?\s+[^\)\]]+'
    )
    # Keyword groups after a dash/slash — feat. and live may consume the rest of the title
    _DASH = (
        r'(?:\d{4}\s*)?remaster(?:ed)?(?:\s*\d{4})?'
        r'|(?:\d{4}\s*)?version(?:\s*\d{4})?'
        r'|live(?:\s+.+)?'
        r'|acoustic(?:\s+version)?'
        r'|radio\s+edit'
        r'|(?:original|extended|club|deluxe)\s+(?:mix|version|edit)'
        r'|(?:single|album)\s+version'
        r'|bonus\s+track'
        r'|(?:mono|stereo)'
        r'|(?:feat|ft|featuring)\.?\s+.+'
    )
    pattern = rf'\s*[\(\[](?:{_PAREN})[\)\]]|\s*[-/]\s*(?:{_DASH})'
    return re.sub(pattern, '', name, flags=re.IGNORECASE).strip()


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
    
    headers = {'Authorization': f'Bearer {access_token}'}

    # Fetch playlist metadata (name only — the embedded `tracks` object was
    # removed from this endpoint by Spotify, so we paginate via /tracks below)
    meta_response = requests.get(
        f'https://api.spotify.com/v1/playlists/{playlist_id}?fields=name',
        headers=headers,
    )
    meta = meta_response.json()
    playlist_name = meta.get('name', 'Unknown')
    print(f"Playlist: {playlist_name}")

    # Fetch tracks via the dedicated tracks endpoint (paginated, max 100/page)
    tracks_response = requests.get(
        f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks',
        headers=headers,
    )
    tracks_page = tracks_response.json()
    all_tracks = tracks_page.get('items', [])
    next_url = tracks_page.get('next')

    while next_url:
        print(f"Fetching more tracks... (currently have {len(all_tracks)})")
        response = requests.get(next_url, headers=headers)
        data = response.json()
        all_tracks.extend(data.get('items', []))
        next_url = data.get('next')

    print(f"✓ Fetched all {len(all_tracks)} tracks!")

    # Preserve the shape downstream (parse_playlist_data) expects
    return {
        'name': playlist_name,
        'tracks': {
            'items': all_tracks,
            'total': tracks_page.get('total', len(all_tracks)),
        },
    }


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

    no_year = [s for s in songs if s['year'] is None]
    if no_year:
        print(f"\n⚠ {len(no_year)} song(s) have no year — edit songs.json manually before re-running:")
        for s in no_year:
            print(f"  - {s['artist']} — {s['original_name']}")

    return songs


_SPOTIFY_HOSTS = frozenset({"open.spotify.com"})


def _is_spotify_url(url: str) -> bool:
    """True only for https URLs on open.spotify.com.

    The no-API scrape paths fetch a pasted URL server-side, so the host
    must be pinned: without this, any pasted link containing '/track/'
    or '/playlist/' would be fetched, including internal hosts.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname in _SPOTIFY_HOSTS


# =============================================================================
# SPOTIFY SCRAPER — extract track links from a public playlist page
# =============================================================================

def scrape_playlist_track_links(playlist_url) -> list[str]:
    """
    Scrape individual track URLs from a public Spotify playlist page.
    Returns a list of 'https://open.spotify.com/track/...' URLs.
    """
    if not _is_spotify_url(playlist_url):
        print("Refusing to scrape a non-Spotify playlist URL.")
        return []
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(playlist_url, headers=headers, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        track_links = []
        # Spotify embeds track links in <meta> and <a> tags on the public page
        for tag in soup.find_all("meta"):
            content = tag.get("content", "")
            if "open.spotify.com/track/" in content:
                # Extract just the track URL (strip query params)
                url = content.split("?")[0]
                if url not in track_links:
                    track_links.append(url)

        # Also look in <a> href attributes
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if "/track/" in href:
                if href.startswith("/"):
                    href = "https://open.spotify.com" + href
                url = href.split("?")[0]
                if url not in track_links:
                    track_links.append(url)

        return track_links
    except Exception as e:
        print(f"Error scraping playlist: {e}")
        return []


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


def get_year_color(year, all_years, settings=None):
    """
    Get color for a year based on its percentile in the distribution.
    """
    if settings is None:
        settings = get_settings()
    gradient = settings.get('color_gradient', [])

    sorted_years = sorted(all_years)

    # Calculate percentile position
    count_below = sum(1 for y in sorted_years if y < year)
    count_equal = sum(1 for y in sorted_years if y == year)
    percentile = (count_below + count_equal / 2) / len(sorted_years)

    n_colors = len(gradient)
    if n_colors == 0:
        return (0.0, 0.0, 0.0) # Fallback to black if no colors
    if n_colors == 1:
        return to_rgba(gradient[0])[:3]

    idx = percentile * (n_colors - 1)
    idx_low = int(np.floor(idx))
    idx_high = int(np.ceil(idx))

    if idx_low == idx_high:
        return to_rgba(gradient[idx_low])[:3]

    # Linear interpolation
    color_low = to_rgba(gradient[idx_low])
    color_high = to_rgba(gradient[idx_high])
    frac = idx - idx_low
    
    r = color_low[0] + (color_high[0] - color_low[0]) * frac
    g = color_low[1] + (color_high[1] - color_low[1]) * frac
    b = color_low[2] + (color_high[2] - color_low[2]) * frac
    
    return (r, g, b)


_google_font_cache = {}

def get_google_font(family_name, size, fallback_font):
    """Downloads a Google Font and caches it, or returns fallback."""
    if not family_name:
        return fallback_font
    
    font_id = family_name.lower().replace(" ", "-")
    cache_key = (font_id, size)
    
    if cache_key in _google_font_cache:
        return _google_font_cache[cache_key]
        
    font_bytes = _google_font_cache.get(font_id)
    if not font_bytes:
        try:
            api_url = f"https://gwfh.mranftl.com/api/fonts/{font_id}"
            r = requests.get(api_url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                ttf_url = None
                
                # Prefer bold (700) or bold-italic if available, otherwise fallback
                for variant in data.get('variants', []):
                    if variant.get('id') == '700' and variant.get('ttf'):
                        ttf_url = variant['ttf']
                        break
                
                if not ttf_url:
                    for variant in data.get('variants', []):
                        if variant.get('ttf'):
                            ttf_url = variant['ttf']
                            break
                            
                if ttf_url:
                    r_ttf = requests.get(ttf_url, timeout=5)
                    if r_ttf.status_code == 200:
                        font_bytes = r_ttf.content
                        _google_font_cache[font_id] = font_bytes
        except Exception as e:
            print(f"Error downloading font: {e}")
            pass
            
    if font_bytes:
        try:
            font = ImageFont.truetype(io.BytesIO(font_bytes), size)
            _google_font_cache[cache_key] = font
            return font
        except:
            pass
            
    return fallback_font

def get_font_for_setting(settings, size):
    """Get the preferred font (Google Font or fallback) for a given size."""
    try:
        fallback = ImageFont.truetype(settings['fonts_dict']['artist'], size)
    except:
        fallback = ImageFont.load_default()
        
    return get_google_font(settings.get('google_font', 'Montserrat'), size, fallback)


def create_solution_side(song_name, artist, year, all_years, output_path):
    """
    Create solution card with year-based color background.
    """
    img = create_solution_side_in_memory(song_name, artist, year, all_years)    
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
    settings = get_settings()
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
        bg_color = settings.get('card_background_color', (0, 0, 0))
        if isinstance(bg_color, str):
            try:
                bg_color = hex2color(bg_color)
            except:
                bg_color = (0, 0, 0)
        c.setFillColorRGB(bg_color[0], bg_color[1], bg_color[2])
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

def apply_background_image(img, bg_img, scale, offset_x, offset_y, card_size):
    """Applies scaled and offset background image to card."""
    aspect = bg_img.width / bg_img.height
    
    if aspect > 1:
        base_h = card_size
        base_w = int(card_size * aspect)
    else:
        base_w = card_size
        base_h = int(card_size / aspect)
        
    new_w = int(base_w * scale)
    new_h = int(base_h * scale)
    
    resized = bg_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    
    x = (card_size - new_w) // 2 + int(offset_x * card_size)
    y = (card_size - new_h) // 2 + int(offset_y * card_size)
    
    if resized.mode in ('RGBA', 'LA') or (resized.mode == 'P' and 'transparency' in resized.info):
        resized = resized.convert("RGBA")
        img.paste(resized, (x, y), resized)
    else:
        img.paste(resized, (x, y))


def render_card_background(img, settings, side="qr", seed=42):
    """Render the card background (solid, neon rings, or image)."""
    size = settings['card_size']
    
    if side == "qr":
        bg_type = settings['qr_bg_type']
        bg_color = settings['qr_bg_color']
        bg_img = settings.get('qr_bg_image')
        scale = settings['qr_bg_scale']
        offset_x = settings['qr_bg_offset_x']
        offset_y = settings['qr_bg_offset_y']
    else:
        bg_type = settings['sol_bg_type']
        # Solution side uses its dynamic year color if type is "gradient", else maybe image over it
        bg_img = settings.get('sol_bg_image')
        scale = settings['sol_bg_scale']
        offset_x = settings['sol_bg_offset_x']
        offset_y = settings['sol_bg_offset_y']
        bg_color = (0, 0, 0) # Fallback

    draw = ImageDraw.Draw(img)
    
    # Fill the entire background with the selected color first
    if side == "qr":
        draw.rectangle([(0, 0), (size, size)], fill=bg_color)
    
    if bg_type == "image" and bg_img:
        apply_background_image(img, bg_img, scale, offset_x, offset_y, size)
    elif bg_type == "neon_rings" and side == "qr":
        # Draw neon rings — unique pattern per card
        center = size // 2
        max_radius = size // 2 - 50
        
        qr_size = int(size * settings['qr_size_ratio'])
        safety_radius = (qr_size // 2) + settings['qr_backplate_padding'] + 20
        
        random.seed(seed)
        neon_colors = settings['neon_colors']
        ring_count = settings.get('neon_ring_count', 8)
        thickness = settings.get('neon_ring_thickness', 12)
        
        for i in range(ring_count):
            color = neon_colors[i % len(neon_colors)]
            radius = max_radius - i * (max_radius // ring_count)
            if radius <= 0:
                break
            
            is_inside_safety = radius < safety_radius
            
            if settings['qr_background_mode'] == "solid" and is_inside_safety:
                continue
                
            num_gaps = random.randint(1, 3)
            for gap in range(num_gaps):
                gap_start = random.randint(0, 360)
                gap_length = random.randint(20, 60)
                
                draw.arc(
                    (center - radius, center - radius, center + radius, center + radius),
                    start=0, end=360, fill=color, width=thickness
                )
                draw.arc(
                    (center - radius, center - radius, center + radius, center + radius),
                    start=gap_start, end=gap_start + gap_length, fill=bg_color, width=thickness
                )

    # Draw border
    if settings.get('card_draw_border'):
        border_width = 20
        draw.rectangle(
            [(border_width, border_width), (size - border_width, size - border_width)],
            outline=settings['card_border_color'],
            width=border_width
        )

def render_qr_backplate(img, settings):
    """Render a solid backplate for the QR code if configured."""
    if settings['qr_background_mode'] != "solid":
        return
        
    size = settings['card_size']
    qr_size = int(size * settings['qr_size_ratio'])
    padding = settings['qr_backplate_padding']
    radius = settings['qr_backplate_radius']
    bg_color = settings['qr_background_color']
    
    center = size // 2
    side = qr_size + 2 * padding
    left = center - side // 2
    top = center - side // 2
    right = left + side
    bottom = top + side
    
    overlay = Image.new("RGBA", (size, size), (0,0,0,0))
    overlay_draw = ImageDraw.Draw(overlay)
    
    if radius > 0:
        overlay_draw.rounded_rectangle([left, top, right, bottom], radius=radius, fill=bg_color)
    else:
        overlay_draw.rectangle([left, top, right, bottom], fill=bg_color)
        
    img.paste(overlay, (0, 0), overlay)

def render_qr_code(img, qr_code, settings):
    """Render the QR code modules on top of the card."""
    size = settings['card_size']
    center = size // 2
    qr_size_base = int(size * settings['qr_size_ratio'])
    quiet_zone = settings.get('qr_quiet_zone', 2)
    
    qr_code_rgb = qr_code.convert('RGB')
    qr_code_resized = qr_code_rgb.resize((qr_size_base, qr_size_base), Image.Resampling.LANCZOS)
    
    qr_l = qr_code_resized.convert('L')
    arr = np.array(qr_l)
    modules_mask = arr > 128
    mask_img = Image.fromarray((modules_mask.astype('uint8') * 255)).convert('1')
    
    left = center - qr_size_base // 2
    top = center - qr_size_base // 2
    
    module_color = settings['qr_module_color']
    
    if settings['qr_background_mode'] == "transparent":
        bg_crop = img.crop((left, top, left + qr_size_base, top + qr_size_base)).convert('L')
        bg_mean = np.array(bg_crop).mean()
        if module_color == (255, 255, 255) or module_color == (0, 0, 0):
             module_color = (0, 0, 0) if bg_mean > 127 else (255, 255, 255)
    
    overlay = Image.new('RGB', (qr_size_base, qr_size_base), module_color)
    img.paste(overlay, (left, top), mask_img)

def render_game_title(img, settings, side="qr"):
    """Render the game title / card label."""
    prefix = "qr" if side == "qr" else "sol"
    
    if not settings.get(f'{prefix}_title_enabled') or not settings.get(f'{prefix}_title'):
        return
        
    title = settings[f'{prefix}_title']
    pos = settings.get(f'{prefix}_title_pos', 'top')
    font_size = settings.get(f'{prefix}_title_size', 80)
    color = settings.get(f'{prefix}_title_color', (255, 255, 255) if side == 'qr' else (0, 0, 0))
    bg_enabled = settings.get(f'{prefix}_title_bg', False)
    bg_color = settings.get('qr_bg_color', (0, 0, 0)) if side == 'qr' else (255, 255, 255)

    size = settings['card_size']
    margin = 100
    
    draw = ImageDraw.Draw(img)
    font = get_font_for_setting(settings, font_size)
        
    bbox = draw.textbbox((0, 0), title, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    center = size // 2
    qr_bound = center + int(size * settings.get('qr_size_ratio', 0.45)) // 2 + settings.get('qr_backplate_padding', 40)
    bw = settings.get('sol_border_width', 100) // 2

    positions = {
        "top": (center, margin + th // 2, "mm"),
        "bottom": (center, size - margin - th // 2, "mm"),
        "top_left": (margin + tw // 2, margin + th // 2, "mm"),
        "top_right": (size - margin - tw // 2, margin + th // 2, "mm"),
        "bottom_left": (margin + tw // 2, size - margin - th // 2, "mm"),
        "bottom_right": (size - margin - tw // 2, size - margin - th // 2, "mm"),
        "center_above_qr": (center, size - qr_bound - margin - th // 2, "mm"),
        "center_below_qr": (center, qr_bound + margin + th // 2, "mm"),
        "in_border_bottom_right": (size - bw, size - bw, "rm"),
        "in_border_bottom_left": (bw, size - bw, "lm"),
        "in_border_top_right": (size - bw, bw, "rm"),
        "in_border_top_left": (bw, bw, "lm"),
    }
    
    if pos not in positions:
        return
        
    x, y, anchor = positions[pos]

    if bg_enabled:
        bg_padding = 15
        overlay = Image.new("RGBA", (size, size), (0,0,0,0))
        overlay_draw = ImageDraw.Draw(overlay)
        
        left = x - tw - bg_padding if anchor == "rm" else (x - bg_padding if anchor == "lm" else x - tw // 2 - bg_padding)
        right = x + bg_padding if anchor == "rm" else (x + tw + bg_padding if anchor == "lm" else x + tw // 2 + bg_padding)
        
        overlay_draw.rectangle([left, y - th // 2 - bg_padding, right, y + th // 2 + bg_padding], fill=bg_color)
        img.paste(overlay, (0, 0), overlay)

    draw.text((x, y), title, fill=color, font=font, anchor=anchor)

def create_qr_with_neon_rings_in_memory(qr_code, seed=42, settings_override=None):
    """
    Create QR code card with colorful neon rings background.
    seed: per-card seed for unique ring patterns.
    settings_override: dict of settings to override defaults.
    """
    settings = get_settings(settings_override)
    size = settings['card_size']
    
    # Base background (will be overriden by render_card_background if needed)
    img = Image.new("RGB", (size, size), settings['qr_bg_color'])
    
    render_card_background(img, settings, side="qr", seed=seed)
    render_qr_backplate(img, settings)
    render_qr_code(img, qr_code, settings)
    render_game_title(img, settings, side="qr")
        
    return img

def create_solution_side_in_memory(song_name, artist, year, all_years, settings_override=None):
    """
    Create solution card and return the PIL Image object directly.
    settings_override: dict of settings to override defaults.
    """
    settings = get_settings(settings_override)
    size = settings['card_size']
    margin = 150
    max_width = size - (2 * margin)
    
    # Handle unknown year gracefully
    display_year = str(year) if year is not None else "????"

    # Filter None years out of all_years for color calculation
    valid_years = [y for y in all_years if y is not None]
    if not valid_years:
        valid_years = [2000]  # fallback

    effective_year = year if year is not None else int(np.median(valid_years))

    # Get color for this year
    color_rgb = get_year_color(effective_year, valid_years, settings)
    color_int = tuple(int(c * 255) for c in color_rgb)
    
    # Create the base image
    ink_saving_mode = settings.get('ink_saving_mode', False)
    background_color = (255, 255, 255) if ink_saving_mode else color_int
    border_width = settings.get('sol_border_width', 100)

    img = Image.new("RGB", (size, size), background_color)
    
    # Apply background overrides for solution side
    render_card_background(img, settings, side="sol")
    
    if ink_saving_mode:
        draw = ImageDraw.Draw(img)
        draw.rectangle([(0, 0), (size - 1, size - 1)], outline=color_int, width=border_width)
    draw = ImageDraw.Draw(img)
    
    font_year = get_font_for_setting(settings, 380)
    font_artist = get_font_for_setting(settings, 110)
    font_song = get_font_for_setting(settings, 100)
    
    # Choose text color based on background luminance for contrast
    if ink_saving_mode:
        text_color = "black"
    else:
        luminance = 0.299 * color_rgb[0] + 0.587 * color_rgb[1] + 0.114 * color_rgb[2]
        text_color = "black" if luminance > 0.5 else "white"

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
    year_text = display_year
    
    # Draw centered text
    gap = 400
    center_x = size / 2
    center_y = size / 2
    
    draw.text((center_x, center_y), year_text, fill=text_color, 
             font=font_year, anchor="mm")
    
    artist_y = center_y - gap
    if '\n' in artist_text:
        draw.multiline_text((center_x, artist_y), artist_text, fill=text_color,
                          font=font_artist, align="center", anchor="mm")
    else:
        draw.text((center_x, artist_y), artist_text, fill=text_color,
                 font=font_artist, anchor="mm")
    
    song_y = center_y + gap
    if '\n' in song_text:
        draw.multiline_text((center_x, song_y), song_text, fill=text_color,
                          font=font_song, align="center", anchor="mm")
    else:
        draw.text((center_x, song_y), song_text, fill=text_color,
                 font=font_song, anchor="mm")
        
    # Render Custom Title on Solution Side
    render_game_title(img, settings, side="sol")
    
    return img


def fetch_no_api_data_from_list(urls, progress_bar=None):
    """
    Scrapes metadata from public Spotify pages based on a provided list of URLs.
    """
    songs = []
    errors = []
    total = len(urls)
    
    for i, url in enumerate(urls):
        idx = i + 1
        if not _is_spotify_url(url):
            errors.append({"url": url, "error": "Not an open.spotify.com URL, skipped"})
            continue
        print(f"  [{idx}/{total}] Scraping: {url}")
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            res = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # Metadata from OpenGraph tags
            title_tag = soup.find("meta", property="og:title")
            desc_tag = soup.find("meta", property="og:description")
            if not title_tag or not desc_tag:
                errors.append({"url": url, "error": "Missing metadata tags"})
                continue

            title = title_tag['content']
            desc = desc_tag['content']
            artist = desc.split(" · ")[0]
            
            # FIX: correct argument order — (title, artist, fallback)
            year, year_source = get_year_and_source(title, artist, None)
            
            song = {
                'name': sanitize_name(title),
                'original_name': title,
                'original_year': None,
                'year': year,
                'year_source': year_source,
                'artist': artist,
                'link': url,
            }
            songs.append(song)
            
            print(f"  {year} | {artist} - {title}")
            time.sleep(0.5)
            
            # Update Progress — fixed off-by-one
            if progress_bar:
                percent = idx / total
                progress_bar.progress(percent, text=f"Scraped {idx}/{total}: {title[:30]}...")

        except Exception as e:
            print(f"  Error scraping {url}: {e}")
            errors.append({"url": url, "error": str(e)})
    
    if errors:
        print(f"\n⚠ {len(errors)} song(s) failed to scrape:")
        for err in errors:
            print(f"  - {err['url']}: {err['error']}")

    no_year = [s for s in songs if s['year'] is None]
    if no_year:
        print(f"\n⚠ {len(no_year)} song(s) have no year — edit songs.json manually before re-running:")
        for s in no_year:
            print(f"  - {s['artist']} — {s['original_name']}")

    return songs

def create_pdf_in_memory(songs, progress_bar=None, settings_override=None):
    if not songs:
        return None

    settings = get_settings(settings_override)

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

    card_label = settings.get('card_label', None)

    for i in range(0, total_cards, 20):
        batch_songs = list(songs[i:i+20])

        # --- PAGE 1: FRONT (QR CODES) ---
        bg_color = settings.get('card_background_color', (0, 0, 0))
        if isinstance(bg_color, str):
            try:
                bg_color = hex2color(bg_color)
            except:
                bg_color = (0, 0, 0)
        c.setFillColorRGB(bg_color[0], bg_color[1], bg_color[2])
        c.rect(0, 0, width, height, stroke=0, fill=1)

        for idx, song in enumerate(batch_songs):
            col = idx % cols
            row = (idx // cols) 
            x = margin_x + col * card_size
            y = height - margin_y - (row + 1) * card_size
            
            base_qr = create_qr_code(song['link'])
            # Per-card unique ring pattern based on link hash
            qr_pil = create_qr_with_neon_rings_in_memory(base_qr, seed=hash(song['link']), settings_override=settings)

            img_byte_arr = io.BytesIO()
            qr_pil.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)

            c.drawImage(ImageReader(img_byte_arr), x, y, width=card_size, height=card_size)

            # Free the per-card rasters; otherwise peak memory grows with playlist size.
            del base_qr, qr_pil, img_byte_arr

        c.showPage()

        # --- PAGE 2: BACK (SOLUTIONS - MIRRORED) ---
        c.setFillColorRGB(1, 1, 1)
        c.rect(0, 0, width, height, stroke=0, fill=1)

        for idx, song in enumerate(batch_songs):
            orig_col = idx % cols
            mirrored_col = (cols - 1) - orig_col
            row = (idx // cols)
            
            x = margin_x + mirrored_col * card_size
            y = height - margin_y - (row + 1) * card_size
            
            sol_pil = create_solution_side_in_memory(
                song['name'], song['artist'], song['year'], years, settings_override=settings
            )

            sol_byte_arr = io.BytesIO()
            sol_pil.save(sol_byte_arr, format='PNG')
            sol_byte_arr.seek(0)

            c.drawImage(ImageReader(sol_byte_arr), x, y, width=card_size, height=card_size)

            del sol_pil, sol_byte_arr

        if progress_bar:
            processed = min(i + 20, total_cards)
            percent = processed / total_cards
            progress_bar.progress(percent, text=f"Generated {processed}/{total_cards} cards...")
        c.showPage()

        # Reclaim the batch's rasters before building the next page.
        gc.collect()

    c.save()
    pdf_data = buffer.getvalue()
    buffer.close()
    return pdf_data