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
