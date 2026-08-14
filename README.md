# Telegram Message Purge Utility (TMPU)

A command-line tool that uses the Telegram **User API** (MTProto) to search for and delete your messages based on keywords, date ranges, and target chats.

**Safety first:** dry-run is the default. Search asks for `y`/`yes` before querying Telegram. Actual deletion requires `--force` and typing `DELETE` at a separate confirmation prompt.

## Requirements

- Python 3.10+
- A Telegram account
- API credentials from Telegram (see below)

## Getting `API_ID` and `API_HASH`

These credentials identify your application to Telegram. They are **not** your login password — they let the tool connect via the User API.

1. Go to [https://my.telegram.org](https://my.telegram.org) and sign in with your phone number.
2. Open **API development tools**.
3. If prompted, fill in the form:
   - **App title** — any name (e.g. `TMPU`)
   - **Short name** — a short identifier (e.g. `tmpu`)
   - **Platform** — e.g. Desktop
   - **Description** — optional
4. Click **Create application**.
5. Copy the values shown on the page:
   - **App api_id** → use as `API_ID` (a number)
   - **App api_hash** → use as `API_HASH` (a long hex string)

Keep `API_HASH` secret. Do not commit it to git or share it publicly.

## Setup

```bash
git clone <repo-url>
cd telegram

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
```

Edit `.env` with your credentials:

```env
API_ID=12345678
API_HASH=your_api_hash_here
PHONE_NUMBER=+1234567890
```

Use your full international phone number for `PHONE_NUMBER` (include country code, e.g. `+1...`).

### First run (authentication)

On the first run, Telethon will prompt for:

1. The login code sent to your Telegram app or SMS
2. Your 2FA password, if enabled

A local session file (`tmpu.session`) is created so later runs do not require codes again. This file is gitignored — do not share it.

## Usage

```bash
python main.py --chats <chats> [options]
```

By default only **private 1:1 chats** are searched. Channels and group chats are skipped unless you pass `--channels` and/or `--group-chats`.

Between Telegram API calls the tool waits **1 second** by default (`--wait-seconds`). Pass `--wait-seconds 0` to disable proactive pauses.

### Arguments

| Flag | Required | Description |
|------|----------|-------------|
| `--chats` | Yes | Comma-separated `@username`, numeric ID, partial dialog title, or `all` |
| `--keywords` | No | Comma-separated strings; partial/stem match (e.g. `hello` matches `Hello`) |
| `--after` | No | Only messages on or after this date (`YYYY-MM-DD`); also skips chats whose last message is before that date |
| `--before` | No | Messages on or before this date (`YYYY-MM-DD`, inclusive) |
| `--everyone` | No | Include messages from all users (requires admin to delete others') |
| `--from` | No | Only messages from this sender (`@username`, ID, or partial display name) |
| `--channels` | No | Include broadcast channels in the search |
| `--group-chats` | No | Include groups and megagroups in the search |
| `--dry-run` | No | Preview matches without deleting (default) |
| `--force` | No | Actually delete (requires typing `DELETE`) |
| `--no-confirmation` | No | Skip the search `y`/`yes` prompt (does **not** skip the `--force` `DELETE` prompt) |
| `--wait-seconds` | No | Seconds to pause between Telegram API calls (default `1`). Use `0` to disable proactive pauses |

### Examples

Preview messages containing "spam" in one chat:

```bash
python main.py --chats @mychannel --keywords spam
```

If the chat is known by its display name (not `@username`), use the exact title or a unique part of it:

```bash
python main.py --chats "Team Chat" --keywords hello,hi
python main.py --chats team --keywords hello
```

By default only **your** messages are matched. To include messages from other users:

```bash
python main.py --chats "Team Chat" --keywords hello --everyone
```

To filter by a specific sender:

```bash
python main.py --chats "Team Chat" --keywords hello --from @alice
python main.py --chats all --keywords hello --from 123456789
```

Previews show `sender=` for each match (`me`, `@username`, or numeric ID).

Search all dialogs for old messages:

```bash
python main.py --chats all --before 2024-01-01
```

Include channels and group chats when searching:

```bash
python main.py --chats all --keywords spam --channels --group-chats
```

Delete matching messages (requires typing `DELETE`):

```bash
python main.py --chats @mychannel --keywords test --force
```

## Safety

- **Dry-run is default** — without `--force`, nothing is deleted.
- **Search asks first** — before any Telegram search, you must confirm with `y` or `yes` (shows candidate chat count and expected search count).
- **`--no-confirmation`** — skips only the search `y`/`yes` prompt; the `--force` `DELETE` prompt still applies.
- **`--force` requires confirmation** — you must type `DELETE` exactly in an interactive terminal.
- **Non-interactive runs abort** — piping or CI without a TTY will not search (unless `--no-confirmation`) or delete.
- **Deletes for everyone** — removed messages disappear for all participants where Telegram allows it.

Always run a dry-run first and review the preview output.

## Development

```bash
.venv/bin/pytest tests/ -v
```

## Project layout

```
├── main.py              # Entry point
├── src/
│   ├── config.py        # .env loading
│   ├── cli.py           # Argument parsing
│   ├── telegram_client.py
│   └── utils.py
├── tests/
├── .env.example
└── requirements.txt
```
