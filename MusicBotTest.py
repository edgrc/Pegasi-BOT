# Importing libraries and modules
import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
from collections import deque
import asyncio
import re
import requests
import base64

# Environment variables for tokens and other sensitive data
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

GUILD_ID = #add your discord token here !

# Spotify credentials (à remplir dans le .env)
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# Create the structure for queueing songs - Dictionary of queues
SONG_QUEUES = {}

# Regex pour détecter les liens Spotify
SPOTIFY_TRACK_REGEX = r"https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?track/[a-zA-Z0-9]+(?:\?.*)?"
SPOTIFY_PLAYLIST_REGEX = r"https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?playlist/[a-zA-Z0-9]+(?:\?.*)?"


# ---------- yt-dlp helper ----------

async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts))


def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)


# ---------- Spotify helpers ----------

def get_spotify_track_info(url):
    api_url = f"https://open.spotify.com/oembed?url={url}"
    data = requests.get(api_url).json()
    return data["title"]  # "Artiste – Titre"


def get_spotify_token(client_id, client_secret):
    auth = f"{client_id}:{client_secret}"
    b64_auth = base64.b64encode(auth.encode()).decode()

    headers = {
        "Authorization": f"Basic {b64_auth}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {"grant_type": "client_credentials"}

    response = requests.post(
        "https://accounts.spotify.com/api/token",
        headers=headers,
        data=data
    )
    response.raise_for_status()
    return response.json()["access_token"]


def get_spotify_playlist_tracks(url, token):
    playlist_id = url.split("/")[-1].split("?")[0]

    headers = {"Authorization": f"Bearer {token}"}
    api_url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

    response = requests.get(api_url, headers=headers)
    response.raise_for_status()
    data = response.json()

    tracks = []
    for item in data.get("items", []):
        track = item.get("track")
        if not track:
            continue
        title = track.get("name")
        artists = ", ".join(artist["name"] for artist in track.get("artists", []))
        if title and artists:
            tracks.append(f"{artists} – {title}")
    return tracks


# ---------- Discord intents & bot ----------

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ---------- Events ----------

@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    await bot.tree.sync(guild=guild)
    print(f"{bot.user} is online!")


# ---------- Commands ----------

@bot.tree.command(name="skip", description="Skips the current playing song")
async def skip(interaction: discord.Interaction):
    if interaction.guild.voice_client and (
            interaction.guild.voice_client.is_playing()
            or interaction.guild.voice_client.is_paused()
    ):
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("Skipped the current song.")
    else:
        await interaction.response.send_message("Not playing anything to skip.")


@bot.tree.command(name="pause", description="Pause the currently playing song.")
async def pause(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if voice_client is None:
        return await interaction.response.send_message("I'm not in a voice channel.")

    if not voice_client.is_playing():
        return await interaction.response.send_message("Nothing is currently playing.")

    voice_client.pause()
    await interaction.response.send_message("Playback paused!")


@bot.tree.command(name="resume", description="Resume the currently paused song.")
async def resume(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if voice_client is None:
        return await interaction.response.send_message("I'm not in a voice channel.")

    if not voice_client.is_paused():
        return await interaction.response.send_message("I’m not paused right now.")

    voice_client.resume()
    await interaction.response.send_message("Playback resumed!")


@bot.tree.command(name="stop", description="Stop playback and clear the queue.")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    if not voice_client or not voice_client.is_connected():
        return await interaction.response.send_message("I'm not connected to any voice channel.")

    guild_id_str = str(interaction.guild_id)
    if guild_id_str in SONG_QUEUES:
        SONG_QUEUES[guild_id_str].clear()

    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()

    await voice_client.disconnect()
    await interaction.response.send_message("Stopped playback and disconnected!")


@bot.tree.command(name="pegasiplay", description="Play a song or add it to the queue.")
@app_commands.describe(song_query="Search query or URL")
async def pegasiplay(interaction: discord.Interaction, song_query: str):
    await interaction.response.defer()

    # Vérif vocal
    voice_channel = getattr(interaction.user.voice, "channel", None)
    if voice_channel is None:
        await interaction.followup.send("You must be in a voice channel.")
        return

    voice_client = interaction.guild.voice_client
    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_channel != voice_client.channel:
        await voice_client.move_to(voice_channel)

    # Options yt-dlp utilisées partout
    ydl_options = {
        "format": "bestaudio[abr<=96]/bestaudio",
        "noplaylist": True,
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
    }

    guild_id = str(interaction.guild_id)
    if SONG_QUEUES.get(guild_id) is None:
        SONG_QUEUES[guild_id] = deque()

    # --- SPOTIFY PLAYLIST DETECTION ---
    if re.match(SPOTIFY_PLAYLIST_REGEX, song_query):

        token = get_spotify_token(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
        tracks = get_spotify_playlist_tracks(song_query, token)

        if not tracks:
            await interaction.followup.send("Impossible de lire la playlist Spotify.")
            return

        guild_id = str(interaction.guild_id)
        if SONG_QUEUES.get(guild_id) is None:
            SONG_QUEUES[guild_id] = deque()

        # 1) On traite immédiatement la première musique
        first_title = tracks[0]
        query = "ytsearch1: " + first_title
        results = await search_ytdlp_async(query, ydl_options)
        entries = results.get("entries", [])

        if not entries:
            await interaction.followup.send("Impossible de lire la première musique de la playlist.")
            return

        first = entries[0]
        SONG_QUEUES[guild_id].append((first["url"], first.get("title", first_title)))

        await interaction.followup.send(
            f"Lecture de la playlist… Première musique : **{first.get('title', first_title)}**")

        # 2) On lance la lecture immédiatement
        if not voice_client.is_playing() and not voice_client.is_paused():
            await play_next_song(voice_client, guild_id, interaction.channel)

        # 3) On remplit le reste de la playlist en arrière-plan
        remaining_tracks = tracks[1:]
        asyncio.create_task(fill_queue_background(remaining_tracks, guild_id, ydl_options))

        return

    # ---------- SPOTIFY TRACK ----------
    if re.match(SPOTIFY_TRACK_REGEX, song_query):
        try:
            track_title = get_spotify_track_info(song_query)
            song_query = track_title
            print(f"Spotify detected → Searching YouTube for: {track_title}")
        except Exception as e:
            print("Spotify track error:", e)
            await interaction.followup.send("Error reading Spotify link.")
            return

    # ---------- Recherche YouTube classique ----------
    query = "ytsearch1: " + song_query
    results = await search_ytdlp_async(query, ydl_options)
    entries = results.get("entries", [])

    if not entries:
        await interaction.followup.send("No results found.")
        return

    first_track = entries[0]
    audio_url = first_track["url"]
    title = first_track.get("title", "Untitled")

    SONG_QUEUES[guild_id].append((audio_url, title))

    if voice_client.is_playing() or voice_client.is_paused():
        await interaction.followup.send(f"Added to queue: **{title}**")
    else:
        await interaction.followup.send(f"Now playing: **{title}**")
        await play_next_song(voice_client, guild_id, interaction.channel)


# ---------- Arriere plan -------------

async def fill_queue_background(tracks, guild_id, ydl_options):
    for track_title in tracks:
        query = "ytsearch1: " + track_title
        results = await search_ytdlp_async(query, ydl_options)
        entries = results.get("entries", [])
        if entries:
            first = entries[0]
            SONG_QUEUES[guild_id].append((first["url"], first.get("title", track_title)))


# ---------- Lecture de la queue ----------

async def play_next_song(voice_client, guild_id, channel):
    if SONG_QUEUES[guild_id]:
        audio_url, title = SONG_QUEUES[guild_id].popleft()

        ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn -c:a libopus -b:a 96k",
        }

        source = discord.FFmpegOpusAudio(
            audio_url,
            **ffmpeg_options,
            executable="bin\\ffmpeg\\ffmpeg.exe",
        )

        def after_play(error):
            if error:
                print(f"Error playing {title}: {error}")
            asyncio.run_coroutine_threadsafe(
                play_next_song(voice_client, guild_id, channel),
                bot.loop
            )

        voice_client.play(source, after=after_play)
        asyncio.create_task(channel.send(f"Now playing: **{title}**"))
    else:
        await voice_client.disconnect()
        SONG_QUEUES[guild_id] = deque()


print(f"TOKEN = {TOKEN}")

# Run the bot
bot.run(TOKEN)

