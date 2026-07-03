"""Probe Skool cookies from .env to confirm we can still authenticate."""
from pathlib import Path
import re

ENV_PATH = Path(__file__).resolve().parents[1] / '.env'
env = {}
for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
    m = re.match(r'^\s*([A-Za-z0-9_.-]+)\s*=\s*"?([^"]*)"?\s*$', line)
    if m:
        env[m.group(1)] = m.group(2)

cookie_blob = env.get('SKOOL_AUTH_COOKIE') or ''
print('cookie_blob_len', len(cookie_blob))

cookies = []
for raw in cookie_blob.split(';'):
    raw = raw.strip()
    if not raw or '=' not in raw:
        continue
    name, value = raw.split('=', 1)
    cookies.append({'name': name.strip(), 'value': value.strip(), 'domain': '.skool.com', 'path': '/'})
print('cookie_count', len(cookies))
print('cookie_names', sorted(c['name'] for c in cookies))

ua = env.get('SKOOL_USER_AGENT') or ''
print('has_ua', bool(ua))
