#!/usr/bin/env python3
# Telegram to Google Drive Upload Bot (Render-Compatible Version)
# This version is optimized for Render deployment

import os
import logging
import json
import io
import tempfile
import threading
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError
from flask import Flask

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get configuration from environment variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")

# Parse authorized users from environment variable (comma-separated list)
AUTHORIZED_USERS_STR = os.environ.get("AUTHORIZED_USERS", "")
AUTHORIZED_USERS = [int(user_id) for user_id in AUTHORIZED_USERS_STR.split(",") if user_id]

# Google Drive scopes
SCOPES = ['https://www.googleapis.com/auth/drive']

# Create Flask app for health checks (required by Render)
app = Flask(__name__)

@app.route('/')
def health_check():
    return "🤖 Telegram Bot is running! ✅"

@app.route('/health')
def health():
    return {"status": "healthy", "bot": "running"}

# Google Drive Authentication using Service Account
def get_drive_service():
    """Get authenticated Google Drive service using Service Account."""
    try:
        # Get service account credentials from environment variable
        service_account_info = os.environ.get("GOOGLE_SERVICE_ACCOUNT")
        
        if not service_account_info:
            raise Exception("GOOGLE_SERVICE_ACCOUNT environment variable not found")
        
        # Parse the JSON credentials
        service_account_data = json.loads(service_account_info)
        
        # Create credentials from service account info
        credentials = service_account.Credentials.from_service_account_info(
            service_account_data, 
            scopes=SCOPES
        )
        
        # Build and return the Drive service
        return build('drive', 'v3', credentials=credentials)
        
    except Exception as e:
        logger.error(f"Failed to authenticate with Google Drive: {str(e)}")
        raise

# Helper function to check if user is authorized
async def check_authorization(update: Update):
    """Check if the user is authorized to use this bot."""
    user_id = update.effective_user.id
    
    # If no authorized users are specified, allow everyone
    if not AUTHORIZED_USERS:
        return True
        
    if user_id in AUTHORIZED_USERS:
        return True
    else:
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return False

# Command handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a welcome message when the command /start is issued."""
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
    """Send a help message when the command /help is issued."""
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
    """Check the status of the bot and Google Drive connection."""
    if not await check_authorization(update):
        return
    
    status_message = "🤖 **Bot Status Report:**\n\n"
    status_message += "✅ Bot is running smoothly\n"
    
    try:
        drive_service = get_drive_service()
        drive_about = drive_service.about().get(fields="storageQuota").execute()
        quota = drive_about.get('storageQuota', {})
        used = int(quota.get('usage', 0)) / (1024 ** 3)  # Convert to GB
        total = int(quota.get('limit', 0)) / (1024 ** 3)  # Convert to GB
        
        status_message += f"✅ Google Drive connected successfully\n"
        status_message += f"💾 Storage used: {used:.2f}GB"
        
        if total > 0:
            status_message += f" of {total:.2f}GB"
            percentage = (used / total) * 100
            status_message += f" ({percentage:.1f}%)"
            
    except Exception as e:
        status_message += f"❌ Google Drive connection error:\n`{str(e)}`"
    
    await update.message.reply_text(status_message)

# Enhanced progress bar function
def create_progress_bar(progress, width=20):
    """Create a visual progress bar."""
    filled = int(width * progress / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {progress}%"

def format_file_size(size_bytes):
    """Format file size in human readable format."""
    if size_bytes >= 1024**3:  # GB
        return f"{size_bytes / (1024**3):.2f}GB"
    elif size_bytes >= 1024**2:  # MB
        return f"{size_bytes / (1024**2):.2f}MB"
    elif size_bytes >= 1024:  # KB
        return f"{size_bytes / 1024:.2f}KB"
    else:
        return f"{size_bytes}B"

# File upload handler
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file uploads from users."""
    if not await check_authorization(update):
        return
    
    # Check if message contains a document
    if not update.message.document:
        await update.message.reply_text("❌ Please send a file document.")
        return
    
    file = update.message.document
    file_name = file.file_name or "unnamed_file"
    file_size = file.file_size
    file_size_formatted = format_file_size(file_size)
    
    # Check file size limit (6GB = 6,442,450,944 bytes)
    max_size = 6 * 1024 * 1024 * 1024  # 6GB in bytes
    if file_size > max_size:
        await update.message.reply_text(
            f"❌ File too large!\n\n"
            f"📁 File: {file_name}\n"
            f"📏 Size: {file_size_formatted}\n"
            f"🚫 Maximum allowed: 6GB\n\n"
            f"Please send a smaller file."
        )
        return
    
    # Initial message
    await update.message.reply_text(
        f"📥 **File Received!**\n\n"
        f"📁 Name: `{file_name}`\n"
        f"📏 Size: {file_size_formatted}\n\n"
        f"⏳ Starting download from Telegram..."
    )
    
    # Download the file from Telegram
    try:
        status_message = await update.message.reply_text("📥 Preparing download...")
        telegram_file = await context.bot.get_file(file.file_id)
        
        # Create a temporary file to store the download
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file_path = temp_file.name
        
        # Download in chunks with progress tracking
        chunk_size = 1 * 1024 * 1024  # 1MB chunks for better progress updates
        downloaded_size = 0
        last_progress_update = -1
        
        with open(temp_file_path, 'wb') as f:
            async for chunk in telegram_file.download_chunk(chunk_size=chunk_size):
                f.write(chunk)
                downloaded_size += len(chunk)
                progress = min(int((downloaded_size / file_size) * 100), 100)
                
                # Update progress every 3% to avoid rate limiting
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
        
        # Start Google Drive upload
        await status_message.edit_text(
            f"✅ **Download Complete!**\n\n"
            f"☁️ Starting upload to Google Drive...\n"
            f"📁 File: {file_name}"
        )
        
        # Upload to Google Drive
        try:
            drive_service = get_drive_service()
            
            # File metadata for Google Drive
            file_metadata = {
                'name': file_name,
                'parents': [GOOGLE_DRIVE_FOLDER_ID] if GOOGLE_DRIVE_FOLDER_ID else []
            }
            
            # Upload with progress tracking
            chunk_size_upload = 2 * 1024 * 1024  # 2MB chunks for upload
            
            with open(temp_file_path, 'rb') as f:
                media = MediaIoBaseUpload(
                    io.FileIO(temp_file_path, 'rb'),
                    mimetype='application/octet-stream',
                    chunksize=chunk_size_upload,
                    resumable=True
                )
                
                # Create the upload request
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
                        
                        # Update every 5% for uploads
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
                
                # Upload completed successfully
                file_id = response.get('id')
                file_link = response.get('webViewLink', 'Link not available')
                
                # Final success message
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
            # Clean up the temporary file
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
    """Handle text messages."""
    if not await check_authorization(update):
        return
        
    await update.message.reply_text(
        "👋 Hello! I'm ready to help you upload files to Google Drive.\n\n"
        "📤 **To upload a file:**\n"
        "Simply send me any document, video, photo, or file!\n\n"
        "❓ **Need help?** Use /help to see all commands."
    )

def run_bot():
    """Run the Telegram bot in a separate thread."""
    # Verify environment variables
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("❌ TELEGRAM_BOT_TOKEN environment variable is not set")
    
    if not GOOGLE_DRIVE_FOLDER_ID:
        logger.warning("⚠️  GOOGLE_DRIVE_FOLDER_ID not set - files will be uploaded to root folder")
    
    if not os.environ.get("GOOGLE_SERVICE_ACCOUNT"):
        raise ValueError("❌ GOOGLE_SERVICE_ACCOUNT environment variable is not set")
    
    logger.info("🚀 Starting Telegram to Google Drive Upload Bot...")
    
    # Create the Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    
    # Add message handlers
    application.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    # Start the Bot
    logger.info("✅ Bot is now running and waiting for files...")
    application.run_polling()

def main():
    """Main function to run both Flask and Telegram bot."""
    # Start the Telegram bot in a separate thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Start Flask app for Render health checks
    PORT = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=PORT, debug=False)

if __name__ == '__main__':
    main()