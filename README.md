# Telegram File Downloader Bot

A Telegram bot that downloads files send to the bot as messages into windows PC.

It tracks progress, and manages additional file processing.

The bot uses asynchronous processing to handle multiple downloads concurrently and includes retry logic, file integrity checks, and periodic cleanup.

---
![Blue_Telegram_Bot](https://github.com/user-attachments/assets/834fee72-8b15-42f3-9f44-854f394082c7)

## Features
 
  - Download files sent via Telegram messages.
  - Process special file types (remove metadata from MKV files).
  - Move the File to prescribed folders after downloads are completed.
  - Track and log download progress, retries, and overall statistics.
---

## External Dependencies

  - [**aiofiles**](https://github.com/Tinche/aiofiles) - Asynchronous file operations.
  - [**humanize**](https://github.com/jmoiron/humanize) - Human-friendly formatting for byte sizes.
  - [**Telethon**](https://github.com/LonamiWebs/Telethon) - Telegram client library for Python.
  - [**python-dotenv**](https://github.com/theskumar/python-dotenv) - Load environment variables from a `.env` file.

---

## Installation

1. **Clone the Repository:**

   git clone https://github.com/vincengomes/telegramdownloader.git
   
   cd telegramdownloader

3. **Create and Activate a Virtual Environment**

    Create Virtual Environment with
   ```
    python -m venv venv
   ```
   Then activate the Virtual Environment with
   ```
    venv\Scripts\activate
   ```


5. **Install Required Packages**

    ```
    pip install aiofiles humanize telethon python-dotenv
    ```


6. **Configuration:**

     - Create a New Bot in Telegram through Botfather. 
      
     - Get the Bot API ID, HASH and BOT Token for the New Bot that was created
    
     - Create a .env File in the Project Root:

    Example .env content:

      ```
      TELEGRAM_API_ID=your_api_id_here
   
      TELEGRAM_API_HASH=your_api_hash_here
   
      TELEGRAM_BOT_TOKEN=your_bot_token_here
   
      DOWNLOAD_PATH=/absolute/path/to/download/folder
   
      MOVE_PATH=/absolute/path/to/move/folder
   
      MKVPROPEDIT_PATH=/absolute/path/to/mkvpropedit_executable
      ```


8. **Usage:**

      Run the bot using the command: 
      ```
      python telegramdownloader.py
      ```
    
    
## Bot Commands:
    
        /status – View current download queue status.
        /stats – View overall download statistics.
        /clear_queue – Clear the download queue.
        /help – Display available commands


