# Dispatch Map

Live dispatch operations now follow the root server guide:
[SERVER_STACK.md](/C:/Users/niceh/OneDrive/Desktop/dispatch/SERVER_STACK.md)

## Active runtime

- Public Streamlit: `127.0.0.1:8501`
- Dispatch API bridge: `http://127.0.0.1:8010/api/dispatch`
- Public share URLs: `https://sales.nasilfamily.com/api/dispatch/share/...`

## Important notes

- `dispatch.nasilfamily.com` must point to `8501`, not `8514`.
- Public dispatch should never use `8000` as its API base.
- The public and LAN launch scripts both read the shared settings file at the workspace root.
- Old local Django drafts were moved under `BACKUP` names so they do not look like live code.

## Telegram setup

`dispatch-map/app.py` loads `dispatch-map/.env` on startup and also respects existing process environment variables.

1. Create `dispatch-map/.env` if it does not exist.
2. Add the Telegram values:

```env
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

3. Restart the Streamlit app after changing `.env`.
4. If either Telegram variable is missing, dispatch skips Telegram sending and only writes a log entry instead of failing the assignment flow.
