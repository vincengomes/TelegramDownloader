import os
import json
import asyncio
import logging
import shutil
import aiofiles
import humanize
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict
from dataclasses import dataclass
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename, Message
from dotenv import load_dotenv


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('telegram_downloader.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Define required environment variables with validation
REQUIRED_ENV_VARS = {
    'TELEGRAM_API_ID': {
        'description': 'Telegram API ID',
        'validator': lambda x: x.isdigit(),  # Must be a number
        'error_msg': 'must be a numeric value'
    },
    'DOWNLOAD_PATH': {
        'description': 'Download Path',
        'validator': lambda x: Path(x).parent.exists(),  # Parent directory must exist
        'error_msg': 'parent directory must exist'
    },
    'MOVE_PATH': {
        'description': 'Move Path',
        'validator': lambda x: Path(x).parent.exists(),  # Parent directory must exist
        'error_msg': 'parent directory must exist'
    },
    'TELEGRAM_API_HASH': {
        'description': 'Telegram API Hash',
        'validator': lambda x: len(x) == 32 and all(c in '0123456789abcdefABCDEF' for c in x),
        'error_msg': 'must be a 32-character hexadecimal string'
    },
    'MKVPROPEDIT_PATH': {
        'description': 'Location of MKV TOOL NIX and its subprocess MKVPROPEDIT for removing the Tags from MKV Files',
        'validator': lambda x: Path(x).exists(),
        'error_msg': 'path does not exist. Please check the availability of MKV TOOL NIX'
    },
    'TELEGRAM_BOT_TOKEN': {
        'description': 'Telegram Bot Token',
        'validator': lambda x: ':' in x and x.split(':')[0].isdigit(),
        'error_msg': 'must be in valid bot token format (number:string)'
    }
}

# Check all required variables and store them in CONFIG
CONFIG = {

    'HISTORY_FILE': 'download_history.json',
    'CHUNK_SIZE': 2 * 1024 * 1024,  # 1MB chunks for progress updates
    'PROGRESS_UPDATE_INTERVAL': 115,  # seconds between progress updates
    'MAX_FILE_SIZE': 6 * 1024 * 1024 * 1024, # 6 GB
    'CONCURRENT_DOWNLOADS': 3, # No downloads that should be done at the Same Time
    'MAX_RETRIES': 5, # Number of Attempts to be done to download the file before declaring it is not possible.
    'MAX_QUEUE_SIZE' :50,  # Max No of Files to be kept in the Queue for Downloading.
}

env_errors = []  # Rename the list

for env_var, settings in REQUIRED_ENV_VARS.items():
    value = os.getenv(env_var)

    if not value:
        env_errors.append(f"{settings['description']} ({env_var}) is not set")
        continue

    if 'validator' in settings and not settings['validator'](value):
        env_errors.append(f"{settings['description']} ({env_var}) {settings['error_msg']}")
        continue

    CONFIG[env_var] = value

if env_errors:
    logger.error("Environment variable errors:")
    for error in env_errors:
        logger.error(f"- {error}")
    raise ValueError("Invalid environment configuration")


class BotLogger:
    def __init__(self, log_instance, message=None):
        self.logger = log_instance
        self.message = message

    async def info(self, text: str):
        """Log info message to both logger and bot if message object exists"""
        self.logger.info(text)
        if self.message:
            await self.message.reply(text)

    async def error(self, text: str):
        """Log error message to both logger and bot if message object exists"""
        self.logger.error(text)
        if self.message:
            await self.message.reply(f"❌ Error: {text}")

    async def warning(self, text: str):
        """Log warning message to both logger and bot if message object exists"""
        self.logger.warning(text)
        if self.message:
            await self.message.reply(f"⚠️ Warning: {text}")

    async def success(self, text: str):
        """Log success message to both logger and bot if message object exists"""
        self.logger.info(text)
        if self.message:
            await self.message.reply(f"✅ Success: {text}")


@dataclass
class DownloadStats:
    total_downloads: int = 0
    failed_downloads: int = 0
    total_bytes: int = 0
    start_time: datetime = datetime.now()


@dataclass
class DownloadTask:
    message: Message
    file_name: str
    file_size: int
    queued_message: Optional[Message] = None
    start_message: Optional[Message] = None
    progress_message: Optional[Message] = None
    bot_logger: Optional[BotLogger] = None
    start_time: datetime = None
    download_speed: float = 0
    eta: timedelta = None
    retries: int = 0
    max_retries: int = CONFIG['MAX_RETRIES']
    current: int = 0
    total: int = 0
    percentage_completed: float = 0.00
    mb_downloaded: float = 0.00
    mb_total: float = 0.00


class TelegramDownloader:
    def __init__(self):
        self.client = None
        self.download_history = self.load_history()
        self.is_downloading = False
        self.stats = DownloadStats()
        self.concurrent_downloads = CONFIG['CONCURRENT_DOWNLOADS']  # Configure number of concurrent downloads
        self.downloads_in_progress: Dict[str, DownloadTask] = {}
        self.download_semaphore = asyncio.Semaphore(self.concurrent_downloads)
        self.max_queue_size = CONFIG['MAX_QUEUE_SIZE']  # Maximum queue size
        self.download_queue = asyncio.Queue(maxsize=self.max_queue_size)  # Changed from Deque to asyncio.Queue
        self.last_backup = datetime.now()
        self.backup_interval = timedelta(hours=1)

    def load_history(self) -> dict:
        try:
            with open(CONFIG['HISTORY_FILE'], 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    async def save_history(self):
        async with aiofiles.open(CONFIG['HISTORY_FILE'], 'w') as f:
            await f.write(json.dumps(self.download_history))

    async def process_download_queue(self):
        while True:  # Run indefinitely
            task = await self.download_queue.get()
            async with self.download_semaphore:
                await self.start_download_task(task)
            self.download_queue.task_done()

    async def start_download_task(self, task: DownloadTask):
        """Starts and manages a single download task."""
        try:
            success, download_path = await self.handle_download(task)
            if success:
                message_id = str(task.message.id)
                self.download_history[message_id] = {
                    'filename': task.file_name,
                    'download_time': datetime.now().isoformat(),
                    'file_size': task.file_size
                }
                await self.save_history()
        except Exception as e:
            logger.error(f"Error processing download task: {str(e)}")
            await task.message.reply(f"❌ Error processing file: {str(e)}")

    async def progress_callback(self, current, total, task: DownloadTask):
        try:
            if not hasattr(task, 'last_progress_time'):
                task.last_progress_time = 0

            now = datetime.now().timestamp()
            if now - task.last_progress_time < CONFIG['PROGRESS_UPDATE_INTERVAL']:
                return

            task.last_progress_time = now
            task.mb_downloaded = current / (1024 * 1024)  # Update the downloaded size in MB
            task.mb_total = total / (1024 * 1024)  # Update the total size in MB
            task.percentage_completed = (current / total) * 100

            # percentage = (current / total) * 100
            # mb_downloaded = current / (1024 * 1024)
            # mb_total = total / (1024 * 1024)

            current_time = datetime.now().strftime("%H:%M:%S")
            current_date = datetime.now().strftime("%d/%b/%Y")

            # progress_text = f"""
            # Downloading: {task.file_name}
            # Progress: {task.percentage_completed:.2f}%
            # Downloaded: {task.mb_downloaded:.2f}MB of {task.mb_total:.2f}MB
            # Time of Update: {current_time} on {current_date}
            # """

            progress_text = (
                f"Downloading: {task.file_name}\n"
                f"Progress: {task.percentage_completed:.2f}%\n"
                f"Downloaded: {task.mb_downloaded:.2f}MB of {task.mb_total:.2f}MB\n"
                f"Time of Update: {current_time} on {current_date}\n"
                f"\n"  # Line break after the group
            )

            if task.progress_message:
                await task.progress_message.edit(progress_text)
            else:
                task.progress_message = await task.message.reply(progress_text)

        except Exception as e:
            logger.error(f"Error in progress callback: {str(e)}")

    async def calculate_download_speed(self, current: int, total: int, task: DownloadTask) -> float:
        """Calculate download speed and ETA."""
        if not task.start_time:
            task.start_time = datetime.now()
            return 0, None

        elapsed_time = (datetime.now() - task.start_time).total_seconds()
        if elapsed_time == 0:
            return 0, None

        speed = current / elapsed_time  # bytes per second
        remaining_bytes = total - current
        eta = timedelta(seconds=int(remaining_bytes / speed)) if speed > 0 else None

        return speed, eta

    async def backup_history(self):
        """Backup download history periodically."""
        if datetime.now() - self.last_backup >= self.backup_interval:
            backup_file = f"download_history_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            async with aiofiles.open(backup_file, 'w') as f:
                await f.write(json.dumps(self.download_history, indent=2))
            self.last_backup = datetime.now()

    async def handle_download(self, task: DownloadTask):
        task.bot_logger = BotLogger(logger, task.message)
        task.start_time = datetime.now()
        self.downloads_in_progress[str(task.message.id)] = task

        try:
            success, download_path = await self.perform_download(task)
            if success:
                await self.process_successful_download(task, download_path)
            else:
                await self._handle_failed_download(task)

            return success, download_path

        finally:
            self.downloads_in_progress.pop(str(task.message.id), None)
            await self.backup_history()

    async def perform_download(self, task: DownloadTask) -> tuple[bool, Optional[str]]:
        """Handle the actual download process with retries."""
        # removed --> async with self.download_semaphore:
        for attempt in range(task.max_retries):
            try:
                download_path = Path(CONFIG['DOWNLOAD_PATH']) / task.file_name
                temp_path = f"{download_path}.part"

                await task.bot_logger.info(f"Download attempt {attempt + 1}/{task.max_retries}")

                async def progress_callback(current, total):
                    speed, eta = await self.calculate_download_speed(current, total, task)
                    task.download_speed = speed
                    task.eta = eta
                    await self.progress_callback(current, total, task)

                await asyncio.wait_for(
                    task.message.download_media(
                        temp_path,
                        progress_callback=progress_callback,
                        # part_size_kb=CONFIG['CHUNK_SIZE']  # Use your config value
                    ),
                    timeout=42 * 3600
                )

                if Path(temp_path).exists():
                    # Verify file integrity
                    if await self.verify_file(temp_path, task.file_size):
                        Path(temp_path).replace(download_path)
                        return True, download_path
                    else:
                        raise ValueError("File integrity check failed")

            except asyncio.TimeoutError:
                await task.bot_logger.error(f"Download timed out (attempt {attempt + 1})")
            except Exception as e:
                await task.bot_logger.error(f"Download failed (attempt {attempt + 1}): {str(e)}")

            task.retries += 1
            if attempt < task.max_retries - 1:
                await asyncio.sleep(5 * (attempt + 1))  # Exponential backoff

            if Path(temp_path).exists():
                Path(temp_path).unlink()

        return False, None

    async def verify_file(self, file_path: str, expected_size: int) -> bool:
        """Verify downloaded file integrity."""
        try:
            actual_size = Path(file_path).stat().st_size
            if actual_size != expected_size:
                return False

            # Optional: Add more integrity checks (e.g., hash verification)
            return True
        except Exception as e:
            logger.error(f"File verification failed: {str(e)}")
            return False

    async def get_queue_status(self) -> str:
        """Get formatted queue status message."""
        active_downloads = len(self.downloads_in_progress)
        queued_downloads = self.download_queue.qsize()  # Changed from --> len(self.download_queue)

        status = f"📊 Queue Status:\n"
        status += f"Active downloads: {active_downloads}/{self.concurrent_downloads}\n"
        status += f"Queued downloads: {queued_downloads}/{self.max_queue_size}\n\n"

        if self.downloads_in_progress:
            status += "🔄 Current Downloads:\n"
            for task in self.downloads_in_progress.values():
                speed = humanize.naturalsize(task.download_speed, binary=True) + "/s"
                progress = f"{task.percentage_completed:.2f}%"
                downloaded = f"{task.mb_downloaded:.2f} MB out of {task.mb_total:.2f} MB"
                eta = str(task.eta).split('.')[0] if task.eta else "calculating..."
                status += f"\n- {task.file_name}\n  progress: {progress}\n  Speed: {speed}\n  downloaded: {downloaded}\n  ETA: {eta}\n"

        return status

    async def get_stats(self) -> str:
        """Get formatted statistics message."""
        uptime = datetime.now() - self.stats.start_time
        success_rate = ((self.stats.total_downloads - self.stats.failed_downloads) /
                        max(self.stats.total_downloads, 1) * 100)

        stats = f"📈 Download Statistics:\n"
        stats += f"\nTotal downloads: {self.stats.total_downloads}\n"
        stats += f"Failed downloads: {self.stats.failed_downloads}\n"
        stats += f"\nSuccess rate: {success_rate:.1f}%\n"
        stats += f"\nTotal data: {humanize.naturalsize(self.stats.total_bytes, binary=True)}\n"
        stats += f"\nUptime: {str(uptime).split('.')[0]}"

        return stats

    async def handle_commands(self, event):
        """Handle bot commands."""
        command = event.message.message.lower()

        if command == '/status':
            status = await self.get_queue_status()
            await event.reply(status)

        elif command == '/stats':
            stats = await self.get_stats()
            await event.reply(stats)

        elif command == '/clear_queue':
            # Add authentication check here
            while not self.download_queue.empty():
                try:
                    self.download_queue.get_nowait()
                    self.download_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            await event.reply("✅ Download queue cleared")

        elif command == '/help':
            help_text = (
                "Available commands:\n"
                "/status - Show current download status\n"
                "/stats - Show download statistics\n"
                "/clear_queue - Clear download queue\n"
                "/help - Show this help message"
            )
            await event.reply(help_text)

    async def cleanup_old_files(self):
        """Cleanup temporary and old files periodically."""
        while True:
            try:
                # Cleanup temp files
                for file in Path(CONFIG['DOWNLOAD_PATH']).glob("*.part"):
                    if (datetime.now() - datetime.fromtimestamp(file.stat().st_mtime)) > timedelta(days=1):
                        file.unlink()

                # Cleanup old history backups (keep last 5)
                backup_files = sorted(Path().glob("download_history_backup_*.json"))
                if len(backup_files) > 5:
                    for file in backup_files[:-5]:
                        file.unlink()

            except Exception as e:
                logger.error(f"Cleanup error: {str(e)}")

            await asyncio.sleep(3600)  # Run every hour

    async def start(self):
        """Start the bot with enhanced features."""
        Path(CONFIG['DOWNLOAD_PATH']).mkdir(parents=True, exist_ok=True)

        self.client = TelegramClient('bot_session', CONFIG['TELEGRAM_API_ID'], CONFIG['TELEGRAM_API_HASH'])

        # Start cleanup task
        asyncio.create_task(self.cleanup_old_files())
        for _ in range(CONFIG['CONCURRENT_DOWNLOADS']):
            asyncio.create_task(self.process_download_queue())

        @self.client.on(events.NewMessage)
        async def handle_new_message(event):
            if event.message.message.startswith('/'):
                await self.handle_commands(event)
                return

            if event.message.media:
                try:
                    if self.download_queue.qsize() >= self.max_queue_size:  # Changed from --> if len(self.download_queue) >= self.max_queue_size:
                        await event.reply("⚠️ Queue is full. Please try again later.")
                        return

                    message_id = str(event.message.id)
                    if message_id in self.download_history:
                        await event.reply("This file has already been downloaded.")
                        return

                    task = await self._create_download_task(event)
                    if task:
                        await self.download_queue.put(task)  # changed from --> self.download_queue.append(task)
                        if not self.is_downloading:
                            asyncio.create_task(self.process_download_queue())

                except Exception as e:
                    logger.error(f"Error handling message: {str(e)}")
                    await event.reply(f"❌ Error processing file: {str(e)}")
            else:
                await event.reply("Please send a file to download or use /help to see available commands.")

        try:
            await self.client.start(bot_token=CONFIG['TELEGRAM_BOT_TOKEN'])
            logger.info("Bot is running. Monitoring / Waiting for files...")
            await self.client.run_until_disconnected()
        except Exception as e:
            logger.error(f"Connection error: {str(e)}")
            await asyncio.sleep(5)
            await self.start()

    async def _create_download_task(self, event) -> Optional[DownloadTask]:
        """Create a new download task from an event."""
        try:
            attributes = event.message.media.document.attributes
            file_name = next(
                (attr.file_name for attr in attributes
                 if isinstance(attr, DocumentAttributeFilename)),
                f"unknown_file_{event.message.id}"
            )

            file_size = event.message.media.document.size

            # Check if file size is within limits (e.g., 2GB)
            if file_size > CONFIG['MAX_FILE_SIZE']:
                await event.reply(f"⚠️ File is too large (max {humanize.naturalsize(CONFIG['MAX_FILE_SIZE'])})")
                return None

            queued_message = await event.reply(
                f"📥 Added to queue: {file_name}\n"
                f"Size: {humanize.naturalsize(file_size, binary=True)}"
            )

            return DownloadTask(
                message=event.message,
                file_name=file_name,
                file_size=file_size,
                queued_message=queued_message
            )
        except Exception as e:
            logger.error(f"Error creating download task: {str(e)}")
            await event.reply("❌ Error processing file request")
            return None

    async def _handle_failed_download(self, task: DownloadTask):
        """Handle a failed download attempt."""
        self.stats.failed_downloads += 1
        await task.bot_logger.error(f"Download failed after {task.max_retries} attempts.")

    def clean_filename(self, filename: str, max_length: int = 255) -> str:
        """
        Clean filename with additional features.

        Args:
            filename: The filename to clean
            max_length: Maximum length for filename (default 255 for NTFS)

        Returns:
            Cleaned filename
        """
        try:
            # Remove leading/trailing spaces and periods
            cleaned = filename.strip('. ')

            # Replace invalid characters
            invalid_chars = r'<>:"/\|?*'
            for char in invalid_chars:
                cleaned = cleaned.replace(char, '_')

            # Replace multiple spaces and underscores with single ones
            cleaned = ' '.join(cleaned.split())
            cleaned = '_'.join(filter(None, cleaned.split('_')))

            # Handle Windows reserved names
            reserved_names = {
                'CON', 'PRN', 'AUX', 'NUL',
                'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
                'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
            }

            # If name (without extension) is reserved, prefix with underscore
            name_without_ext = Path(cleaned).stem.upper()
            if name_without_ext in reserved_names:
                cleaned = f"_{cleaned}"

            # Handle maximum length (keeping extension intact)
            if len(cleaned) > max_length:
                p = Path(cleaned)
                ext = p.suffix
                stem = p.stem[:max_length - len(ext)]
                cleaned = f"{stem}{ext}"

            return cleaned

        except Exception as e:
            logger.error(f"Error cleaning filename '{filename}': {str(e)}")
            # Return a safe default name if cleaning fails
            return f"file_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    async def process_successful_download(self, task: DownloadTask, download_path: Path):
        """Process a successfully downloaded file."""
        self.stats.total_downloads += 1
        self.stats.total_bytes += task.file_size
        await task.bot_logger.success(f"Download complete: {task.file_name}")

        # Sanitize the filename
        cleaned_filename = self.clean_filename(task.file_name)

        # # Create the destination folder (for all non-archive, non-MKV files)
        folder_name = Path(cleaned_filename).stem
        destination_folder = Path(CONFIG['MOVE_PATH']) / folder_name

        # try:
        # destination_folder.mkdir(parents=True, exist_ok=True)
        # await task.bot_logger.info(f"Created folder: {destination_folder}")
        # except Exception as folder_error:
        # await task.bot_logger.error(f"Error creating folder {destination_folder}: {folder_error}")
        # return  # Stop processing if folder creation fails

        # Handle MKV files
        if download_path.suffix.lower() == '.mkv':
            cleaned_download_path = download_path.with_name(cleaned_filename)
            folder_name = Path(cleaned_filename).stem
            destination_folder = Path(CONFIG['MOVE_PATH']) / folder_name
            try:
                destination_folder.mkdir(parents=True, exist_ok=True)
                await task.bot_logger.info(f"Created folder: {destination_folder}")
            except Exception as folder_error:
                await task.bot_logger.error(f"Error creating folder {destination_folder}: {folder_error}")
                return  # Stop processing if folder creation fails

            try:
                os.rename(download_path, cleaned_download_path)
                await self.process_mkv_file(task, cleaned_download_path, destination_folder)
                return  # Return after processing MKV
            except Exception as e:
                await task.bot_logger.error(f"Error renaming MKV file: {e}")
                return

        # Handle archive files
        if download_path.suffix.lower() in ('.rar', '.zip', '.tar', '.tgs'):
            await self.handle_archive_file(task, download_path)
            return

        # Move non-MKV and non-archive files
        try:
            move_path = destination_folder / cleaned_filename
            shutil.move(str(download_path), str(move_path))
            await task.bot_logger.success(f"File moved to {move_path}")
        except Exception as e:
            await task.bot_logger.error(f"Error moving file: {e}")

    async def handle_archive_file(self, task: DownloadTask, download_path: Path):
        """Handle archive files (e.g., .rar, .zip) without creating a folder."""
        try:
            cleaned_filename = self.clean_filename(task.file_name)
            destination_path = Path(CONFIG['MOVE_PATH']) / cleaned_filename
            shutil.move(str(download_path), str(destination_path))
            await task.bot_logger.success(f"Archive file moved to {destination_path}")
        except Exception as e:
            await task.bot_logger.error(f"Error moving archive file: {e}")

    async def process_mkv_file(self, task: DownloadTask, file_path: Path, destination_folder: Path):
        """Process an MKV file using mkvpropedit."""
        try:
            if not CONFIG['MKVPROPEDIT_PATH']:
                await task.bot_logger.warning(
                    "MKVPropedit path is not set. Skipping MKV processing on the file downloaded.")
                return

            if not Path(CONFIG['MKVPROPEDIT_PATH']).exists():
                await task.bot_logger.error(
                    "mkvpropedit executable not found. Skipping MKV processing on the downloaded file.")
                return

            # Construct command
            command = [
                str(CONFIG['MKVPROPEDIT_PATH']),
                str(file_path),
                "--edit",
                "info",
                "--set",
                f"title={file_path.stem}"
            ]

            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                await task.bot_logger.error(
                    f"MKV metadata update failed for {file_path.name}. Error: {stderr.decode()}")
                return

            # Move the file to the destination folder using shutil.move
            cleaned_filename = self.clean_filename(task.file_name)
            move_path = destination_folder / cleaned_filename

            try:
                shutil.move(str(file_path), str(move_path))
                await task.bot_logger.success(f"MKV metadata updated and file moved to {move_path}")
            except Exception as move_error:
                await task.bot_logger.error(f"Error moving MKV file: {move_error}")

        except Exception as e:
            await task.bot_logger.error(f"Error processing MKV file: {str(e)}")


async def main():
    if not all([CONFIG['TELEGRAM_API_ID'], CONFIG['TELEGRAM_API_HASH'], CONFIG['TELEGRAM_BOT_TOKEN']]):
        logger.error("Missing required environment variables. Please check your .env file.")
        return

    while True:
        try:
            downloader = TelegramDownloader()
            await downloader.start()
        except Exception as e:
            logger.error(f"Bot crashed: {str(e)}")
            await asyncio.sleep(5)  # Wait before restarting


if __name__ == "__main__":
    asyncio.run(main())