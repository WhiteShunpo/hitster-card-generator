import streamlit as st
import os
from src import utils

# --- CONFIGURATION & SESSION STATE ---
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(PROJECT_ROOT, "fonts")

# Initialize session state for user input if it doesn't exist
if 'user_input' not in st.session_state:
    st.session_state.user_input = ""

def set_example_links():
    st.session_state.user_input = (
        "https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8?si=44d4b8822cac4dc8\n"
        "https://open.spotify.com/track/0Bo5fjMtTfCD8vHGebivqc?si=5bc94c4aadf84bca\n"
        "https://open.spotify.com/track/6Sy9BUbgFse0n0LPA5lwy5?si=ac74b629e3834310"
    )

db = {
    "fonts_dict": {
        'year': os.path.join(FONT_DIR, "Montserrat-Bold.ttf"),
        'artist': os.path.join(FONT_DIR, "Montserrat-SemiBold.ttf"),
        'song': os.path.join(FONT_DIR, "Montserrat-MediumItalic.ttf")
    },
    "color_gradient": ["#7030A0", "#E31C79", "#FF6B9D", "#FFA500", "#FFD700", "#87CEEB", "#4169E1"],
    "card_size": 2000, 
    "neon_colors": [(255, 0, 100), (0, 200, 255), (255, 255, 0), (0, 255, 120)],
    # FIXME: allow to set from UI:
    "ink_saving_mode": False,
    "card_draw_border": False,
    "card_background_color": "black",
    "card_border_color": "white",
}
utils.db = db

# --- UI INTERFACE ---
st.set_page_config(page_title="Hitster Generator", page_icon="🎵", layout="wide")

with st.sidebar:
     # --- NEW SETTINGS SECTION ---
    st.header("⚙️ Settings")
    
    # helper text
    st.caption("Customize your print layout")
    
    # 1. Ink Saving Mode Toggle
    ink_mode = st.toggle("Ink Saving Mode", value=False, 
                        help="Use white background and black text to save ink.")
    
    # 2. Border Toggle
    border_mode = st.toggle("Draw Cutting Borders", value=False,
                           help="Draw a line around each card for easier cutting.")

    # 3. Update the global configuration based on UI selection
    db["ink_saving_mode"] = ink_mode
    db["card_draw_border"] = border_mode
    # Update colors dynamically based on ink mode
    db["card_background_color"] = "white" if ink_mode else "black"
    db["card_border_color"] = "black" if ink_mode else "white"
    
    st.divider()

    # --- FEEDBACK SECTION ---
    st.header("Feedback")
    st.write("Found a bug or have a feature idea? Let me know on GitHub!")
    
    # This button opens your GitHub Issues page in a new tab
    st.link_button(
        label="Open GitHub Issues", 
        url="https://github.com/WhiteShunpo/hitster-cards-generator/issues",
        type="secondary", # "primary" for red/filled, "secondary" for outline
    )

    st.divider()

    # --- SUPPORT SECTION ---
    st.markdown("### ☕ Support the Project")
    st.write("If this tool made your game night special, feel free to support the developer!")
    
    # This uses the professional button from Buy Me a Coffee
    # Replace 'WhiteShunpo' if your final username is different
    button_html = """
    <a href="https://www.buymeacoffee.com/WhiteShunpo" target="_blank">
        <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" 
        alt="Buy Me A Coffee" style="height: 50px !important;width: 181px !important;" >
    </a>
    """
    st.markdown(button_html, unsafe_allow_html=True)


st.divider()

with st.expander("Disclaimer, Accuracy & Support"):
    st.info("""
    **Why are some years wrong?**
    Metadata providers often list the date a song was added to a digital album (like a 'Greatest Hits' or 'Remaster') rather than the original single release date.
    
    **📱 Mobile User Note:**
    If the download button doesn't respond on your phone, please try a desktop browser. Mobile browsers sometimes struggle with large in-memory PDF streams.
    """)

# --- MAIN PAGE ---
# Hero Section
col1, col2 = st.columns([2, 1])
with col1:
    st.title("🎵 Hitster Card Generator")
    st.markdown("Generate custom music game cards from any Spotify playlist.")
with col2:
    # Optional: Place a small logo or card preview here
    # st.image("example_pictures/qr_code_side.png", width=150)
    pass

st.divider()

# Instructions and Preview Columns
info_col, preview_col = st.columns([1, 1])

with info_col:
    with st.expander("How do I get links?", expanded=True):
        st.write("1. Open **Spotify Desktop**.")
        st.write("2. Go to your playlist.")
        st.write("3. Select songs (**Ctrl+A**).")
        st.write("4. Copy (**Ctrl+C**).")
        st.write("5. Paste below!")

with preview_col:
    # Example images to show the "end goal"
    st.info("**Tip:** Spotify 'Track Links' usually start with `https://open.spotify.com/track/...`")

# Input Area with Functional Enhancements
st.subheader("Input")

# Sample Links Button
st.button("✨ Load Example Links", on_click=set_example_links)

# Text Area connected to Session State
user_playlist_input = st.text_input(
    "Paste Playlist links here:", 
    key="user_playlist_input",
    placeholder="https://open.spotify.com/playlist/..."
)

# Text Area connected to Session State
user_input = st.text_area(
    "Paste Spotify links here:", 
    height=200, 
    key="user_input",
    placeholder="https://open.spotify.com/track/..."
)

# Real-time Link Counter
# Note: Standard Spotify track links usually contain '/track/' and a 22-char ID
valid_links_found = [line for line in user_input.split('\n') if "/track/" in line]
# Only one line can be entered in a text_input, so this will be either empty or a single playlist link
valid_playlist_link_found = [user_playlist_input] if "/playlist/" in user_playlist_input else []
link_count = len(valid_links_found)

if link_count > 0:
    st.success(f"{link_count} valid Spotify track(s) detected.")
else:
    st.warning("No valid track links detected yet.")


# --- GENERATION LOGIC ---
if st.button("Create My PDF", type="primary"):
    if link_count == 0:
        st.error("Please paste some valid Spotify track links first!")
    else:
        links_to_process = [line.strip() for line in valid_links_found]
        
        with st.status("Working on your cards...", expanded=True) as status:
            st.write("Step 1: Scraping metadata...")
            progress_bar = st.progress(0, text="Scraping starting...")
            
            songs = utils.fetch_no_api_data_from_list(links_to_process, progress_bar)
            years = [song['year'] for song in songs]
            if -1 in years:
                st.warning("🕵️ Some years couldn't be found automatically and are marked as '-1'. "
               "You might want to check the PDF or use the 'Accuracy Fix' on GitHub.")
            st.write("Step 2: Generating high-res cards...")
            progress_bar.progress(0, text="PDF generation starting...")
            
            # 1. Generate the PDF
            pdf_data = utils.create_pdf_in_memory(songs, valid_playlist_link_found, progress_bar)
            
            status.update(label="All Cards Generated!", state="complete")
            progress_bar.empty()

        # 2. Place the download button IMMEDIATELY here
        # This keeps the 'pdf_data' variable in scope for the download
        st.download_button(
            label="💾 Download Printable PDF",
            data=pdf_data,
            file_name="my_hitster_cards.pdf",
            mime="application/pdf",
            use_container_width=True
        )
        
        # 3. Success message
        st.balloons()