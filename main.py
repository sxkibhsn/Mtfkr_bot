import os
import discord
from discord import app_commands
from discord.ext import commands
import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv
from flask import Flask
from threading import Thread
# Load environment variables
load_dotenv()
# --- GOOGLE SHEET SETUP ---
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_name(
    "credentials.json", scope)
sheet_client = gspread.authorize(creds)
sheet = sheet_client.open("MTFKR Attendance").sheet1  # Change if needed

# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

active_attendance_channels = set()


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await tree.sync()
        print(f"✅ Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")


# --- Enable attendance when bot is mentioned ---
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if bot.user in message.mentions:
        if message.author.guild_permissions.administrator:
            active_attendance_channels.add(message.channel.id)
            await message.channel.send(
                "✅ Attendance activated in this channel.")
        else:
            await message.channel.send(
                "⛔ You must be an admin to activate attendance.")

    await bot.process_commands(message)


# --- /party Slash Command ---
@tree.command(name="party", description="Check in for event participation.")
@app_commands.describe(
    image1="Upload party screenshot (required)",
    name1="Mention 1st member",
    name2="Mention 2nd member",
    name3="Mention 3rd member",
    name4="Mention 4th member",
    name5="Mention 5th member",
    name6="Mention 6th member",
)
async def party(interaction: discord.Interaction,
                image1: discord.Attachment,
                name1: discord.Member,
                name2: discord.Member = None,
                name3: discord.Member = None,
                name4: discord.Member = None,
                name5: discord.Member = None,
                name6: discord.Member = None):
    # Attendance must be activated
    if interaction.channel.id not in active_attendance_channels:
        await interaction.response.send_message(
            "⚠️ Attendance is not active in this channel.", ephemeral=True)
        return

    await interaction.response.defer()  # Prevent timeout

    author = interaction.user.display_name
    timestamp = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    event_name = interaction.channel.name
    image_url = image1.url
    file_name = image1.filename

    # Collect valid member mentions
    members = [
        name for name in [name1, name2, name3, name4, name5, name6]
        if name is not None
    ]
    if not members:
        await interaction.followup.send(
            "❌ You must mention at least 1 member.", ephemeral=True)
        return

    # Fetch all existing rows for this event to detect duplicates
    existing_records = sheet.get_all_records()
    already_recorded = {
        row["Name"] + "|" + row["Party"]
        for row in existing_records if row["Name"] == event_name
    }

    summary_lines = []
    for member in members:
        key = f"{event_name}|{member.display_name}"
        if key in already_recorded:
            summary_lines.append(
                f"**{member.display_name}** - Duplicate Member")
        else:
            # Append to Google Sheet
            sheet.append_row([
                timestamp, author, member.display_name, image_url, event_name
            ])
            summary_lines.append(f"**{member.display_name}**")

    summary = (f"**Name:** {event_name}\n"
               f"**Party:**\n" + "\n".join(summary_lines) + "\n"
               f"**Images:** [📎 {file_name}]({image_url})")

    await interaction.followup.send(summary)


# --- /attendance_percent Slash Command --- 
@tree.command(name="attendance_percent", description="Show a member's attendance percentage.")
@app_commands.describe(member="Select the member to check attendance for.")
async def attendance_percent(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()

    try:
        records = sheet.get_all_records()
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to read sheet: {e}", ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    fifteen_days_ago = now - datetime.timedelta(days=15)
    thirty_days_ago = now - datetime.timedelta(days=30)

    all_events = set()
    member_events = set()
    last_15_days = set()
    last_30_days = set()

    for row in records:
        event = row.get("Event")
        attendee = row.get("Member")
        timestamp_str = row.get("Timestamp")

        if event and attendee:
            all_events.add(event)
            if attendee == member.display_name:
                member_events.add(event)
                try:
                    timestamp = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                    
                    # Attendance for the last 15 days
                    if timestamp >= fifteen_days_ago:
                        last_15_days.add(event)

                    # Attendance for the last 30 days
                    if timestamp >= thirty_days_ago:
                        last_30_days.add(event)

                except Exception as e:
                    print(f"Skipping row with bad timestamp: {timestamp_str} - {e}")

    total_events = len(all_events)
    attended = len(member_events)
    attended_15 = len(last_15_days)
    attended_30 = len(last_30_days)

    if total_events == 0:
        await interaction.followup.send("⚠️ No events found in the sheet.", ephemeral=True)
        return

    percent = (attended / total_events) * 100
    percent_15 = (attended_15 / total_events) * 100
    percent_30 = (attended_30 / total_events) * 100

    summary = (f"📊 **Attendance Summary for {member.display_name}**\n"
               f"✅ Attended: {attended} event(s)\n"
               f"📅 Total Events: {total_events}\n"
               f"📈 Attendance Rate (Overall): {percent:.2f}%\n"
               f"📅 Last 15 Days: {percent_15:.2f}%\n"
               f"📅 Last 30 Days: {percent_30:.2f}%")

    await interaction.followup.send(summary)



# --- /attendance_stats Slash Command ---
@tree.command(name="attendance_stats",
              description="Show a member's attendance over time.")
@app_commands.describe(member="Select the member to check stats for.")
async def attendance_stats(interaction: discord.Interaction,
                           member: discord.Member):
    await interaction.response.defer()

    try:
        records = sheet.get_all_records()
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to read sheet: {e}",
                                        ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    fifteen_days_ago = now - datetime.timedelta(days=15)
    current_month = now.month
    current_year = now.year

    all_events = set()
    total_attended = set()
    last_15_days = set()
    current_month_events = set()

    for row in records:
        event = row.get("Event")
        attendee = row.get("Member")
        timestamp_str = row.get("Timestamp")

        if not (event and attendee and timestamp_str):
            continue

        all_events.add(event)

        if attendee == member.display_name:
            total_attended.add(event)
            try:
                timestamp = datetime.datetime.strptime(timestamp_str,
                                                       "%Y-%m-%d %H:%M:%S")

                if timestamp >= fifteen_days_ago:
                    last_15_days.add(event)

                if timestamp.month == current_month and timestamp.year == current_year:
                    current_month_events.add(event)

            except Exception as e:
                print(
                    f"Skipping row with bad timestamp: {timestamp_str} - {e}")

    total_events = len(all_events)
    stats = {
        "Total Attendance": len(total_attended),
        "Last 15 Days": len(last_15_days),
        "This Month": len(current_month_events),
    }

    summary = (f"📊 **Detailed Attendance for {member.display_name}**\n"
               f"📅 **Total Events:** {total_events}\n"
               f"✅ **Attended:** {stats['Total Attendance']}\n"
               f"🕒 **Last 15 Days:** {stats['Last 15 Days']} event(s)\n"
               f"🗓️ **This Month:** {stats['This Month']} event(s)")

    await interaction.followup.send(summary)


# --- /leaderboard Slash Command --- 
@tree.command(name="leaderboard", description="Show attendance percentage for all members.")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        records = sheet.get_all_records()
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to read sheet: {e}", ephemeral=True)
        return

    now = datetime.datetime.utcnow()
    thirty_days_ago = now - datetime.timedelta(days=30)

    event_set = set()
    member_attendance = {}

    for row in records:
        member = row.get("Member")
        event = row.get("Event")
        timestamp_str = row.get("Timestamp")

        if not (member and event and timestamp_str):
            continue

        event_set.add(event)

        # Only count attendance for events in the last 30 days
        try:
            timestamp = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            if timestamp >= thirty_days_ago:
                if member not in member_attendance:
                    member_attendance[member] = set()
                member_attendance[member].add(event)
        except Exception as e:
            print(f"Skipping row with bad timestamp: {timestamp_str} - {e}")

    total_events = len(event_set)

    if total_events == 0:
        await interaction.followup.send("⚠️ No events found in the sheet.")
        return

    leaderboard_data = []
    for member, events_attended in member_attendance.items():
        percent = (len(events_attended) / total_events) * 100
        leaderboard_data.append((member, percent))

    sorted_board = sorted(leaderboard_data, key=lambda x: x[1], reverse=True)

    leaderboard_lines = []
    for i, (member, percent) in enumerate(sorted_board, start=1):
        leaderboard_lines.append(f"**{i}. {member}** — {percent:.2f}%")

    leaderboard_text = "\n".join(leaderboard_lines)
    await interaction.followup.send(
        f"🏆 **Attendance Leaderboard (Last 30 Days)**\n\n{leaderboard_text}")


# --- Flask Keep-Alive Server ---

app = Flask(__name__)
@app.route('/')

def keep_alive():

    return "BFLxMain2.1 - I'm up baby!", 200

def run():

    app.run(host='0.0.0.0', port=8080)

# Run the Flask app in a separate thread
Thread(target=run).start()

# --- Run the Discord Bot ---

# --- Run the Bot ---
bot_token = os.getenv('DISCORD_TOKEN') 
    
bot.run(bot_token)  # Starts the bot

