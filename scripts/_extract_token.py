from pathlib import Path
import sys
for raw in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    line = raw.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key, _, value = line.partition('=')
    if key.strip() == 'ENGINE_WEBHOOK_TOKEN':
        print(value.strip().strip(chr(34)).strip(chr(39)))
        break
