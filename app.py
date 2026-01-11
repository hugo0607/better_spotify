import streamlit as st
import boto3
import yt_dlp
import os
import tempfile
import requests
import base64
import random

# ============================================
# CONFIGURACIÓN DE SECRETS (Compatible con Render)
# ============================================
def get_secret(key, default=None):
    """
    Obtiene un secret desde múltiples fuentes en orden de prioridad:
    1. Variables de entorno (para Render)
    2. st.secrets (para desarrollo local)
    3. /etc/secrets/ (para Render con archivos)
    """
    # 1. Intentar desde variables de entorno
    env_value = os.getenv(key)
    if env_value:
        return env_value
    
    # 2. Intentar desde st.secrets
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        pass
    
    # 3. Intentar desde /etc/secrets/secrets.toml (Render)
    try:
        import toml
        secrets_path = f"/etc/secrets/secrets.toml"
        if os.path.exists(secrets_path):
            secrets_data = toml.load(secrets_path)
            if key in secrets_data:
                return secrets_data[key]
    except:
        pass
    
    return default

# ============================================
# CONFIGURACIÓN DE PÁGINA
# ============================================
st.set_page_config(
    page_title="Better Spotify",
    page_icon="🎵",
    layout="wide"
)

# ============================================
# CONTROL DE ACCESO FÁCIL
# ============================================
def check_access():
    """Verifica acceso con código persistente en sesión"""
    SECRET_CODE = get_secret("ACCESS_CODE", "mi_codigo_secreto_123")
    
    # Si ya está autenticado en esta sesión
    if st.session_state.get("authenticated", False):
        return
    
    # Verificar query params
    query_params = st.query_params
    access_code = query_params.get("code", None)
    
    if access_code == SECRET_CODE:
        st.session_state.authenticated = True
        st.rerun()
    else:
        st.error("🔒 Acceso denegado")
        
        # Formulario alternativo
        with st.form("login_form"):
            code_input = st.text_input("Código de acceso", type="password")
            if st.form_submit_button("Acceder"):
                if code_input == SECRET_CODE:
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("Código incorrecto")
        
        st.stop()

check_access()


# ============================================
# FUNCIONES DE BACKBLAZE B2
# ============================================
def get_b2_client():
    """Crea cliente de Backblaze B2"""
    return boto3.client(
        's3',
        endpoint_url=get_secret("B2_ENDPOINT"),
        aws_access_key_id=get_secret("B2_KEY_ID"),
        aws_secret_access_key=get_secret("B2_APP_KEY")
    )

def list_playlists():
    """Lista todas las carpetas (playlists) en el bucket"""
    b2 = get_b2_client()
    bucket = get_secret("B2_BUCKET")
    
    response = b2.list_objects_v2(Bucket=bucket, Delimiter='/')
    playlists = []
    
    for prefix in response.get('CommonPrefixes', []):
        folder_name = prefix['Prefix'].rstrip('/')
        playlists.append(folder_name)
    
    return playlists

def list_songs_in_playlist(playlist_name):
    """Lista todas las canciones en una playlist"""
    b2 = get_b2_client()
    bucket = get_secret("B2_BUCKET")
    
    response = b2.list_objects_v2(Bucket=bucket, Prefix=f"{playlist_name}/")
    songs = []
    
    for obj in response.get('Contents', []):
        key = obj['Key']
        if key.endswith('.mp3'):
            song_name = key.split('/')[-1]
            songs.append({'key': key, 'name': song_name})
    
    return songs

def get_song_data(key):
    """Descarga la canción y devuelve los bytes"""
    b2 = get_b2_client()
    bucket = get_secret("B2_BUCKET")
    
    response = b2.get_object(Bucket=bucket, Key=key)
    return response['Body'].read()

def upload_song_to_b2(file_path, playlist_name):
    """Sube una canción a B2"""
    b2 = get_b2_client()
    bucket = get_secret("B2_BUCKET")
    filename = os.path.basename(file_path)
    key = f"{playlist_name}/{filename}"
    
    b2.upload_file(file_path, bucket, key)
    return key

# ============================================
# FUNCIONES DE SPOTIFY API
# ============================================
def get_spotify_access_token():
    """Obtiene token de acceso para la API de Spotify"""
    client_id = get_secret("SPOTIFY_CLIENT_ID")
    client_secret = get_secret("SPOTIFY_CLIENT_SECRET")
    
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
    
    headers = {
        'Authorization': f'Basic {encoded_credentials}',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    data = {'grant_type': 'client_credentials'}
    
    response = requests.post('https://accounts.spotify.com/api/token', headers=headers, data=data)
    response.raise_for_status()
    return response.json()['access_token']

def extract_playlist_id(playlist_url):
    """Extrae el ID de la playlist de una URL de Spotify"""
    playlist_url = playlist_url.strip().split('?')[0]
    parts = playlist_url.split('/')
    for part in parts:
        if len(part) == 22:
            return part
    raise ValueError("No se pudo extraer el ID de la playlist")

def get_playlist_tracks(playlist_url):
    """Extrae todas las canciones de una playlist de Spotify"""
    access_token = get_spotify_access_token()
    playlist_id = extract_playlist_id(playlist_url)
    
    headers = {'Authorization': f'Bearer {access_token}'}
    url = f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks'
    
    all_tracks = []
    
    while url:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        for item in data.get('items', []):
            track = item.get('track', {})
            if track:
                name = track.get('name', '')
                artists = [a.get('name', '') for a in track.get('artists', [])]
                if name and artists:
                    all_tracks.append(f"{name} - {', '.join(artists)}")
        
        url = data.get('next')
    
    return all_tracks

# ============================================
# FUNCIÓN DE DESCARGA CON YT-DLP
# ============================================
def download_song(song_name, output_path, quality=192):
    """Descarga canción de YouTube como MP3"""
    search_query = f"ytsearch1:{song_name}"
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'nooverwrites': True,
        'no_color': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': str(quality),
        }],
        'outtmpl': os.path.join(output_path, '%(title)s.%(ext)s'),
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([search_query])
    
    for f in os.listdir(output_path):
        if f.endswith('.mp3'):
            return os.path.join(output_path, f)
    return None

# ============================================
# FUNCIÓN DE AUTOPLAY
# ============================================
def inject_autoplay_script():
    """Inyecta JavaScript para reproducción automática"""
    autoplay_js = """
    <script>
    let isProcessing = false; // Flag para evitar ejecuciones simultáneas
    
    setTimeout(() => {
        function setupAudioListener() {
            try {
                const parentDoc = window.parent.document;
                const audio = parentDoc.querySelector('audio.stAudio');
                
                if (audio && !audio.hasAttribute('data-listener-added')) {
                    console.log('✅ Audio encontrado, configurando listener...');
                    
                    // Marcar que ya tiene listener para no duplicar
                    audio.setAttribute('data-listener-added', 'true');
                    
                    // Evento cuando termina el audio
                    audio.addEventListener('ended', function() {
                        if (isProcessing) {
                            console.log('⏳ Ya hay una ejecución en proceso, saltando...');
                            return;
                        }
                        
                        isProcessing = true;
                        console.log('🎵 Audio terminado, buscando siguiente...');
                        
                        const buttons = Array.from(parentDoc.querySelectorAll('button'));
                        const nextButton = buttons.find(btn => 
                            (btn.textContent || '').includes('🔀')
                        );
                        
                        if (nextButton) {
                            console.log('🔘 Haciendo clic en siguiente...');
                            nextButton.click();
                            
                            // Esperar 4 segundos antes de reproducir (2s para cargar + 2s extra)
                            setTimeout(() => {
                                const newAudio = parentDoc.querySelector('audio.stAudio');
                                if (newAudio) {
                                    // Remover el atributo del audio anterior
                                    const oldAudios = parentDoc.querySelectorAll('audio[data-listener-added]');
                                    oldAudios.forEach(a => {
                                        if (a !== newAudio) {
                                            a.removeAttribute('data-listener-added');
                                        }
                                    });
                                    
                                    newAudio.play()
                                        .then(() => {
                                            console.log('▶️ Reproduciendo nuevo audio');
                                            isProcessing = false;
                                            // Configurar listener para el nuevo audio
                                            setupAudioListener();
                                        })
                                        .catch(e => {
                                            console.log('❌ Error reproduciendo:', e);
                                            isProcessing = false;
                                            setupAudioListener();
                                        });
                                } else {
                                    console.log('⚠️ No se encontró nuevo audio');
                                    isProcessing = false;
                                    setupAudioListener();
                                }
                            }, 4000);
                        } else {
                            console.log('⚠️ No se encontró botón siguiente');
                            isProcessing = false;
                        }
                    });
                    
                    console.log('✅ Listener configurado correctamente');
                }
            } catch(e) {
                console.log('❌ Error:', e);
                isProcessing = false;
            }
        }
        
        // Configurar el listener inicial
        setupAudioListener();
        
        // Verificar periódicamente por si se carga nuevo audio
        setInterval(() => {
            if (!isProcessing) {
                setupAudioListener();
            }
        }, 3000);
        
        console.log('✅ Sistema de autoplay activado');
    }, 3000);
    </script>
    """
    st.components.v1.html(autoplay_js, height=0)


# ============================================
# INTERFAZ DE STREAMLIT
# ============================================
st.title("🎵 Spotify Playlist Manager")

tab1, tab2 = st.tabs(["⬇️ Descargar Playlist", "🎧 Mis Playlists"])

# ============================================
# TAB 1: DESCARGAR PLAYLIST
# ============================================
with tab1:
    st.header("Descargar nueva playlist")
    
    playlist_url = st.text_input(
        "🔗 URL de la playlist de Spotify",
        placeholder="https://open.spotify.com/playlist/..."
    )
    
    folder_name = st.text_input(
        "📁 Nombre de la carpeta",
        placeholder="Nombre para guardar la playlist"
    )
    
    quality = st.select_slider(
        "🎚️ Calidad de audio",
        options=[128, 192, 320],
        value=192,
        format_func=lambda x: f"{x} kbps"
    )
    
    if st.button("🚀 Descargar Playlist", type="primary"):
        if not playlist_url:
            st.error("Introduce la URL de la playlist")
        elif not folder_name.strip():
            st.error("Introduce un nombre para la carpeta")
        elif "spotify.com/playlist" not in playlist_url:
            st.error("URL no válida. Debe ser una playlist de Spotify")
        else:
            try:
                safe_name = "".join(c for c in folder_name if c.isalnum() or c in ' -_').strip()
                
                with st.spinner("Obteniendo canciones de Spotify..."):
                    canciones = get_playlist_tracks(playlist_url)
                
                st.success(f"✅ {len(canciones)} canciones encontradas")
                
                progress_bar = st.progress(0)
                status = st.empty()
                
                for i, song in enumerate(canciones):
                    status.text(f"⬇️ ({i+1}/{len(canciones)}) {song}")
                    
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        downloaded = download_song(song, tmp_dir, quality)
                        
                        if downloaded and os.path.exists(downloaded):
                            upload_song_to_b2(downloaded, safe_name)
                    
                    progress_bar.progress((i + 1) / len(canciones))
                
                status.empty()
                st.success(f"🎉 ¡Listo! Canciones guardadas en '{safe_name}'")
                st.balloons()
                    
            except Exception as e:
                st.error(f"Error: {str(e)}")




# ============================================
# TAB 2: MIS PLAYLISTS
# ============================================
with tab2:
    st.header("Mis playlists guardadas")
    
    if st.button("🔄 Refrescar"):
        st.rerun()
    
    try:
        playlists = list_playlists()
        
        if not playlists:
            st.info("No hay playlists guardadas. ¡Descarga una en la otra pestaña!")
        else:
            selected_playlist = st.selectbox(
                "📁 Selecciona una playlist",
                playlists,
                format_func=lambda x: f"🎵 {x}"
            )
            
            if selected_playlist:
                songs = list_songs_in_playlist(selected_playlist)
                
                if songs:
                    st.write(f"**{len(songs)} canciones**")
                    st.divider()
                    
                    # Inicializar índice aleatorio en session_state
                    if 'current_index' not in st.session_state:
                        st.session_state.current_index = random.randint(0, len(songs) - 1)
                    
                    # Botón para canción aleatoria
                    if st.button("🔀 Siguiente (Aleatoria)", type="primary"):
                        st.session_state.current_index = random.randint(0, len(songs) - 1)
                        st.rerun()
                    
                    song_names = [s['name'].replace('.mp3', '') for s in songs]
                    selected_index = st.selectbox(
                        "🎵 Selecciona una canción",
                        range(len(songs)),
                        index=st.session_state.current_index,
                        format_func=lambda i: song_names[i]
                    )
                    
                    # Actualizar session_state si el usuario selecciona manualmente
                    st.session_state.current_index = selected_index
                    
                    if selected_index is not None:
                        selected_song = songs[selected_index]
                        st.write(f"**🎧 Reproduciendo:** {selected_song['name'].replace('.mp3', '')}")
                        
                        audio_data = get_song_data(selected_song['key'])
                        st.audio(audio_data, format='audio/mp3')
                        
                        # Inyectar script de autoplay
                        inject_autoplay_script()
                        
                        with st.expander("📋 Todas las canciones"):
                            for i, name in enumerate(song_names):
                                prefix = "▶️ " if i == selected_index else "　"
                                st.write(f"{prefix}{name}")
                else:
                    st.warning("Playlist vacía")
                    
    except Exception as e:
        st.error(f"Error: {str(e)}")
        st.info("Configura los secrets de Backblaze B2")

# ============================================
# SIDEBAR
# ============================================
with st.sidebar:
    st.header("ℹ️ Configuración")
    
    st.markdown("""
    ### Para Render (Variables de Entorno)
    
    Agrega estas variables en el dashboard de Render:
    
    - `B2_ENDPOINT`
    - `B2_KEY_ID`
    - `B2_APP_KEY`
    - `B2_BUCKET`
    - `SPOTIFY_CLIENT_ID`
    - `SPOTIFY_CLIENT_SECRET`
    - `ACCESS_CODE`
    
    ### Para local (.streamlit/secrets.toml)
    
    ```toml
    B2_ENDPOINT = "https://s3.eu-central-003.backblazeb2.com"
    B2_KEY_ID = "tu_key_id"
    B2_APP_KEY = "tu_app_key"
    B2_BUCKET = "songs-bucket-app"
    SPOTIFY_CLIENT_ID = "tu_client_id"
    SPOTIFY_CLIENT_SECRET = "tu_client_secret"
    ACCESS_CODE = "mi_codigo_secreto_123"
    ```
    """)
