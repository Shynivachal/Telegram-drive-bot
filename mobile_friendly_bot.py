#!/usr/bin/env python3

import os
import logging
import json
import io
import tempfile

from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get configuration from environment variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
AUTHORIZED_USERS_STR = os.environ.get("AUTHORIZED_USERS", "")
AUTHORIZED_USERS = [int(user_id) for user_id in AUTHORIZED_USERS_STR.split(",") if user_id]
SCOPES = ['https://www.googleapis.com/auth/drive']

# Flask app for health checks and webhook
app = Flask(__name__)

@app.route("/")
def health_check():
    return "🤖 Telegram Bot is running! ✅"

@app.route("/health")
def health():
    return {"status": "healthy", "bot": "running"}

def get_drive_service():
    try:
        service_account_info = os.environ.get("GOOGLE_SERVICE_ACCOUNT")
        if not service_account_info:
            raise Exception("GOOGLE_SERVICE_ACCOUNT environment variable not found")
        service_account_data = json.loads(service_account_info)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_data,
            scopes=SCOPES
        )
        return build('drive', 'v3', credentials=credentials)
    except Exception as e:
        logger.error(f"Failed to authenticate with Google Drive: {str(e)}")
        raise

async def check_authorization(update: Update):
    user_id = update.effective_user.id
    if not AUTHORIZED_USERS:
        return True
    if user_id in AUTHORIZED_USERS:
        return True
    else:
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return False

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_authorization(update):
        return
    await update.message.reply_text(
        "🚀 **Welcome to Telegram to Google Drive Bot!**\n\n"
        "📁 Send me any file and I'll upload it to your Google Drive\n"
        "💾 I can handle files up to 6GB in size\n"
        "📊 You'll see progress updates during upload\n\n"
        "Use /help to see all available commands."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_authorization(update):
        return
    await update.message.reply_text(
        "📖 **Available Commands:**\n\n"
        "🏁 /start - Start the bot\n"
        "❓ /help - Show this help message\n"
        "📊 /status - Check bot and Drive status\n\n"
        "📤 **To upload files:**\n"
        "Just send me any file and I'll upload it to Google Drive automatically!"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_authorization(update):
        return
    status_message = "🤖 **Bot Status Report:**\n\n"
    status_message += "✅ Bot is running smoothly\n"
    try:
        drive_service = get_drive_service()
        drive_about = drive_service.about().get(fields="storageQuota").execute()
        quota = drive_about.get('storageQuota', {})
        used = int(quota.get('usage', 0)) / (1024 ** 3)
        total = int(quota.get('limit', 0)) / (1024 ** 3)
        status_message += f"✅ Google Drive connected successfully\n"
        status_message += f"💾 Storage used: {used:.2f}GB"
        if total > 0:
            status_message += f" of {total:.2f}GB"
            percentage = (used / total) * 100
            status_message += f" ({percentage:.1f}%)"
    except Exception as e:
        status_message += f"❌ Google Drive connection error:\n`{str(e)}`"
    await update.message.reply_text(status_message)

def create_progress_bar(progress, width=20):
    filled = int(width * progress / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {progress}%"

def format_file_size(size_bytes):
    if size_bytes >= 1024**3:
        return f"{size_bytes / (1024**3):.2f}GB"
    elif size_bytes >= 1024**2:
        return f"{size_bytes / (1024**2):.2f}MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f}KB"
    else:
        return f"{size_bytes}B"

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_authorization(update):
        return
    if not update.message.document:
        await update.message.reply_text("❌ Please send a file document.")
        return
    file = update.message.document
    file_name = file.file_name or "unnamed_file"
    file_size = file.file_size
    file_size_formatted = format_file_size(file_size)
    max_size = 6 * 1024 * 1024 * 1024
    if file_size > max_size:
        await update.message.reply_text(
            f"❌ File too large!\n\n"
            f"📁 File: {file_name}\n"
            f"📏 Size: {file_size_formatted}\n"
            f"🚫 Maximum allowed: 6GB\n\n"
            f"Please send a smaller file."
        )
        return
    await update.message.reply_text(
        f"📥 **File Received!**\n\n"
        f"📁 Name: `{file_name}`\n"
        f"📏 Size: {file_size_formatted}\n\n"
        f"⏳ Starting download from Telegram..."
    )
    try:
        status_message = await update.message.reply_text("📥 Preparing download...")
        telegram_file = await context.bot.get_file(file.file_id)
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file_path = temp_file.name
        chunk_size = 1 * 1024 * 1024
        downloaded_size = 0
        last_progress_update = -1
        with open(temp_file_path, 'wb') as f:
            async for chunk in telegram_file.download_chunk(chunk_size=chunk_size):
                f.write(chunk)
                downloaded_size += len(chunk)
                progress = min(int((downloaded_size / file_size) * 100), 100)
                if progress - last_progress_update >= 3:
                    progress_bar = create_progress_bar(progress)
                    downloaded_formatted = format_file_size(downloaded_size)
                    await status_message.edit_text(
                        f"📥 **Downloading from Telegram**\n\n"
                        f"{progress_bar}\n\n"
                        f"📊 Progress: {downloaded_formatted} / {file_size_formatted}\n"
                        f"⚡ Status: {progress}% complete"
                    )
                    last_progress_update = progress
        await status_message.edit_text(
            f"✅ **Download Complete!**\n\n"
            f"☁️ Starting upload to Google Drive...\n"
            f"📁 File: {file_name}"
        )
        try:
            drive_service = get_drive_service()
            file_metadata = {
                'name': file_name,
                'parents': [GOOGLE_DRIVE_FOLDER_ID] if GOOGLE_DRIVE_FOLDER_ID else []
            }
            chunk_size_upload = 2 * 1024 * 1024
            media = MediaIoBaseUpload(
                io.FileIO(temp_file_path, 'rb'),
                mimetype='application/octet-stream',
                chunksize=chunk_size_upload,
                resumable=True
            )
            request = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id,webViewLink,size'
            )
            response = None
            last_upload_progress = -1
            while response is None:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    uploaded_size = int(file_size * status.progress())
                    if progress - last_upload_progress >= 5:
                        progress_bar = create_progress_bar(progress)
                        uploaded_formatted = format_file_size(uploaded_size)
                        await status_message.edit_text(
                            f"☁️ **Uploading to Google Drive**\n\n"
                            f"{progress_bar}\n\n"
                            f"📊 Progress: {uploaded_formatted} / {file_size_formatted}\n"
                            f"⚡ Status: {progress}% complete\n"
                            f"📁 File: {file_name}"
                        )
                        last_upload_progress = progress
            file_id = response.get('id')
            file_link = response.get('webViewLink', 'Link not available')
            await status_message.edit_text(
                f"🎉 **Upload Successful!**\n\n"
                f"📁 **File:** `{file_name}`\n"
                f"📏 **Size:** {file_size_formatted}\n"
                f"🆔 **Drive ID:** `{file_id}`\n\n"
                f"🔗 **[Open in Google Drive]({file_link})**\n\n"
                f"✅ Your file is now safely stored in Google Drive!"
            )
        except HttpError as error:
            error_msg = str(error)
            await status_message.edit_text(
                f"❌ **Google Drive Upload Failed**\n\n"
                f"📁 File: {file_name}\n"
                f"🚫 Error: {error_msg}\n\n"
                f"Please try again or contact support."
            )
            logger.error(f"Google Drive upload error: {error_msg}")
        finally:
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            logger.info(f"Cleaned up temporary file: {temp_file_path}")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error handling file: {error_msg}")
        await update.message.reply_text(
            f"❌ **Error Processing File**\n\n"
            f"📁 File: {file_name}\n"
            f"🚫 Error: {error_msg}\n\n"
            f"Please try again or send a different file."
        )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_authorization(update):
        return
    await update.message.reply_text(
        "👋 Hello! I'm ready to help you upload files to Google Drive.\n\n"
        "📤 **To upload a file:**\n"
        "Simply send me any document, video, photo, or file!\n\n"
        "❓ **Need help?** Use /help to see all commands."
    )

# --- Telegram Bot Setup ---
application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start_command))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("status", status_command))
application.add_handler(MessageHandler(filters.Document.ALL, handle_file))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

@app.route(f"/webhook/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "OK"

@app.before_first_request
def set_webhook():
    public_url = f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}/webhook/{TELEGRAM_BOT_TOKEN}"
    application.bot.set_webhook(public_url)
    logger.info(f"Webhook set to: {public_url}")

if __name__ == "__main__":
    PORT = int(os.environ.get('PORT', 8000))
    app.run(host="0.0.0.0", port=PORT)
        
