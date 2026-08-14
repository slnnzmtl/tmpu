# Technical Specification: Telegram Message Purge Utility (TMPU)
**Target Year:** 2026

## 1. Project Overview
**Name:** Telegram Message Purge Utility (TMPU)
**Objective:** Develop a lightweight, configurable Command Line Interface (CLI) application that authenticates via the Telegram User API (MTProto) to search for and delete specific messages based on user-defined parameters (keywords, date ranges, and specific chats).
**Design Philosophy:** Safety first (destructive actions require confirmation/dry-runs), modularity, and strict adherence to Telegram's 2026 API rate limits and Session v2 architectures.

## 2. Technical Stack
* **Language:** Python 3.10+
* **Core Library:** `Telethon >= 1.44.0` (Mandatory for Session-string v2 compliance)
* **CLI Framework:** `argparse` (Standard library)
* **Configuration:** `.env` file via `python-dotenv`

## 3. File Layout
```text
tmpu/
├── .env.example
├── requirements.txt
├── main.py
└── src/
    ├── __init__.py
    ├── config.py
    ├── cli.py
    ├── telegram_client.py
    └── utils.py
```

## 4. Functional Requirements
### 4.1 Authentication
* Authenticate as a regular user (User API, not Bot API) using `API_ID` and `API_HASH`.
* Maintain a local `.session` file (v2 format) to prevent requiring 2FA/SMS codes on subsequent runs.

### 4.2 Search & Filter Capabilities
The utility must retrieve messages matching the following parameters:
* **Target Chats:** Search within specific chats (by @username or ID) or across all accessible dialogs. 
    * *2026 Constraint:* `messages.SearchGlobal` is reserved for Premium users. If searching all chats, the script must loop through `client.iter_dialogs()` and execute `client.iter_messages()` on each chat individually.
* **Keywords:** Exact string matching within the message text.
* **Date Range:** Messages sent before (`max_date`) or after (`min_date`) a specific timestamp.
* **Sender:** Default behavior targets ONLY messages sent by the authenticated user (`me`). An explicit override flag is required to target other users (requires admin rights).

### 4.3 Deletion Logic
* **Batched Deletion:** Collect message IDs and delete them in batches (up to 100 per request per chat) to minimize API calls.
* **Dry Run Mode:** Must default to a `--dry-run` state where it only logs the messages that *would* be deleted, unless `--force` is passed.

## 5. CLI Interface & Arguments
Located in `src/cli.py` and executed via `main.py`.

```text
--chats       [Required] Comma-separated list of chat IDs/usernames, or "all".
--keywords    [Optional] Comma-separated strings to search for.
--after       [Optional] Date in YYYY-MM-DD format.
--before      [Optional] Date in YYYY-MM-DD format.
--everyone    [Optional] Flag to delete messages from all users (requires admin). Defaults to 'me' only.
--dry-run     [Optional] Flag to simulate deletion and output targets. (Default behavior).
--force       [Optional] Flag to execute actual deletion.
```

## 6. Architectural Components

### src/config.py
* Handles environment variable loading via `dotenv`.
* Validates the presence of `API_ID`, `API_HASH`, and `PHONE_NUMBER`.

### src/cli.py
* Parses standard input arguments.
* Validates date formats and chat lists before passing them to the main controller.

### src/telegram_client.py
* Initializes the `TelegramClient`.
* Contains `fetch_target_messages()`:
    * Uses `client.iter_messages(chat, search=keyword, offset_date=before)`.
    * Implements the dialog-looping fallback for `--chats all`.
* Contains `delete_messages_batch()`:
    * Passes collected IDs to `client.delete_messages()`.

### src/utils.py
* **Logging:** Setup console output with `INFO`, `WARNING`, and `ERROR` levels. Must catch API deprecation notices (often output as warnings in 2026 API responses).
* **Rate-limit Handler:** A unified decorator or context manager for handling exceptions.

## 7. Error Handling & 2026 Guardrails
* **Granular Flood Headers:** Catch `telethon.errors.FloodWaitError` along with context-specific 2026 variants (e.g., `FloodPeerWaitError`). Read the required wait time, log a warning, automatically `asyncio.sleep(seconds)`, and resume.
* **Access Errors:** Catch `ChatAdminRequiredError` if the user attempts to delete others' messages without permissions. Log and skip to the next chat.
* **Revoked Chats:** Handle `ChannelPrivateError` gracefully if the user no longer has access to a target chat.

## 8. Developer Implementation Steps
1. **Environment Setup:** Define `requirements.txt` with `telethon>=1.44.0` and `python-dotenv`.
2. **Authentication:** Build `telegram_client.py` initialization to ensure local `.session` generation.
3. **Search Logic:** Write the asynchronous generator for fetching messages, ensuring the `--chats all` fallback is properly implemented to avoid Premium-only API restrictions.
4. **Deletion Logic:** Wire up batched deletions.
5. **CLI Wiring:** Connect `src/cli.py` arguments to the client methods in `main.py`.
6. **Safety & Limits:** Implement the `--dry-run` default and the granular rate-limit exception handlers in `src/utils.py`.