import streamlit as st
import bcrypt
import json
import os
import numpy as np
from skyfield.api import load, EarthSatellite, wgs84
from datetime import datetime, timedelta
import requests
import pytz
import folium
from streamlit_folium import st_folium
import time
import pandas as pd
import plotly.express as px

# ----------------- CONFIGURATION -----------------
st.set_page_config(page_title="ASAL Satellite Tracker", page_icon="🛰️", layout="wide")
USERS_FILE = "users.json"
FAV_FILE = "favorites.json"
HISTORY_FILE = "pass_history.json"
CDS_LAT = 35.7025
CDS_LON = -0.621389

# Telegram Configuration
TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")

SATELLITES = {
    "ALSAT-1": 27559,
    "ALSAT-2A": 36798,
    "ALSAT-1N": 41789,
    "ALSAT-1B": 41785,
    "ALSAT-2B": 41786,
}

# ----------------- UTILITY FUNCTIONS -----------------
def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance in km between two geographic points"""
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat/2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2)**2
    c = 2 * np.arcsin(np.sqrt(a)) 
    r = 6371
    return c * r

def send_telegram_notification(message):
    """Send notification via Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        st.warning("Missing Telegram configuration. Notifications disabled.")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        params = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, params=params, timeout=10)
        return response.status_code == 200
    except Exception as e:
        st.error(f"Telegram sending error: {str(e)}")
        return False

# ----------------- USER MANAGEMENT FUNCTIONS -----------------
def load_users():
    """Load users from JSON file"""
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_users(users):
    """Save users to JSON file"""
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

def register_user(name, email, password):
    """Register a new user"""
    users = load_users()
    if email in users:
        return False
    hashed_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    users[email] = {
        "name": name, 
        "email": email, 
        "password": hashed_pw,
        "notifications": True
    }
    save_users(users)
    return True

def authenticate_user(email, password):
    """Authenticate user"""
    users = load_users()
    if email in users and bcrypt.checkpw(password.encode(), users[email]["password"].encode()):
        return users[email]["name"]
    return None

def load_favorites():
    """Load favorite satellites"""
    if os.path.exists(FAV_FILE):
        with open(FAV_FILE, "r") as f:
            favorites_data = json.load(f)
            if isinstance(favorites_data, list):
                new_format = {"default@user.com": favorites_data}
                save_favorites(new_format)
                return new_format
            return favorites_data
    return {}

def save_favorites(favs):
    """Save favorite satellites"""
    with open(FAV_FILE, "w") as f:
        json.dump(favs, f)

def get_user_favorites(email):
    """Get user's favorites"""
    favorites_data = load_favorites()
    return favorites_data.get(email, [])

def save_user_favorites(email, favorites):
    """Save user's favorites"""
    favorites_data = load_favorites()
    favorites_data[email] = favorites
    save_favorites(favorites_data)

@st.cache_data(ttl=3600)
def load_satellite_info():
    """Load satellite information"""
    try:
        with open("alsat_satellites.json", "r", encoding='utf-8') as file:
            data = json.load(file)
            return {item["norad_id"]: item for item in data}
    except:
        return {}

@st.cache_data(ttl=3600)
def fetch_tle_from_celestrak(norad_id):
    """Fetch TLE data from Celestrak"""
    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            lines = response.text.strip().splitlines()
            if len(lines) >= 3:
                return lines[1], lines[2]
    except Exception as e:
        st.error(f"TLE retrieval error: {str(e)}")
    return None, None

def get_next_passes(name, norad_id, send_notification=False):
    """Get upcoming visible passes"""
    tle1, tle2 = fetch_tle_from_celestrak(norad_id)
    if not tle1 or not tle2:
        return [], (None, None)

    satellite = EarthSatellite(tle1, tle2, name)
    ts = load.timescale()
    observer = wgs84.latlon(CDS_LAT, CDS_LON)
    now = ts.now()
    end_time = ts.utc(now.utc_datetime() + timedelta(days=2))

    times, events = satellite.find_events(observer, now, end_time, altitude_degrees=10.0)
    passes = []
    
    for i in range(len(events)):
        if events[i] == 0:  # AOS (Acquisition of Signal)
            aos_time = times[i].utc_datetime()
            los_time = times[i+2].utc_datetime() if i+2 < len(events) else None
            
            if los_time:
                passes.append((aos_time, los_time))
                
                if send_notification:
                    # Vérifier si le satellite passe au-dessus du CDS
                    check_interval = timedelta(seconds=30)
                    current_check = aos_time
                    
                    while current_check < los_time:
                        t = ts.utc(current_check)
                        pos = satellite.at(t).subpoint()
                        distance = calculate_distance(pos.latitude.degrees, pos.longitude.degrees, 
                                                    CDS_LAT, CDS_LON)
                        
                        if distance < 10:
                            overhead_time = current_check
                            alert_time = overhead_time - timedelta(minutes=1)
                            current_time = datetime.now(pytz.utc)
                            
                            if current_time <= alert_time <= current_time + timedelta(minutes=1):
                                msg = (f"🛰️ {name} est AU-DESSUS DU CDS MAINTENANT!\n"
                                      f"• Heure: {overhead_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                                      f"• Altitude: {pos.elevation.km:.1f} km\n"
                                      f"• Distance au CDS: {distance:.2f} km")
                                send_telegram_notification(f"ALERTE CDS\n{msg}")
                        
                        current_check += check_interval
                    
                    # Notification 5 minutes avant le passage
                    #alert_time = aos_time - timedelta(hours=4, minutes=10)

                    alert_time = aos_time - timedelta(minutes=5)
                    current_time = datetime.now(pytz.utc)
                    
                    if current_time <= alert_time <= current_time + timedelta(minutes=5):
                        msg = (f"🛰️ {name} arriving in 5 minutes!\n"
                              f"• Start: {aos_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                              f"• End: {los_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                              f"• Duration: {(los_time-aos_time).seconds//60} min")
                        send_telegram_notification(f"PASS ALERT\n{msg}")
    return passes, (tle1, tle2)


# ----------------- AUTH INTERFACE -----------------
def show_authentication():
    """Show authentication interface"""
    menu = st.sidebar.selectbox("Menu", ["Login", "Create Account"])
    if menu == "Create Account":
        st.subheader("Create Account")
        name = st.text_input("Full Name")
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        confirm_password = st.text_input("Confirm Password", type="password")
        if st.button("Register"):
            if not name or not email or not password or not confirm_password:
                st.warning("Please fill all fields.")
            elif password != confirm_password:
                st.error("❌ Passwords don't match.")
            else:
                if register_user(name, email, password):
                    favorites_data = load_favorites()
                    favorites_data[email] = []
                    save_favorites(favorites_data)
                    st.success("✅ Account created. Please login now.")
                else:
                    st.warning("⚠️ This email is already registered.")
    else:
        st.subheader("Login")
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        if st.button("Login", key="login_button"):
            user_name = authenticate_user(email, password)
            if user_name:
                st.session_state.user_name = user_name
                st.session_state.user_email = email
                st.session_state.is_authenticated = True  # Directly set authenticated
                st.session_state.enable_notifications = True
                st.session_state.favorites = get_user_favorites(email)
                st.rerun()  # Force immediate refresh
            else:
                st.error("❌ Incorrect email or password.")

# ----------------- DISPLAY FUNCTIONS -----------------
def show_satellite_info(sat_data, norad_id):
    """Show satellite information"""
    st.subheader("📄 Technical Details")
    cols = st.columns(2)

    with cols[0]:
        st.markdown("**Identity**")
        st.markdown(f"- Name: {sat_data.get('name', 'N/A')}")
        st.markdown(f"- NORAD ID: `{norad_id}`")
        st.markdown(f"- International code: {sat_data.get('int_code', 'N/A')}")
        st.markdown(f"- Launch date: {sat_data.get('launch_date', 'N/A')}")
        st.markdown(f"- Status: {sat_data.get('status', 'N/A')}")
        st.markdown(f"- Type: {sat_data.get('type', 'N/A')}")

    with cols[1]:
        st.markdown("**Orbit**")
        st.markdown(f"- Apogee: {sat_data.get('apogee_km', 'N/A')} km")
        st.markdown(f"- Perigee: {sat_data.get('perigee_km', 'N/A')} km")
        st.markdown(f"- Inclination: {sat_data.get('inclination_deg', 'N/A')}°")
        st.markdown(f"- Period: {sat_data.get('period_min', 'N/A')} min")
        st.markdown(f"- Semi-major axis: {sat_data.get('semi_major_axis_km', 'N/A')} km")

    st.markdown("**Description:**")
    st.markdown(f"> {sat_data.get('description', 'No description available.')}")

def show_satellite_map(tle_lines):
    """Show satellite position on map"""
    try:
        m = folium.Map(
            location=[CDS_LAT, CDS_LON],
            zoom_start=5,
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='Esri',
            prefer_canvas=True
        )

        folium.Marker(
            [CDS_LAT, CDS_LON],
            popup="Satellite Development Center",
            icon=folium.Icon(color='green')
        ).add_to(m)

        satellite = EarthSatellite(*tle_lines, st.session_state.selected_satellite)
        ts = load.timescale()
        now = ts.now()

        pos = satellite.at(now).subpoint()
        folium.Marker(
            [pos.latitude.degrees, pos.longitude.degrees],
            popup=f"""
                <b>{st.session_state.selected_satellite}</b><br>
                Current position<br>
                Lat: {pos.latitude.degrees:.4f}°<br>
                Lon: {pos.longitude.degrees:.4f}°<br>
                Alt: {pos.elevation.km:.1f} km
            """,
            icon=folium.Icon(color='red', icon='satellite', prefix='fa')
        ).add_to(m)

        minutes_range = np.linspace(-45, 45, 180)
        times = ts.utc(now.utc_datetime() + np.array([timedelta(minutes=m) for m in minutes_range]))
        lat_lon = [(satellite.at(t).subpoint().latitude.degrees, satellite.at(t).subpoint().longitude.degrees) for t in times]
        folium.PolyLine(lat_lon, color='yellow', weight=2.5, opacity=0.8).add_to(m)

        st_folium(m, width=700, height=500, returned_objects=[], use_container_width=True)

    except Exception as e:
        st.error(f"Map creation error: {str(e)}")

def show_results(passes, tle_lines):
    """Show pass results and TLE data"""
    st.subheader(f"Data for {st.session_state.selected_satellite}")
    
    if not st.session_state.countdown_placeholder:
        st.session_state.countdown_placeholder = st.empty()
    
    if passes:
        now_utc = datetime.now(pytz.utc)
        display_text = ""
        
        for i, (aos, los) in enumerate(passes, 1):
            duration = los - aos
            countdown = aos - now_utc
            
            satellite = EarthSatellite(*tle_lines, st.session_state.selected_satellite)
            ts = load.timescale()
            check_times = [ts.utc(aos + timedelta(seconds=x*30)) 
                          for x in range(int(duration.total_seconds()/30))]
            
            overhead_times = []
            for t in check_times:
                pos = satellite.at(t).subpoint()
                distance = calculate_distance(pos.latitude.degrees, pos.longitude.degrees,
                                             CDS_LAT, CDS_LON)
                if distance < 10:
                    overhead_times.append(t.utc_datetime())
            
            if overhead_times:
                overhead_str = "\n- Above CDS at: " + ", ".join(
                    [t.strftime('%H:%M:%S UTC') for t in overhead_times])
                display_text += f"📡 Pass #{i}:\n- Start: {aos.strftime('%Y-%m-%d %H:%M:%S UTC')}\n- End: {los.strftime('%Y-%m-%d %H:%M:%S UTC')}\n- Duration: {str(duration).split('.')[0]}\n- Countdown: {str(countdown).split('.')[0]}{overhead_str}\n\n"
            else:
                display_text += f"📡 Pass #{i}:\n- Start: {aos.strftime('%Y-%m-%d %H:%M:%S UTC')}\n- End: {los.strftime('%Y-%m-%d %H:%M:%S UTC')}\n- Duration: {str(duration).split('.')[0]}\n- Countdown: {str(countdown).split('.')[0]}\n\n"

        st.session_state.countdown_placeholder.markdown(display_text)

        st.write("📄 Automatically updated TLE:")
        tle_text = f"0 {st.session_state.selected_satellite}\n{tle_lines[0]}\n{tle_lines[1]}"
        tle_filename = f"{st.session_state.selected_satellite.replace(' ', '_')}_TLE.txt"
        st.code(tle_text)
        st.download_button("📁 Export TLE", tle_text, file_name=tle_filename, mime="text/plain")

    else:
        st.warning("No visible passes in the next 48 hours or TLE unavailable.")

def update_countdown():
    """Update countdown dynamically"""
    if st.session_state.pass_data and st.session_state.countdown_placeholder:
        while True:
            time.sleep(1)
            now_utc = datetime.now(pytz.utc)
            updated_text = ""
            
            for i, (aos, los) in enumerate(st.session_state.pass_data, 1):
                duration = los - aos
                countdown = aos - now_utc
                
                if countdown.total_seconds() > 0:
                    updated_text += f"📡 Pass #{i}:\n- Start: {aos.strftime('%Y-%m-%d %H:%M:%S UTC')}\n- End: {los.strftime('%Y-%m-%d %H:%M:%S UTC')}\n- Duration: {str(duration).split('.')[0]}\n- Countdown: {str(countdown).split('.')[0]}\n\n"
                else:
                    if now_utc < los:
                        elapsed = now_utc - aos
                        updated_text += f"🚀 Pass #{i} IN PROGRESS:\n- Start: {aos.strftime('%Y-%m-%d %H:%M:%S UTC')}\n- End: {los.strftime('%Y-%m-%d %H:%M:%S UTC')}\n- Duration: {str(duration).split('.')[0]}\n- Elapsed: {str(elapsed).split('.')[0]}\n\n"
                    else:
                        updated_text += f"✅ Pass #{i} COMPLETED:\n- Start: {aos.strftime('%Y-%m-%d %H:%M:%S UTC')}\n- End: {los.strftime('%Y-%m-%d %H:%M:%S UTC')}\n- Duration: {str(duration).split('.')[0]}\n\n"
            
            st.session_state.countdown_placeholder.markdown(updated_text)
            
            if not st.session_state.get("is_authenticated", False):
                break

# ----------------- MAIN FUNCTION -----------------
def main():
    """Main application function"""
    # Session state initialization
    if "is_authenticated" not in st.session_state:
        st.session_state.is_authenticated = False
    if "user_name" not in st.session_state:
        st.session_state.user_name = ""
    if "user_email" not in st.session_state:
        st.session_state.user_email = ""
    if "favorites" not in st.session_state:
        st.session_state.favorites = []
    if "selected_satellite" not in st.session_state:
        st.session_state.selected_satellite = list(SATELLITES.keys())[0]
    if "enable_notifications" not in st.session_state:
        st.session_state.enable_notifications = False
    if "last_checked" not in st.session_state:
        st.session_state.last_checked = datetime.min
    if "pass_data" not in st.session_state:
        st.session_state.pass_data = None
    if "countdown_placeholder" not in st.session_state:
        st.session_state.countdown_placeholder = None

    # Show authentication if not logged in
    if not st.session_state.is_authenticated:
        show_authentication()
        return

    # Main interface
    st.sidebar.success(f"Logged in as {st.session_state.user_name}")
    
    # Favorites management
    st.sidebar.title("⭐ Favorites")
    if st.sidebar.button("Add to favorites"):
        if (st.session_state.selected_satellite not in st.session_state.favorites and 
            st.session_state.user_email):
            st.session_state.favorites.append(st.session_state.selected_satellite)
            save_user_favorites(st.session_state.user_email, st.session_state.favorites)

    for fav in st.session_state.favorites:
        cols = st.sidebar.columns([3, 1])
        cols[0].markdown(f"- {fav}")
        if cols[1].button("×", key=f"del_{fav}"):
            st.session_state.favorites.remove(fav)
            save_user_favorites(st.session_state.user_email, st.session_state.favorites)
            st.rerun()
            return

    # Notification preferences
    st.session_state.enable_notifications = st.sidebar.checkbox(
        "Enable Telegram notifications",
        value=st.session_state.enable_notifications
    )
    
    # Logout button
    if st.sidebar.button("🚪 Logout"):
        st.session_state.is_authenticated = False
        st.session_state.user_name = ""
        st.session_state.user_email = ""
        st.rerun()
        return

    # Periodic pass checking
    if (datetime.now() - st.session_state.last_checked).seconds >= 60:
        for sat_name in st.session_state.favorites:
            norad_id = SATELLITES.get(sat_name)
            if norad_id:
                get_next_passes(sat_name, norad_id, st.session_state.enable_notifications)
        
        st.session_state.last_checked = datetime.now()

    # Satellite selection
    st.title("ASAL Satellite Tracker")
    selected = st.selectbox(
        "Select a satellite:", 
        list(SATELLITES.keys()),
        key="satellite_select"
    )
    
    # Immediate selection update
    if selected != st.session_state.selected_satellite:
        st.session_state.selected_satellite = selected
        st.rerun()

    # Satellite information display
    satellite_info = load_satellite_info()
    current_norad_id = SATELLITES[selected]
    sat_data = satellite_info.get(current_norad_id, {})

    if sat_data:
        show_satellite_info(sat_data, current_norad_id)
    else:
        st.info("No metadata available for this satellite.")

    # TLE and pass data retrieval
    passes, (tle1, tle2) = get_next_passes(selected, current_norad_id, st.session_state.enable_notifications)
    tle_lines = [tle1, tle2] if tle1 and tle2 else []
    st.session_state.pass_data = passes

    # Map display
    if tle_lines:
        show_satellite_map(tle_lines)

    # Results display
    show_results(passes, tle_lines)

    # Footer
    st.markdown("---")
    st.caption("Developed for Algerian Space Agency (ASAL) | Orbital data: Celestrak.org")

    # Start dynamic countdown
    update_countdown()

# ----------------- APPLICATION LAUNCH -----------------
if __name__ == "__main__":
    main()