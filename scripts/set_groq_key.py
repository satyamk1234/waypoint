"""Interactive, hidden key entry. Never accepts a key as a command-line argument."""
import getpass
import os
from pathlib import Path

root=Path(__file__).resolve().parent.parent
path=root/'.env'
if path.is_symlink(): raise SystemExit('Refusing a symlink .env')
key=getpass.getpass('Groq API key (hidden): ').strip()
if not key.startswith('gsk_') or any(c.isspace() for c in key):
    raise SystemExit('Expected a Groq key starting with gsk_; no file was changed.')
lines=path.read_text().splitlines() if path.exists() else (root/'.env.example').read_text().splitlines()
lines=[line for line in lines if not line.strip().startswith('GROQ_API_KEY=')]
lines.append('GROQ_API_KEY='+key)
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as file: file.write('\n'.join(lines)+'\n')
os.chmod(path,0o600)
print('Saved GROQ_API_KEY to .env. Restart the application to enable chat.')
